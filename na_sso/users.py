import re

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import current_admin
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.models import ManagedUser, PasswordHistory, SyncState, utcnow
from na_sso.security import encrypt_secret, generate_password, hash_password, validate_password
from na_sso.sync import sync_user
from na_sso.connectors import get_connectors, validate_for_targets

router = APIRouter()
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?$")


def _guard(request: Request) -> str | Response:
    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    return admin


def _render(request: Request, name: str, ctx: dict, **kw):
    from na_sso.main import templates

    ctx.setdefault("targets", _targets_context())
    ctx.setdefault("password_policy", get_settings().file.password_policy)
    return templates.TemplateResponse(request, name, ctx, **kw)


def _targets_context() -> list:
    return get_connectors()


def _set_pending(db, user: ManagedUser, password: str | None, target_ids: set[str] | None = None,
                 *, require_password_change: bool = False) -> None:
    """Record a new pending credential and reset all target sync states."""
    if password is not None and not require_password_change:
        user.pending_secret = encrypt_secret(password)
    elif require_password_change:
        user.pending_secret = None
    existing = {s.target: s for s in user.sync_states}
    connectors = {item.target_id: item for item in __import__("na_sso.connectors", fromlist=["get_connectors"]).get_connectors()}
    legacy_mode = not get_settings().config_file
    selected = set(connectors) if target_ids is None and (user.sync_states or legacy_mode) else (target_ids or set())
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
            state.state = "pending_chpw_disable" if not newly_assigned and previous_state == "ok" else "chpw"
            state.detail = "password change required before propagation"
        else:
            state.state = "awaiting_credentials" if newly_assigned and needs_password and password is None else "pending"
            state.detail = ""
        state.attempt_count = 0
        state.next_retry_at = None


@router.get("/")
async def index(request: Request):
    return RedirectResponse("/users" if current_admin(request) else "/login", status_code=303)


@router.get("/users")
async def list_users(request: Request):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        users = db.query(ManagedUser).order_by(ManagedUser.username).all()
        for u in users:
            u.sync_states  # eager-load for template
    return _render(request, "users.html", {"users": users, "admin": admin, "targets": _targets_context()})


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
    role: str = Form("user"),
    target_ids: list[str] = Form(default=[]),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    username = username.strip().lower()
    if confirm_password is not None and password_generated != "true" and password != confirm_password:
        return _render(request, "user_form.html", {
            "user": None, "admin": admin, "suggested": "",
            "error": "Password confirmation does not match.",
            "targets": _targets_context(),
            "password_policy": get_settings().file.password_policy,
        }, status_code=422)
    if not USERNAME_RE.fullmatch(username):
        return _render(
            request,
            "user_form.html",
            {"user": None, "admin": admin, "suggested": password,
             "error": "Username must use lowercase letters, digits, underscores, dots or hyphens; separators cannot be first or last."},
            status_code=422,
        )
    with get_session() as db:
        if db.query(ManagedUser).filter(ManagedUser.username == username).first():
            return _render(
                request,
                "user_form.html",
                {"user": None, "admin": admin, "suggested": password,
                 "error": f"Username '{username}' already exists."},
                status_code=422,
            )
        validation = validate_password(password, username=username, email=email, display_name=display_name)
        if not validation.valid:
            return _render(request, "user_form.html", {"user": None, "admin": admin,
                "suggested": password, "error": " ".join(validation.errors)}, status_code=422)
        connectors = {item.target_id: item for item in get_connectors()}
        if any(item not in connectors for item in target_ids):
            return RedirectResponse("/users/new", status_code=303)
        user = ManagedUser(
            username=username, display_name=display_name.strip(), email=email.strip(),
            password_hash=hash_password(password), role="admin" if role == "admin" else "user",
            password_decision_required=True, password_decision_kind="initial", password_changed_at=utcnow(),
        )
        identity = validate_for_targets(user, [connectors[item] for item in target_ids])
        if not identity.ok:
            return _render(request, "user_form.html", {"user": None, "admin": admin,
                "suggested": "", "error": identity.detail, "targets": list(connectors.values()),
                "password_policy": get_settings().file.password_policy}, status_code=422)
        user.desired_action = "ensure"
        db.add(user)
        _set_pending(db, user, password, None if not get_settings().config_file and not target_ids else set(target_ids),
                     require_password_change=True)
        record_audit(db, admin, "user.create", username)
        db.commit()
        user_id = user.id
    background_tasks.add_task(sync_user, user_id)
    return RedirectResponse("/users", status_code=303)


