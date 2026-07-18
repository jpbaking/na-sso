import re
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import current_user, permission_guard
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import LifecycleOperation, ManagedUser, PasswordHistory, SyncState, utcnow
from na_sso.lifecycle import LifecycleCommand, OperationStatus, sync_state_payload
from na_sso.inventory import InventoryParams, query_inventory, summarise_user
from na_sso.operations import (
    OperationConflict,
    finish_operation,
    get_latest_operation,
    operation_payload,
    request_operation,
)
from na_sso.permissions import (
    ASSIGNABLE_ROLES,
    MANAGE_SECURITY,
    MANAGE_USERS,
    default_home,
    has_permission,
    permission_context,
    role_definition,
)
from na_sso.notifications import enqueue_notification
from na_sso.security import encrypt_secret, hash_password, validate_password
from na_sso.sync import sync_user
from na_sso.connectors import get_connectors, validate_for_targets

router = APIRouter()
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?$")
BULK_ACTIONS = frozenset({"assign", "unassign", "disable", "retry"})


def _guard(request: Request) -> str | Response:
    principal = permission_guard(request, MANAGE_USERS)
    if isinstance(principal, Response):
        return principal
    return principal["username"]


def _can_manage_subject(request: Request, user: ManagedUser) -> bool:
    principal = current_user(request)
    return bool(
        principal
        and (
            has_permission(principal["role"], MANAGE_SECURITY)
            or user.role == "user"
        )
        and not user.is_root
    )


def _render(request: Request, name: str, ctx: dict, **kw):
    from na_sso.main import templates

    ctx.setdefault("targets", _targets_context())
    ctx.setdefault("password_policy", get_settings().file.password_policy)
    ctx.setdefault("admin_area", True)
    ctx.setdefault("form_values", {})
    principal = current_user(request)
    role = principal["role"] if principal else "user"
    ctx.setdefault("permissions", permission_context(role))
    ctx.setdefault("assignable_roles", ASSIGNABLE_ROLES)
    ctx.setdefault("role_definition", role_definition)
    return template_response(templates, request, name, ctx, **kw)


def _user_form_values(
    *,
    username: str = "",
    display_name: str = "",
    email: str = "",
    target_ids: list[str] | set[str] = (),
    role: str = "user",
) -> dict:
    return {
        "username": username,
        "display_name": display_name,
        "email": email,
        "target_ids": list(target_ids),
        "role": role,
    }


def _targets_context() -> list:
    return get_connectors()


def _sync_views(users: list[ManagedUser], targets: list) -> dict[int, dict[str, dict]]:
    views: dict[int, dict[str, dict]] = {}
    for user in users:
        states = {state.target: state for state in user.sync_states}
        views[user.id] = {}
        for target in targets:
            state = states.get(target.target_id)
            views[user.id][target.target_id] = sync_state_payload(
                state.state if state else None,
                assigned=state.assigned if state else False,
                retired=state.retired if state else False,
                desired_action=user.desired_action,
                detail=state.detail if state else "",
                attempt_count=state.attempt_count if state else 0,
                next_retry_at=state.next_retry_at if state else None,
                operation_id=state.operation_id if state else None,
            )
    return views


