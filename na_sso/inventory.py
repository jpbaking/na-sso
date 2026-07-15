from dataclasses import dataclass, replace
from math import ceil
from typing import Mapping
from urllib.parse import urlencode

from sqlalchemy import and_, case, exists, func, not_, or_, select
from sqlalchemy.orm import selectinload

from na_sso.models import ManagedUser, SyncState


LIFECYCLE_FILTERS = frozenset({
    "all", "active", "chpw", "expired", "disabled", "deleting", "deleted", "protected"
})
ISSUE_FILTERS = frozenset({"all", "attention", "retrying", "none"})
SORT_FIELDS = frozenset({"username", "name", "lifecycle", "coverage", "issues", "password"})
PAGE_SIZES = frozenset({25, 50, 100})
ISSUE_STATES = frozenset({
    "failed", "awaiting_credentials", "pending_expiry_disable", "expired_disabled", "retired"
})


@dataclass(frozen=True)
class InventoryParams:
    search: str = ""
    lifecycle: str = "all"
    target: str = ""
    issues: str = "all"
    sort: str = "username"
    direction: str = "asc"
    page: int = 1
    per_page: int = 25

    @classmethod
    def parse(cls, values: Mapping[str, str]) -> "InventoryParams":
        def positive_int(name: str, default: int) -> int:
            try:
                return max(1, int(values.get(name, str(default))))
            except (TypeError, ValueError):
                return default

        per_page = positive_int("per_page", 25)
        if per_page not in PAGE_SIZES:
            per_page = 100 if per_page > 100 else 25
        lifecycle = values.get("lifecycle", "all")
        issues = values.get("issues", "all")
        sort = values.get("sort", "username")
        direction = values.get("direction", "asc")
        return cls(
            search=values.get("q", "").strip()[:100],
            lifecycle=lifecycle if lifecycle in LIFECYCLE_FILTERS else "all",
            target=values.get("target", "").strip()[:64],
            issues=issues if issues in ISSUE_FILTERS else "all",
            sort=sort if sort in SORT_FIELDS else "username",
            direction=direction if direction in {"asc", "desc"} else "asc",
            page=min(positive_int("page", 1), 1_000_000),
            per_page=per_page,
        )

    def url(self, *, page: int | None = None, **changes) -> str:
        current = replace(self, page=page if page is not None else self.page, **changes)
        return "/users?" + urlencode({
            "q": current.search,
            "lifecycle": current.lifecycle,
            "target": current.target,
            "issues": current.issues,
            "sort": current.sort,
            "direction": current.direction,
            "page": current.page,
            "per_page": current.per_page,
        })


@dataclass(frozen=True)
class InventorySummary:
    lifecycle: str
    healthy_targets: int
    assigned_targets: int
    issue_count: int


@dataclass(frozen=True)
class InventoryItem:
    user: ManagedUser
    summary: InventorySummary


@dataclass(frozen=True)
class InventoryPage:
    items: list[InventoryItem]
    params: InventoryParams
    total: int
    pages: int

    @property
    def has_previous(self) -> bool:
        return self.params.page > 1

    @property
    def has_next(self) -> bool:
        return self.params.page < self.pages


def lifecycle_value(user: ManagedUser) -> str:
    if user.is_root:
        return "protected"
    if user.desired_action == "delete":
        return "deleted" if user.deleted_at else "deleting"
    if user.status == "disabled":
        return "disabled"
    if user.password_decision_kind in {"initial", "reset"}:
        return "chpw"
    if user.password_decision_kind == "expired":
        return "expired"
    return "active"


def summarise_user(user: ManagedUser) -> InventorySummary:
    assigned = [state for state in user.sync_states if state.assigned and not state.retired]
    issue_count = sum(
        state.retired or (state.assigned and state.state in ISSUE_STATES)
        for state in user.sync_states
    )
    return InventorySummary(
        lifecycle=lifecycle_value(user),
        healthy_targets=sum(state.state == "ok" for state in assigned),
        assigned_targets=len(assigned),
        issue_count=issue_count,
    )


def query_inventory(db, params: InventoryParams) -> InventoryPage:
    lifecycle = case(
        (ManagedUser.role == "root", "protected"),
        (and_(ManagedUser.desired_action == "delete", ManagedUser.deleted_at.is_not(None)), "deleted"),
        (ManagedUser.desired_action == "delete", "deleting"),
        (ManagedUser.status == "disabled", "disabled"),
        (ManagedUser.password_decision_kind.in_(("initial", "reset")), "chpw"),
        (ManagedUser.password_decision_kind == "expired", "expired"),
        else_="active",
    )
    healthy_count = select(func.count(SyncState.id)).where(
        SyncState.user_id == ManagedUser.id,
        SyncState.assigned.is_(True),
        SyncState.retired.is_(False),
        SyncState.state == "ok",
    ).correlate(ManagedUser).scalar_subquery()
    issue_condition = or_(
        SyncState.retired.is_(True),
        and_(SyncState.assigned.is_(True), SyncState.state.in_(ISSUE_STATES)),
    )
    issue_count = select(func.count(SyncState.id)).where(
        SyncState.user_id == ManagedUser.id,
        issue_condition,
    ).correlate(ManagedUser).scalar_subquery()
    has_issue = exists().where(SyncState.user_id == ManagedUser.id, issue_condition)
    has_retry = exists().where(
        SyncState.user_id == ManagedUser.id,
        SyncState.assigned.is_(True),
        SyncState.state == "failed",
        SyncState.next_retry_at.is_not(None),
    )

    query = db.query(ManagedUser)
    if params.search:
        term = f"%{params.search.lower()}%"
        query = query.filter(or_(
            func.lower(ManagedUser.username).like(term),
            func.lower(ManagedUser.display_name).like(term),
            func.lower(ManagedUser.email).like(term),
        ))
    if params.lifecycle != "all":
        query = query.filter(lifecycle == params.lifecycle)
    if params.target:
        query = query.filter(exists().where(
            SyncState.user_id == ManagedUser.id,
            SyncState.target == params.target,
            SyncState.assigned.is_(True),
            SyncState.retired.is_(False),
        ))
    if params.issues == "attention":
        query = query.filter(has_issue)
    elif params.issues == "retrying":
        query = query.filter(has_retry)
    elif params.issues == "none":
        query = query.filter(not_(has_issue))

    total = query.count()
    pages = max(1, ceil(total / params.per_page))
    effective_page = min(params.page, pages)
    params = replace(params, page=effective_page)
    sort_expressions = {
        "username": func.lower(ManagedUser.username),
        "name": func.lower(ManagedUser.display_name),
        "lifecycle": lifecycle,
        "coverage": healthy_count,
        "issues": issue_count,
        "password": func.coalesce(ManagedUser.password_keep_until, ManagedUser.password_changed_at),
    }
    ordering = sort_expressions[params.sort]
    ordering = ordering.desc() if params.direction == "desc" else ordering.asc()
    users = query.options(selectinload(ManagedUser.sync_states)).order_by(
        ordering, func.lower(ManagedUser.username).asc(), ManagedUser.id.asc()
    ).offset((effective_page - 1) * params.per_page).limit(params.per_page).all()
    return InventoryPage(
        items=[InventoryItem(user=user, summary=summarise_user(user)) for user in users],
        params=params,
        total=total,
        pages=pages,
    )
