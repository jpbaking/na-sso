from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import ceil
import re
from typing import Mapping
from urllib.parse import urlencode

from sqlalchemy import func, or_, select

from na_sso.models import AuditEvent, LifecycleOperation, OperationTargetAttempt


OUTCOMES = frozenset({"all", "succeeded", "failed", "in_progress", "uncorrelated"})
PAGE_SIZES = frozenset({25, 50, 100})
REDACTED = "[redacted]"
_SENSITIVE_DETAIL = re.compile(
    r"(?i)(password|passphrase|api[_ -]?secret|private[_ -]?key|authorization|bearer|token)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def _date(value: str, *, end: bool = False) -> datetime | None:
    try:
        result = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return result + timedelta(days=1) if end else result


@dataclass(frozen=True)
class AuditParams:
    date_from: str = ""
    date_to: str = ""
    actor: str = ""
    subject: str = ""
    target: str = ""
    action: str = ""
    operation: str = ""
    outcome: str = "all"
    page: int = 1
    per_page: int = 50

    @classmethod
    def parse(cls, values: Mapping[str, str]) -> "AuditParams":
        def positive(name: str, default: int) -> int:
            try:
                return max(1, int(values.get(name, str(default))))
            except (TypeError, ValueError):
                return default

        per_page = positive("per_page", 50)
        if per_page not in PAGE_SIZES:
            per_page = 100 if per_page > 100 else 50
        outcome = values.get("outcome", "all")
        return cls(
            date_from=values.get("date_from", "") if _date(values.get("date_from", "")) else "",
            date_to=values.get("date_to", "") if _date(values.get("date_to", ""), end=True) else "",
            actor=values.get("actor", "").strip()[:64],
            subject=values.get("subject", "").strip()[:128],
            target=values.get("target", "").strip()[:64],
            action=values.get("action", "").strip()[:64],
            operation=values.get("operation", "").strip()[:36],
            outcome=outcome if outcome in OUTCOMES else "all",
            page=min(positive("page", 1), 1_000_000),
            per_page=per_page,
        )

    def url(self, *, page: int | None = None, **changes) -> str:
        value = replace(self, page=page if page is not None else self.page, **changes)
        return "/audit?" + urlencode({
            "date_from": value.date_from, "date_to": value.date_to,
            "actor": value.actor, "subject": value.subject, "target": value.target,
            "action": value.action, "operation": value.operation,
            "outcome": value.outcome, "page": value.page, "per_page": value.per_page,
        })

    def export_url(self, format: str, *, page: int | None = None) -> str:
        value = replace(self, page=page if page is not None else self.page)
        return f"/audit/export.{format}?" + urlencode({
            "date_from": value.date_from, "date_to": value.date_to,
            "actor": value.actor, "subject": value.subject, "target": value.target,
            "action": value.action, "operation": value.operation,
            "outcome": value.outcome, "page": value.page,
        })


@dataclass(frozen=True)
class AuditItem:
    event: AuditEvent
    operation: LifecycleOperation | None
    summary: str
    outcome: str


@dataclass(frozen=True)
class AuditPage:
    items: list[AuditItem]
    params: AuditParams
    total: int
    pages: int

    @property
    def has_previous(self) -> bool: return self.params.page > 1

    @property
    def has_next(self) -> bool: return self.params.page < self.pages


FRIENDLY_ACTIONS = {
    "user.create": "Created managed account",
    "user.update": "Updated managed account",
    "user.delete_requested": "Requested account deletion",
    "user.restore": "Restored managed account",
    "user.purge": "Purged local account record",
    "password.changed": "Changed account password",
    "password.expired": "Password expired",
    "password.keep_acknowledged": "Acknowledged expired password",
    "target.credentials_saved": "Saved target credentials",
    "target.probed": "Tested target connection",
    "role.assigned": "Assigned scoped role",
    "audit.exported": "Exported audit events",
    "mfa.login": "Verified administrator sign-in",
    "mfa.totp.enrolled": "Enrolled authenticator app",
    "mfa.totp.revoked": "Revoked authenticator app",
    "mfa.webauthn.enrolled": "Enrolled passkey",
    "mfa.webauthn.revoked": "Revoked passkey",
    "mfa.recovery.regenerated": "Replaced recovery codes",
    "webhook.delivered": "Delivered webhook notification",
    "webhook.failed": "Webhook delivery exhausted retries",
    "webhook.enabled": "Enabled webhook destination",
    "webhook.disabled": "Disabled webhook destination",
    "webhook.retry_requested": "Requested webhook retry",
}


def friendly_action(action: str) -> str:
    return FRIENDLY_ACTIONS.get(action, action.replace(".", " ").replace("_", " ").capitalize())


def operation_outcome(operation: LifecycleOperation | None) -> str:
    if operation is None:
        return "uncorrelated"
    if operation.status == "succeeded":
        return "succeeded"
    if operation.status in {"failed", "partially_failed", "blocked", "cancelled"}:
        return "failed"
    return "in_progress"


def safe_detail(detail: str) -> str:
    """Defensively redact common credential shapes from operator-facing exports."""
    detail = _PEM_PRIVATE_KEY.sub(REDACTED, detail)
    return _SENSITIVE_DETAIL.sub(lambda match: f"{match.group(1)}={REDACTED}", detail)


def _filtered_query(db, params: AuditParams):
    query = db.query(AuditEvent)
    if params.date_from:
        query = query.filter(AuditEvent.at >= _date(params.date_from))
    if params.date_to:
        query = query.filter(AuditEvent.at < _date(params.date_to, end=True))
    if params.actor:
        query = query.filter(func.lower(AuditEvent.actor).like(f"%{params.actor.lower()}%"))
    if params.subject:
        query = query.filter(func.lower(AuditEvent.subject).like(f"%{params.subject.lower()}%"))
    if params.action:
        query = query.filter(func.lower(AuditEvent.action).like(f"%{params.action.lower()}%"))
    if params.operation:
        query = query.filter(AuditEvent.operation_id.like(f"{params.operation}%"))
    if params.target:
        target_operations = select(OperationTargetAttempt.operation_id).where(
            OperationTargetAttempt.target == params.target
        )
        query = query.filter(or_(
            AuditEvent.detail.like(f"%target={params.target}%"),
            AuditEvent.operation_id.in_(target_operations),
        ))
    if params.outcome == "uncorrelated":
        query = query.filter(AuditEvent.operation_id.is_(None))
    elif params.outcome != "all":
        statuses = {
            "succeeded": ("succeeded",),
            "failed": ("failed", "partially_failed", "blocked", "cancelled"),
            "in_progress": ("queued", "running"),
        }[params.outcome]
        operations = select(LifecycleOperation.id).where(LifecycleOperation.status.in_(statuses))
        query = query.filter(AuditEvent.operation_id.in_(operations))
    return query


def query_audit(db, params: AuditParams, *, page_size: int | None = None) -> AuditPage:
    query = _filtered_query(db, params)

    total = query.count()
    per_page = page_size or params.per_page
    pages = max(1, ceil(total / per_page))
    params = replace(params, page=min(params.page, pages), per_page=per_page)
    events = query.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).offset(
        (params.page - 1) * params.per_page
    ).limit(params.per_page).all()
    operation_ids = {event.operation_id for event in events if event.operation_id}
    operations = {
        operation.id: operation for operation in db.query(LifecycleOperation).filter(
            LifecycleOperation.id.in_(operation_ids)
        ).all()
    } if operation_ids else {}
    return AuditPage(
        items=[AuditItem(
            event=event,
            operation=operations.get(event.operation_id),
            summary=friendly_action(event.action),
            outcome=operation_outcome(operations.get(event.operation_id)),
        ) for event in events],
        params=params, total=total, pages=pages,
    )
