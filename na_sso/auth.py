from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from na_sso.config import get_settings

COOKIE = "na-sso-session"
MAX_AGE = 12 * 3600

router = APIRouter()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="session")


def current_admin(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
    except (BadSignature, KeyError):
        return None
    from na_sso.db import get_session
    from na_sso.models import ManagedUser

    if "id" not in data:  # pre-migration cookie; force a fresh authenticated session
        return None
    with get_session() as db:
        account = db.get(ManagedUser, data["id"])
        if (
            not account
            or account.status != "active"
            or account.desired_action == "delete"
            or account.session_version != data.get("v")
            or account.role not in {"admin", "root"}
        ):
            return None
        return account.username


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
    except (BadSignature, KeyError):
        return None
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        account = db.get(ManagedUser, data.get("id"))
        if not account or account.status != "active" or account.session_version != data.get("v"):
            return None
        return {
            "id": account.id,
            "username": account.username,
            "display_name": account.display_name,
            "email": account.email,
            "role": account.role,
            "status": account.status,
            "has_ssh_key": bool(account.ssh_public_key),
            "password_decision_kind": account.password_decision_kind,
            "password_expires_at": account.password_expires_at,
            "restricted": account.password_decision_required,
        }


@router.get("/login")
async def login_page(request: Request):
    from na_sso.main import templates

    if current_admin(request):
        return RedirectResponse("/users", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    from na_sso.main import templates

    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import verify_password

    with get_session() as db:
        admin = (
            db.query(ManagedUser).filter(ManagedUser.username == username).one_or_none()
        )
    if admin and admin.password_hash and admin.status == "active" and verify_password(password, admin.password_hash):
        from na_sso.sync import credential_handoff
        await credential_handoff(admin.id, password)
        destination = "/account/password-decision" if admin.password_decision_required else ("/users" if admin.role in {"admin", "root"} else "/account")
        resp = RedirectResponse(destination, status_code=303)
        resp.set_cookie(
            COOKIE,
            _serializer().dumps({"id": admin.id, "v": admin.session_version}),
            max_age=MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return resp
    return templates.TemplateResponse(
        request, "login.html", {"error": "Invalid credentials."}, status_code=401
    )


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


def _password_form(request: Request, title: str, *, decision: bool = False, error: str = "") -> HTMLResponse:
    from na_sso.main import templates

    policy = get_settings().file.password_policy
    account = current_user(request)
    return templates.TemplateResponse(request, "password_form.html", {
        "title": title,
        "decision": decision,
        "allow_keep": bool(account and account["password_decision_kind"] == "expired"),
        "error": error,
        "policy": policy,
        "admin": account["username"] if account else None,
        "home_url": "/account",
    })


@router.get("/account")
async def account_page(request: Request):
    from na_sso.main import templates

    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["restricted"]:
        return RedirectResponse("/account/password-decision", status_code=303)
    return templates.TemplateResponse(request, "account.html", {
        "account": account,
        "admin": account["username"],
        "home_url": "/account",
        "fallback_enabled": get_settings().file.ssh_key_policy.allow_server_fallback,
    })


@router.get("/account/password-decision")
async def password_decision_page(request: Request):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if not account["restricted"]:
        return RedirectResponse("/account", status_code=303)
    title = "Accept or change your password" if account["password_decision_kind"] == "expired" else "Change your temporary password"
    return _password_form(request, title, decision=True)


@router.post("/account/password-decision")
async def password_decision(request: Request, choice: str = Form(...), current_password: str = Form(""), new_password: str = Form(""), confirm_password: str | None = Form(None), password_generated: str = Form("false")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return await _complete_password_action(request, account, choice, current_password, new_password, confirm_password, password_generated, decision=True)


@router.get("/account/password")
async def password_page(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return _password_form(request, "Change password")


@router.post("/account/password")
async def password_change(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str | None = Form(None), password_generated: str = Form("false"), choice: str = Form("change")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return await _complete_password_action(request, account, "change", current_password, new_password, confirm_password, password_generated, decision=False)


@router.post("/account/ssh-key")
async def enroll_ssh_key(request: Request, public_key: str = Form(""), private_key: str = Form("")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["role"] == "root":
        return HTMLResponse("Root is local-only.", status_code=403)
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import public_key_from_private
    try:
        derived = public_key_from_private(private_key) if private_key else public_key.strip()
    except (TypeError, ValueError):
        return HTMLResponse("Invalid private key.", status_code=422)
    if not derived.startswith(("ssh-ed25519 ", "ssh-rsa ")):
        return HTMLResponse("Unsupported SSH public key.", status_code=422)
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        user.ssh_public_key = derived
        record_audit(db, user.username, "ssh_key.enrolled", user.username, derived.split()[0])
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    return RedirectResponse("/account", status_code=303)


@router.post("/account/ssh-key/generate")
async def server_generate_ssh_key(request: Request):
    from na_sso.main import templates

    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    from na_sso.config import get_settings
    if account["role"] == "root" or not get_settings().file.ssh_key_policy.allow_server_fallback:
        return HTMLResponse("Server-handled fallback is disabled.", status_code=403)
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import generate_ssh_keypair
    private, public = generate_ssh_keypair()
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        user.ssh_public_key = public
        record_audit(db, user.username, "ssh_key.generated_once", user.username, "ed25519 server-handled non-persistent")
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    content = templates.get_template("private_key_once.html").render({
        "request": request,
        "admin": account["username"],
        "home_url": "/account",
        "private_key": private,
    })
    return HTMLResponse(content, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _complete_password_action(request: Request, account: dict, choice: str, current_password: str, new_password: str, confirm_password: str | None, password_generated: str, *, decision: bool):
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser, PasswordHistory, utcnow
    from na_sso.security import hash_password, validate_password, verify_password
    from na_sso.config import get_settings

    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        if not user or not user.password_hash or not verify_password(current_password, user.password_hash):
            return _password_form(request, "Accept or change your password" if decision else "Change password", decision=decision, error="Invalid current password.")
        if choice == "keep" and decision and user.password_decision_kind == "expired":
            user.password_decision_required = False
            user.password_decision_kind = ""
            user.password_changed_at = utcnow()
            record_audit(db, user.username, "password.keep_acknowledged", user.username, "risk warning acknowledged")
        elif choice == "change":
            if confirm_password is not None and password_generated != "true" and new_password != confirm_password:
                return _password_form(request, "Accept or change your password" if decision else "Change password", decision=decision, error="Password confirmation does not match.")
            history_rows = db.query(PasswordHistory).filter_by(user_id=user.id).order_by(PasswordHistory.created_at.desc()).limit(get_settings().file.password_policy.history_size).all()
            validation = validate_password(new_password, username=user.username, email=user.email,
                display_name=user.display_name, old_password=current_password,
                history_hashes=tuple([user.password_hash, *(row.password_hash for row in history_rows)]))
            if not validation.valid:
                return _password_form(request, "Accept or change your password" if decision else "Change password", decision=decision, error=" ".join(validation.errors))
            db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
            user.password_hash = hash_password(new_password)
            user.password_decision_required = False
            user.password_decision_kind = ""
            user.password_changed_at = utcnow()
            user.session_version += 1
            record_audit(db, user.username, "password.changed", user.username)
        else:
            return _password_form(request, "Accept or change your password", decision=True, error="Choose keep or change.")
        db.commit()
        user_id = user.id
    from na_sso.sync import credential_handoff
    await credential_handoff(user_id, new_password if choice == "change" else current_password)
    response = RedirectResponse("/login" if choice == "change" else "/account", status_code=303)
    if choice == "change":
        response.delete_cookie(COOKIE)
    return response
