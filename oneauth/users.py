from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from oneauth.auth import current_admin
from oneauth.audit import record_audit
from oneauth.db import get_session
from oneauth.models import ManagedUser, SyncState, utcnow
from oneauth.security import encrypt_secret, generate_password
from oneauth.sync import sync_user

TARGETS = ["opnsense", "nexus", "nextcloud"]

router = APIRouter()


def _guard(request: Request) -> str | Response:
    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    return admin


def _render(request: Request, name: str, ctx: dict, **kw):
    from oneauth.main import templates

    return templates.TemplateResponse(request, name, ctx, **kw)


def _set_pending(db, user: ManagedUser, password: str | None) -> None:
    """Record a new pending credential and reset all target sync states."""
    if password is not None:
        user.pending_secret = encrypt_secret(password)
    existing = {s.target: s for s in user.sync_states}
    for target in TARGETS:
        state = existing.get(target)
        if state is None:
            state = SyncState(user=user, target=target)
            db.add(state)
        state.state = "pending"
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
    return _render(request, "users.html", {"users": users, "admin": admin})


@router.get("/users/new")
async def new_user_page(request: Request):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    return _render(
        request,
        "user_form.html",
        {"user": None, "admin": admin, "suggested": generate_password(), "error": None},
    )


@router.post("/users/new")
async def create_user(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    username = username.strip().lower()
    if not username.isidentifier():
        return _render(
            request,
            "user_form.html",
            {"user": None, "admin": admin, "suggested": password,
             "error": "Username must be letters, digits and underscores."},
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
        user = ManagedUser(
            username=username, display_name=display_name.strip(), email=email.strip()
        )
        user.desired_action = "ensure"
        db.add(user)
        _set_pending(db, user, password)
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
        if not user or user.desired_action == "delete":
            return RedirectResponse("/users", status_code=303)
        user.sync_states
    return _render(
        request,
        "user_form.html",
        {"user": user, "admin": admin, "suggested": generate_password(), "error": None},
    )


@router.post("/users/{user_id}")
async def update_user(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int,
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    status: str = Form("active"),
):
    admin = _guard(request)
    if isinstance(admin, Response):
        return admin
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.desired_action == "delete":
            return RedirectResponse("/users", status_code=303)
        user.display_name = display_name.strip()
        user.email = email.strip()
        user.status = "disabled" if status == "disabled" else "active"
        user.desired_action = "ensure"
        _set_pending(db, user, password.strip() or None)
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
        if user:
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
    if target not in TARGETS:
        return RedirectResponse("/users", status_code=303)
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user:
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
        if not user or user.desired_action != "delete" or not password.strip():
            return RedirectResponse("/users", status_code=303)
        user.desired_action = "ensure"
        user.deletion_requested_at = None
        user.deleted_at = None
        user.status = "active"
        _set_pending(db, user, password.strip())
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
        if user and user.desired_action == "delete" and user.deleted_at is not None:
            record_audit(db, admin, "user.purge", user.username)
            db.delete(user)
            db.commit()
    return RedirectResponse("/users", status_code=303)