@router.get("/users/{user_id}")
async def edit_user_page(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.desired_action == "delete" or user.is_root:
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
    status: str = Form("active"),
    role: str = Form("user"),
    target_ids: list[str] = Form(default=[]),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.desired_action == "delete" or user.is_root:
            return RedirectResponse("/users", status_code=303)
        connectors = {item.target_id: item for item in get_connectors()}
        if any(item not in connectors for item in target_ids):
            return RedirectResponse(f"/users/{user_id}", status_code=303)
        proposed = ManagedUser(username=user.username, display_name=display_name.strip(), email=email.strip())
        identity = validate_for_targets(proposed, [connectors[item] for item in target_ids])
        if not identity.ok:
            return _render(request, "user_form.html", {"user": user, "admin": admin,
                "suggested": "", "error": identity.detail, "targets": list(connectors.values()),
                "password_policy": get_settings().file.password_policy}, status_code=422)
        user.display_name = proposed.display_name
        user.email = proposed.email
        user.status = "disabled" if status == "disabled" else "active"
        user.role = "admin" if role == "admin" else "user"
        if password.strip():
            if confirm_password is not None and password_generated != "true" and password.strip() != confirm_password:
                return _render(request, "user_form.html", {
                    "user": user, "admin": admin, "suggested": "",
                    "error": "Password confirmation does not match.",
                    "targets": list(connectors.values()),
                    "password_policy": get_settings().file.password_policy,
                }, status_code=422)
            history = tuple(row.password_hash for row in db.query(PasswordHistory).filter_by(user_id=user.id).order_by(PasswordHistory.created_at.desc()).limit(get_settings().file.password_policy.history_size).all())
            validation = validate_password(password.strip(), username=user.username, email=user.email,
                                           display_name=user.display_name, history_hashes=history)
            if not validation.valid:
                return _render(request, "user_form.html", {"user": user, "admin": admin,
                    "suggested": password, "error": " ".join(validation.errors)}, status_code=422)
            if user.password_hash:
                db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
            user.password_hash = hash_password(password.strip())
            user.password_changed_at = utcnow()
            user.password_decision_required = True
            user.password_decision_kind = "reset"
            user.session_version += 1
        user.desired_action = "ensure"
        _set_pending(db, user, password.strip() or None, None if not get_settings().config_file and not target_ids else set(target_ids),
                     require_password_change=bool(password.strip()))
        record_audit(db, admin, "user.update", user.username)
        db.commit()
    background_tasks.add_task(sync_user, user_id)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    request: Request, user_id: int, background_tasks: BackgroundTasks
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user and not user.is_root:
            user.desired_action = "delete"
            user.deletion_requested_at = utcnow()
            user.deleted_at = None
            _set_pending(db, user, None)
            record_audit(db, admin, "user.delete", user.username, "requested")
            db.commit()
            background_tasks.add_task(sync_user, user_id, "delete")
    return RedirectResponse("/users", status_code=303)


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
        if not user or user.is_root:
            return RedirectResponse("/users", status_code=303)
        record_audit(db, admin, "sync.retry", user.username, target)
        db.commit()
    background_tasks.add_task(sync_user, user_id, None, target, "manual-retry")
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/restore")
async def restore_user(request: Request, user_id: int, background_tasks: BackgroundTasks, password: str = Form(...)):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.is_root or user.desired_action != "delete" or not password.strip():
            return RedirectResponse("/users", status_code=303)
        validation = validate_password(password.strip(), username=user.username, email=user.email, display_name=user.display_name)
        if not validation.valid:
            return RedirectResponse(f"/users/{user_id}", status_code=303)
        user.desired_action = "ensure"
        user.deletion_requested_at = None
        user.deleted_at = None
        user.status = "active"
        if user.password_hash:
            db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
        user.password_hash = hash_password(password.strip())
        user.password_changed_at = utcnow()
        user.password_decision_required = True
        user.password_decision_kind = "reset"
        user.session_version += 1
        _set_pending(db, user, password.strip(), require_password_change=True)
        record_audit(db, admin, "user.restore", user.username)
        db.commit()
    background_tasks.add_task(sync_user, user_id)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/purge")
async def purge_user(request: Request, user_id: int):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user and not user.is_root and user.desired_action == "delete" and user.deleted_at is not None:
            record_audit(db, admin, "user.purge", user.username)
            db.delete(user)
            db.commit()
    return RedirectResponse("/users", status_code=303)