def _set_pending(db, user: ManagedUser, password: str | None, target_ids: set[str] | None = None,
                 *, require_password_change: bool = False,
                 remote_accounts_absent: bool = False,
                 update_assignment_exceptions: bool = True,
                 assignment_actor: str = "lifecycle") -> None:
    """Record a new pending credential and reset all target sync states."""
    if password is not None and not require_password_change:
        user.pending_secret = encrypt_secret(password)
    elif require_password_change:
        user.pending_secret = None
    existing = {s.target: s for s in user.sync_states}
    connectors = {item.target_id: item for item in __import__("na_sso.connectors", fromlist=["get_connectors"]).get_connectors()}
    legacy_mode = not get_settings().config_file
    selected = set(connectors) if target_ids is None and (user.sync_states or legacy_mode) else (target_ids or set())
    if update_assignment_exceptions:
        from na_sso.assignments import record_selected_target_exceptions
        record_selected_target_exceptions(db, user, selected, actor=assignment_actor)
    if user.is_root and selected:
        raise ValueError("root account cannot have target assignments")
    for state in user.sync_states:
        if state.target not in selected and state.assigned:
            state.assigned = False
            state.state = "pending_disable"
            state.next_retry_at = None
    for target in selected:
        state = existing.get(target)
        if state is None:
            connector = connectors[target]
            state = SyncState(user=user, target=target, target_type=connector.target_type)
            db.add(state)
        newly_assigned = not state.assigned
        previous_state = state.state
        state.assigned = True
        state.retired = False
        needs_password = connectors[target].capabilities.password
        if require_password_change:
            state.state = (
                "pending_chpw_disable"
                if not remote_accounts_absent
                and not newly_assigned
                and previous_state == "ok"
                else "chpw"
            )
            state.detail = "password change required before propagation"
        else:
            state.state = "awaiting_credentials" if newly_assigned and needs_password and password is None else "pending"
            state.detail = ""
        state.attempt_count = 0
        state.next_retry_at = None


@router.get("/")
async def index(request: Request):
    account = current_user(request)
    return RedirectResponse(
        default_home(account["role"]) if account else "/login", status_code=303
    )


@router.get("/users")
async def list_users(request: Request):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    params = InventoryParams.parse(request.query_params)
    with get_session() as db:
        inventory = query_inventory(db, params)
        users = [item.user for item in inventory.items]
        operation_views = {
            u.id: operation_payload(
                get_latest_operation(
                    db,
                    u,
                    LifecycleCommand.DELETE if u.desired_action == "delete" else None,
                ),
                u.sync_states,
            )
            for u in users
        }
    targets = _targets_context()
    return _render(
        request,
        "users.html",
        {
            "users": users,
            "admin": admin,
            "targets": targets,
            "sync_views": _sync_views(users, targets),
            "operation_views": operation_views,
            "inventory": inventory,
        },
    )


@router.get("/users/new")
async def new_user_page(request: Request):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    return _render(
        request,
        "user_form.html",
        {"user": None, "admin": admin, "suggested": "", "error": None,
         "targets": _targets_context(), "password_policy": get_settings().file.password_policy},
    )


@router.post("/users/new")
async def create_user(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    confirm_password: str | None = Form(None),
    password_generated: str = Form("false"),
    credential_handoff_confirmed: str = Form("false"),
    role: str = Form("user"),
    target_ids: list[str] = Form(default=[]),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    username = username.strip().lower()
    form_values = _user_form_values(
        username=username,
        display_name=display_name,
        email=email,
        target_ids=target_ids,
        role=role,
    )
    if password_generated == "true" and credential_handoff_confirmed != "true":
        return _render(request, "user_form.html", {
            "user": None,
            "admin": admin,
            "error": "Generate the password again, save the full value, and confirm the handoff before creating the user.",
            "form_values": form_values,
        }, status_code=422)
    if confirm_password is not None and password_generated != "true" and password != confirm_password:
        return _render(request, "user_form.html", {
            "user": None, "admin": admin, "suggested": "",
            "error": "Password confirmation does not match.",
            "form_values": form_values,
            "targets": _targets_context(),
            "password_policy": get_settings().file.password_policy,
        }, status_code=422)
    if not USERNAME_RE.fullmatch(username):
        return _render(
            request,
            "user_form.html",
            {"user": None, "admin": admin, "suggested": password,
             "form_values": form_values,
             "error": "Username must use lowercase letters, digits, underscores, dots or hyphens; separators cannot be first or last."},
            status_code=422,
        )
    with get_session() as db:
        if db.query(ManagedUser).filter(ManagedUser.username == username).first():
            return _render(
                request,
                "user_form.html",
                {"user": None, "admin": admin, "suggested": password,
                 "form_values": form_values,
                 "error": f"Username '{username}' already exists."},
                status_code=422,
            )
        validation = validate_password(password, username=username, email=email, display_name=display_name)
        if not validation.valid:
            return _render(request, "user_form.html", {"user": None, "admin": admin,
                "suggested": password, "form_values": form_values,
                "error": " ".join(validation.errors)}, status_code=422)
        connectors = {item.target_id: item for item in get_connectors()}
        if any(item not in connectors for item in target_ids):
            return RedirectResponse("/users/new", status_code=303)
        assigned_role = (
            role if has_permission(current_user(request)["role"], MANAGE_SECURITY)
            and role in {item.value for item in ASSIGNABLE_ROLES}
            else "user"
        )
        user = ManagedUser(
            username=username, display_name=display_name.strip(), email=email.strip(),
            password_hash=hash_password(password), role=assigned_role,
            password_decision_required=True, password_decision_kind="initial", password_changed_at=utcnow(),
        )
        identity = validate_for_targets(user, [connectors[item] for item in target_ids])
        if not identity.ok:
            return _render(request, "user_form.html", {"user": None, "admin": admin,
                "suggested": "", "form_values": form_values,
                "error": identity.detail, "targets": list(connectors.values()),
                "password_policy": get_settings().file.password_policy}, status_code=422)
        user.desired_action = "ensure"
        db.add(user)
        db.flush()
        _set_pending(db, user, password, None if not get_settings().config_file and not target_ids else set(target_ids),
                     require_password_change=True)
        operation = request_operation(db, user, LifecycleCommand.CREATE, admin)
        record_audit(db, admin, "user.create", username, operation_id=operation.id)
        if assigned_role != "user":
            record_audit(
                db, admin, "role.assigned", username,
                f"role={assigned_role}", operation_id=operation.id,
            )
        db.commit()
        user_id = user.id
    background_tasks.add_task(sync_user, user_id, operation_id=operation.id)
    return redirect_with_feedback(
        "/users",
        title="User created",
        message=(
            f"{username} was saved. The user must replace the temporary password "
            "before assigned targets can be provisioned."
        ),
    )


def _bulk_selection(user_ids: list[int]) -> list[int]:
    return list(dict.fromkeys(user_ids))[:100]


@router.post("/users/bulk/preview")
async def bulk_preview(
    request: Request,
    user_ids: list[int] = Form(default=[]),
    action: str = Form(""),
    target_id: str = Form(""),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    selected = _bulk_selection(user_ids)
    connectors = {item.target_id: item for item in get_connectors()}
    error = ""
    if not selected:
        error = "Select at least one managed account."
    elif action not in BULK_ACTIONS:
        error = "Choose a supported bulk action."
    elif action in {"assign", "unassign"} and target_id not in connectors:
        error = "Choose a configured target for assignment changes."
    with get_session() as db:
        found = db.query(ManagedUser).filter(ManagedUser.id.in_(selected)).all() if selected else []
        by_id = {user.id: user for user in found}
        users = [
            by_id[item] for item in selected
            if item in by_id and _can_manage_subject(request, by_id[item])
        ]
        excluded = len(selected) - len(users)
    action_labels = {
        "assign": "Assign target (onboard)",
        "unassign": "Unassign and disable target (offboard)",
        "disable": "Disable accounts",
        "retry": "Retry failed targets",
    }
    return _render(request, "bulk_preview.html", {
        "admin": admin,
        "users": users,
        "selected_ids": selected,
        "action": action,
        "action_label": action_labels.get(action, "Bulk action"),
        "target_id": target_id,
        "target": connectors.get(target_id),
        "excluded": excluded,
        "replay_token": str(uuid4()),
        "error": error or None,
        "error_title": "Bulk action not ready",
    }, status_code=422 if error else 200)


@router.post("/users/bulk/execute")
async def bulk_execute(
    request: Request,
    background_tasks: BackgroundTasks,
    user_ids: list[int] = Form(default=[]),
    action: str = Form(""),
    target_id: str = Form(""),
    replay_token: str = Form(""),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    selected = _bulk_selection(user_ids)
    connectors = {item.target_id: item for item in get_connectors()}
    try:
        token = str(UUID(replay_token))
    except (ValueError, AttributeError):
        return redirect_with_feedback(
            "/users", title="Bulk action rejected",
            message="The confirmation token is invalid. Preview the action again.", level="danger",
        )
    if (
        not selected
        or action not in BULK_ACTIONS
        or (action in {"assign", "unassign"} and target_id not in connectors)
    ):
        return redirect_with_feedback(
            "/users", title="Bulk action rejected",
            message="The selected accounts or action are no longer valid. Preview the action again.",
            level="danger",
        )

    queued: list[tuple[int, str | None, str | None]] = []
    with get_session() as db:
        subject = f"bulk:{token}"
        existing = db.query(LifecycleOperation).filter_by(
            command=LifecycleCommand.BULK.value, actor=admin, subject=subject
        ).one_or_none()
        if existing:
            return redirect_with_feedback(
                "/users", title="Bulk action already processed",
                message=f"No changes were repeated. Correlation {existing.id[:8]} records the original result.",
            )
        parent = LifecycleOperation(
            command=LifecycleCommand.BULK.value,
            status=OperationStatus.RUNNING.value,
            actor=admin,
            subject=subject,
            requested_target=target_id or None,
            total_targets=len(selected),
            started_at=utcnow(),
        )
        db.add(parent)
        db.flush()
        found = db.query(ManagedUser).filter(ManagedUser.id.in_(selected)).all()
        by_id = {user.id: user for user in found}
        succeeded = 0
        failures: list[str] = []
        for user_id in selected:
            user = by_id.get(user_id)
            if not user or not _can_manage_subject(request, user) or user.desired_action == "delete":
                failures.append(f"{user_id}: unavailable or protected")
                continue
            if action == "retry":
                retry_states = [
                    state for state in user.sync_states
                    if not state.retired and state.state == "failed"
                    and (not target_id or state.target == target_id)
                ]
                if not retry_states:
                    failures.append(f"{user.username}: no failed targets")
                    continue
                for state in retry_states:
                    owning_operation = (
                        db.get(LifecycleOperation, state.operation_id)
                        if state.operation_id else None
                    )
                    reusable_id = (
                        owning_operation.id
                        if owning_operation
                        and owning_operation.status in {
                            OperationStatus.FAILED.value,
                            OperationStatus.PARTIALLY_FAILED.value,
                            OperationStatus.QUEUED.value,
                            OperationStatus.RUNNING.value,
                        }
                        else None
                    )
                    queued.append((user.id, state.target, reusable_id))
            elif action == "disable":
                if user.status != "disabled":
                    try:
                        operation = request_operation(db, user, LifecycleCommand.DISABLE, admin)
                    except OperationConflict as error:
                        failures.append(f"{user.username}: {error}")
                        continue
                    user.status = "disabled"
                    queued.append((user.id, None, operation.id))
            else:
                assigned = {
                    state.target for state in user.sync_states if state.assigned and not state.retired
                }
                changed = target_id not in assigned if action == "assign" else target_id in assigned
                if changed:
                    try:
                        operation = request_operation(db, user, LifecycleCommand.UPDATE, admin)
                    except OperationConflict as error:
                        failures.append(f"{user.username}: {error}")
                        continue
                    if action == "assign":
                        assigned.add(target_id)
                    else:
                        assigned.discard(target_id)
                    _set_pending(db, user, None, assigned, assignment_actor=admin)
                    queued.append((user.id, None, operation.id))
            record_audit(
                db, admin, f"bulk.{action}", user.username,
                f"target={target_id or 'all'}", operation_id=parent.id,
            )
            succeeded += 1
        failed = len(failures)
        parent.completed_targets = succeeded
        parent.failed_targets = failed
        parent.status = (
            OperationStatus.SUCCEEDED.value if failed == 0
            else OperationStatus.PARTIALLY_FAILED.value if succeeded else OperationStatus.FAILED.value
        )
        parent.detail = (
            f"action={action}; succeeded={succeeded}; failed={failed}"
            + (f"; {' | '.join(failures[:10])}" if failures else "")
        )
        parent.completed_at = utcnow()
        enqueue_notification(
            db, "approval.completed", actor=admin, subject=subject,
            dedupe_key=f"bulk:{parent.id}", operation_id=parent.id,
            target_id=target_id or None, outcome=parent.status,
        )
        enqueue_notification(
            db, "lifecycle.completed", actor=admin, subject=subject,
            dedupe_key=f"{parent.id}:{parent.status}", operation_id=parent.id,
            target_id=target_id or None, outcome=parent.status,
        )
        db.commit()
        correlation = parent.id[:8]
    for user_id, retry_target, operation_id in queued:
        background_tasks.add_task(
            sync_user, user_id, target=retry_target, actor=admin, operation_id=operation_id
        )
    return redirect_with_feedback(
        "/users",
        title="Bulk action complete" if not failures else "Bulk action partially complete",
        message=(
            f"{succeeded} account(s) accepted; {len(failures)} failed validation. "
            f"Correlation {correlation}. Replaying this confirmation will not repeat changes."
        ),
        level="success" if not failures else "danger",
    )


@router.get("/users/{user_id}")
async def user_detail_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.is_root:
            return RedirectResponse("/users", status_code=303)
        user.sync_states
        summary = summarise_user(user)
    targets = _targets_context()
    return _render(request, "user_detail.html", {
        "user": user,
        "admin": admin,
        "summary": summary,
        "targets": targets,
        "sync_views": _sync_views([user], targets),
        "can_manage_subject": _can_manage_subject(request, user),
    })


@router.get("/users/{user_id}/edit")
async def edit_user_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.desired_action == "delete" or not _can_manage_subject(request, user):
            if user and not user.is_root:
                return Response("Forbidden", status_code=403)
            return RedirectResponse("/users", status_code=303)
        user.sync_states
    return _render(
        request,
        "user_form.html",
        {"user": user, "admin": admin, "suggested": "", "error": None,
         "targets": _targets_context(), "password_policy": get_settings().file.password_policy},
    )


@router.post("/users/{user_id}")
async def update_user(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int,
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str | None = Form(None),
    password_generated: str = Form("false"),
    credential_handoff_confirmed: str = Form("false"),
    status: str = Form("active"),
    role: str | None = Form(None),
    target_ids: list[str] = Form(default=[]),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.desired_action == "delete" or not _can_manage_subject(request, user):
            if user and not user.is_root:
                return Response("Forbidden", status_code=403)
            return RedirectResponse("/users", status_code=303)
        connectors = {item.target_id: item for item in get_connectors()}
        form_values = _user_form_values(
            display_name=display_name,
            email=email,
            target_ids=target_ids,
            role=role or user.role,
        )
        if password_generated == "true" and credential_handoff_confirmed != "true":
            return _render(request, "user_form.html", {
                "user": user,
                "admin": admin,
                "error": "Generate the password again, save the full value, and confirm the handoff before saving the reset.",
                "form_values": form_values,
                "targets": list(connectors.values()),
            }, status_code=422)
        if any(item not in connectors for item in target_ids):
            return RedirectResponse(f"/users/{user_id}", status_code=303)
        proposed = ManagedUser(username=user.username, display_name=display_name.strip(), email=email.strip())
        identity = validate_for_targets(proposed, [connectors[item] for item in target_ids])
        if not identity.ok:
            return _render(request, "user_form.html", {"user": user, "admin": admin,
                "suggested": "", "form_values": form_values,
                "error": identity.detail, "targets": list(connectors.values()),
                "password_policy": get_settings().file.password_policy}, status_code=422)
        user.display_name = proposed.display_name
        user.email = proposed.email
        user.status = "disabled" if status == "disabled" else "active"
        old_role = user.role
        if (
            has_permission(current_user(request)["role"], MANAGE_SECURITY)
            and role in {item.value for item in ASSIGNABLE_ROLES}
        ):
            user.role = role
        if user.role != old_role:
            user.session_version += 1
        if password.strip():
            if confirm_password is not None and password_generated != "true" and password.strip() != confirm_password:
                return _render(request, "user_form.html", {
                    "user": user, "admin": admin, "suggested": "",
                    "form_values": form_values,
                    "error": "Password confirmation does not match.",
                    "targets": list(connectors.values()),
                    "password_policy": get_settings().file.password_policy,
                }, status_code=422)
            history = tuple(row.password_hash for row in db.query(PasswordHistory).filter_by(user_id=user.id).order_by(PasswordHistory.created_at.desc()).limit(get_settings().file.password_policy.history_size).all())
            validation = validate_password(password.strip(), username=user.username, email=user.email,
                                           display_name=user.display_name, history_hashes=history)
            if not validation.valid:
                return _render(request, "user_form.html", {"user": user, "admin": admin,
                    "suggested": password, "form_values": form_values,
                    "error": " ".join(validation.errors)}, status_code=422)
            if user.password_hash:
                db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
            user.password_hash = hash_password(password.strip())
            user.password_changed_at = utcnow()
            user.password_keep_until = None
            user.password_keep_count = 0
            user.password_decision_required = True
            user.password_decision_kind = "reset"
            user.session_version += 1
        try:
            operation = request_operation(db, user, LifecycleCommand.UPDATE, admin)
        except OperationConflict as error:
            return _render(
                request,
                "user_form.html",
                {
                    "user": user,
                    "admin": admin,
                    "suggested": "",
                    "form_values": form_values,
                    "error": str(error),
                    "targets": list(connectors.values()),
                    "password_policy": get_settings().file.password_policy,
                },
                status_code=409,
            )
        user.desired_action = "ensure"
        _set_pending(db, user, password.strip() or None, None if not get_settings().config_file and not target_ids else set(target_ids),
                     require_password_change=bool(password.strip()), assignment_actor=admin)
        record_audit(db, admin, "user.update", user.username, operation_id=operation.id)
        if user.role != old_role:
            record_audit(
                db, admin, "role.assigned", user.username,
                f"from={old_role}; to={user.role}", operation_id=operation.id,
            )
        db.commit()
    background_tasks.add_task(sync_user, user_id, operation_id=operation.id)
    return redirect_with_feedback(
        "/users",
        title="Changes saved",
        message=f"{user.username} was updated and target synchronization has started.",
    )


def _lifecycle_action_page(
    request: Request,
    admin: str,
    user: ManagedUser,
    *,
    action_kind: str,
    title: str,
    error: str = "",
    status_code: int = 200,
):
    with get_session() as db:
        attached = db.get(ManagedUser, user.id)
        attached.sync_states
        operation = (
            operation_payload(
                get_latest_operation(db, attached, LifecycleCommand.DELETE),
                attached.sync_states,
            )
            if action_kind != "delete"
            else None
        )
        assigned_targets = [
            state.target
            for state in attached.sync_states
            if state.assigned and not state.retired
        ]
    return _render(
        request,
        "user_action.html",
        {
            "admin": admin,
            "user": attached,
            "title": title,
            "action_kind": action_kind,
            "assigned_targets": assigned_targets,
            "operation": operation,
            "error": error,
            "error_title": f"{title} not confirmed",
        },
        status_code=status_code,
    )


@router.get("/users/{user_id}/delete")
async def delete_user_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or not _can_manage_subject(request, user) or user.desired_action == "delete":
            return redirect_with_feedback(
                "/users",
                title="Delete unavailable",
                message="This account cannot start a new deletion operation.",
                level="danger",
            )
    return _lifecycle_action_page(
        request, admin, user, action_kind="delete", title="Delete user"
    )


@router.post("/users/{user_id}/delete")
async def delete_user(
    request: Request, user_id: int, background_tasks: BackgroundTasks
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user and _can_manage_subject(request, user):
            operation = request_operation(db, user, LifecycleCommand.DELETE, admin)
            user.desired_action = "delete"
            user.deletion_requested_at = utcnow()
            user.deleted_at = None
            assigned = {
                state.target for state in user.sync_states
                if state.assigned and not state.retired
            }
            _set_pending(
                db, user, None, assigned,
                update_assignment_exceptions=False,
            )
            record_audit(
                db,
                admin,
                "user.delete",
                user.username,
                "requested",
                operation.id,
            )
            enqueue_notification(
                db, "approval.completed", actor=admin, subject=user.username,
                dedupe_key=f"delete:{operation.id}", operation_id=operation.id,
                outcome="approved",
            )
            db.commit()
            background_tasks.add_task(
                sync_user, user_id, "delete", operation_id=operation.id
            )
            return redirect_with_feedback(
                "/users",
                title="Deletion requested",
                message=(
                    f"Operation {operation.id[:8]} is removing {user.username} from "
                    "assigned targets. Recovery appears only after it completes."
                ),
                level="info",
            )
    return redirect_with_feedback(
        "/users",
        title="Delete unavailable",
        message="The account was not found or is protected.",
        level="danger",
    )


@router.post("/users/{user_id}/retry/{target}")
async def retry_user_target(
    request: Request, user_id: int, target: str, background_tasks: BackgroundTasks
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    valid_targets = {item.target_id for item in __import__("na_sso.connectors", fromlist=["get_connectors"]).get_connectors()}
    if not get_settings().config_file:
        valid_targets.update({"opnsense", "nexus", "nextcloud"})
    if target not in valid_targets:
        return RedirectResponse("/users", status_code=303)
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or not _can_manage_subject(request, user):
            return RedirectResponse("/users", status_code=303)
        state = next((item for item in user.sync_states if item.target == target), None)
        operation_id = state.operation_id if state else None
        record_audit(
            db, admin, "sync.retry", user.username, target, operation_id
        )
        db.commit()
    background_tasks.add_task(
        sync_user,
        user_id,
        None,
        target,
        "manual-retry",
        operation_id=operation_id,
    )
    return RedirectResponse("/users", status_code=303)


@router.get("/users/{user_id}/restore")
async def restore_user_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if (
            not user
            or not _can_manage_subject(request, user)
            or user.desired_action != "delete"
            or user.deleted_at is None
        ):
            return redirect_with_feedback(
                "/users",
                title="Restore unavailable",
                message="The account can be restored only after remote deletion completes.",
                level="danger",
            )
    return _lifecycle_action_page(
        request, admin, user, action_kind="restore", title="Restore user"
    )


@router.post("/users/{user_id}/restore")
async def restore_user(
    request: Request,
    user_id: int,
    background_tasks: BackgroundTasks,
    password: str = Form(...),
    confirm_password: str | None = Form(None),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if (
            not user
            or not _can_manage_subject(request, user)
            or user.desired_action != "delete"
            or user.deleted_at is None
            or not password.strip()
        ):
            return redirect_with_feedback(
                "/users",
                title="Restore unavailable",
                message="The account can be restored only after remote deletion completes.",
                level="danger",
            )
        if confirm_password is not None and password != confirm_password:
            return _lifecycle_action_page(
                request,
                admin,
                user,
                action_kind="restore",
                title="Restore user",
                error="Password confirmation does not match.",
                status_code=422,
            )
        validation = validate_password(password.strip(), username=user.username, email=user.email, display_name=user.display_name)
        if not validation.valid:
            return _lifecycle_action_page(
                request,
                admin,
                user,
                action_kind="restore",
                title="Restore user",
                error=" ".join(validation.errors),
                status_code=422,
            )
        try:
            operation = request_operation(db, user, LifecycleCommand.RESTORE, admin)
        except OperationConflict:
            return redirect_with_feedback(
                "/users",
                title="User not restored",
                message="Another lifecycle operation is still running.",
                level="danger",
            )
        user.desired_action = "ensure"
        user.deletion_requested_at = None
        user.deleted_at = None
        user.status = "active"
        if user.password_hash:
            db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
        user.password_hash = hash_password(password.strip())
        user.password_changed_at = utcnow()
        user.password_keep_until = None
        user.password_keep_count = 0
        user.password_decision_required = True
        user.password_decision_kind = "reset"
        user.session_version += 1
        from na_sso.assignments import resolve_assignment_intents
        connectors = {item.target_id: item for item in get_connectors()}
        intents = resolve_assignment_intents(db, user, connectors)
        _set_pending(
            db,
            user,
            password.strip(),
            set(intents),
            require_password_change=True,
            remote_accounts_absent=True,
            update_assignment_exceptions=False,
        )
        record_audit(db, admin, "user.restore", user.username, operation_id=operation.id)
        db.commit()
    background_tasks.add_task(sync_user, user_id, operation_id=operation.id)
    return redirect_with_feedback(
        "/users",
        title="User restored",
        message=(
            f"{user.username} is active locally. The user must replace the temporary "
            "password before assigned targets are provisioned again."
        ),
    )


@router.get("/users/{user_id}/purge")
async def purge_user_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if (
            not user
            or not _can_manage_subject(request, user)
            or user.desired_action != "delete"
            or user.deleted_at is None
        ):
            return redirect_with_feedback(
                "/users",
                title="Purge unavailable",
                message="The local record can be purged only after remote deletion completes.",
                level="danger",
            )
    return _lifecycle_action_page(
        request, admin, user, action_kind="purge", title="Purge local record"
    )


@router.post("/users/{user_id}/purge")
async def purge_user(
    request: Request, user_id: int, confirm_username: str = Form("")
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if (
            user
            and _can_manage_subject(request, user)
            and user.desired_action == "delete"
            and user.deleted_at is not None
        ):
            if confirm_username != user.username:
                return _lifecycle_action_page(
                    request,
                    admin,
                    user,
                    action_kind="purge",
                    title="Purge local record",
                    error=f"Type {user.username} exactly to confirm permanent removal.",
                    status_code=422,
                )
            username = user.username
            operation = request_operation(db, user, LifecycleCommand.PURGE, admin)
            finish_operation(
                user,
                operation,
                OperationStatus.SUCCEEDED,
                completed_targets=0,
                failed_targets=0,
            )
            record_audit(
                db, admin, "user.purge", user.username, operation_id=operation.id
            )
            enqueue_notification(
                db, "approval.completed", actor=admin, subject=user.username,
                dedupe_key=f"purge:{operation.id}", operation_id=operation.id,
                outcome="approved",
            )
            enqueue_notification(
                db, "lifecycle.completed", actor=admin, subject=user.username,
                dedupe_key=f"{operation.id}:{operation.status}", operation_id=operation.id,
                outcome=operation.status,
            )
            db.delete(user)
            db.commit()
            return redirect_with_feedback(
                "/users",
                title="Local record purged",
                message=(
                    f"{username} was removed locally. Correlated operation and audit "
                    "history were retained."
                ),
            )
    return redirect_with_feedback(
        "/users",
        title="Purge unavailable",
        message="The local record can be purged only after remote deletion completes.",
        level="danger",
    )
