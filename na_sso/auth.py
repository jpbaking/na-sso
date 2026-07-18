from datetime import datetime, timedelta
from time import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from na_sso.config import get_settings
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.permissions import default_home, has_permission, permission_context, role_definition

COOKIE = "na-sso-session"
MAX_AGE = 12 * 3600

router = APIRouter()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="session")


def _acknowledgement_result(policy, now: datetime) -> datetime | None:
    if policy.expiry_acknowledgement_mode == "grace":
        return now + timedelta(days=policy.expiry_acknowledgement_grace_days)
    if (
        policy.expiry_acknowledgement_mode == "renewal"
        and policy.expires_after_days is not None
    ):
        return now + timedelta(days=policy.expires_after_days)
    return None


def permission_guard(request: Request, permission: str) -> dict | Response:
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if not has_permission(account["role"], permission):
        return Response("Forbidden", status_code=403)
    from na_sso.mfa import account_requires_mfa
    if account_requires_mfa(account["id"], account["role"]) and not account["mfa_verified"]:
        return RedirectResponse("/account/mfa", status_code=303)
    return account


def set_session_cookie(
    response: Response,
    *,
    account_id: int,
    session_version: int,
    mfa_verified: bool,
    authenticated_at: int | None = None,
) -> None:
    response.set_cookie(
        COOKIE,
        _serializer().dumps({
            "id": account_id,
            "v": session_version,
            "mfa": mfa_verified,
            "auth_at": authenticated_at or int(time()),
        }),
        max_age=MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=get_settings().session_cookie_secure,
    )


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
        if (
            not account
            or account.status != "active"
            or account.desired_action == "delete"
            or account.session_version != data.get("v")
        ):
            return None
        return {
            "id": account.id,
            "username": account.username,
            "display_name": account.display_name,
            "email": account.email,
            "role": account.role,
            "status": account.status,
            "has_ssh_key": bool(account.active_ssh_keys),
            "password_decision_kind": account.password_decision_kind,
            "password_expires_at": account.password_expires_at,
            "password_keep_until": account.password_keep_until,
            "password_keep_count": account.password_keep_count,
            "restricted": account.password_decision_required,
            "session_version": account.session_version,
            "mfa_verified": bool(data.get("mfa")),
            "authenticated_at": int(data.get("auth_at", 0)),
        }


@router.get("/login")
async def login_page(request: Request):
    from na_sso.main import templates

    account = current_user(request)
    if account:
        from na_sso.mfa import account_requires_mfa
        if account_requires_mfa(account["id"], account["role"]) and not account["mfa_verified"]:
            return RedirectResponse("/account/mfa", status_code=303)
        return RedirectResponse(default_home(account["role"]), status_code=303)
    return template_response(
        templates,
        request,
        "login.html",
        {"error": None, "error_title": "Sign-in failed"},
    )


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
        from na_sso.models import utcnow
        with get_session() as db:
            stored = db.get(ManagedUser, admin.id)
            if stored:
                stored.last_authenticated_at = utcnow()
                db.commit()
        from na_sso.sync import credential_handoff
        await credential_handoff(admin.id, password)
        operator = any(permission_context(admin.role).values())
        if not admin.password_decision_required and operator:
            from na_sso.mfa import account_has_mfa, begin_mfa_login
            if account_has_mfa(admin.id):
                return begin_mfa_login(admin)
        mfa_verified = not operator or not get_settings().file.admin_mfa_policy.required
        destination = (
            "/account/password-decision" if admin.password_decision_required
            else "/account/mfa" if operator and not mfa_verified
            else default_home(admin.role)
        )
        resp = RedirectResponse(destination, status_code=303)
        set_session_cookie(
            resp,
            account_id=admin.id,
            session_version=admin.session_version,
            mfa_verified=mfa_verified,
        )
        return resp
    return template_response(
        templates,
        request,
        "login.html",
        {"error": "Invalid credentials.", "error_title": "Sign-in failed"},
        status_code=401,
    )


@router.post("/logout")
async def logout():
    from na_sso.mfa import MFA_PENDING_COOKIE, TOTP_SETUP_COOKIE, WEBAUTHN_CHALLENGE_COOKIE
    resp = RedirectResponse("/login", status_code=303)
    for cookie in (COOKIE, MFA_PENDING_COOKIE, TOTP_SETUP_COOKIE, WEBAUTHN_CHALLENGE_COOKIE):
        resp.delete_cookie(cookie)
    return resp


def _password_form(request: Request, title: str, *, decision: bool = False, error: str = "") -> HTMLResponse:
    from na_sso.main import templates

    policy = get_settings().file.password_policy
    account = current_user(request)
    expired_decision = bool(
        decision and account and account["password_decision_kind"] == "expired"
    )
    limit = policy.expiry_acknowledgement_limit
    keep_count = account["password_keep_count"] if account else 0
    limit_reached = limit is not None and keep_count >= limit
    allow_keep = bool(
        expired_decision
        and policy.expiry_acknowledgement_mode != "disabled"
        and not limit_reached
    )
    from na_sso.models import utcnow
    keep_result_at = _acknowledgement_result(policy, utcnow()) if allow_keep else None
    if keep_result_at is None:
        allow_keep = False
    return template_response(templates, request, "password_form.html", {
        "title": title,
        "decision": decision,
        "expired_decision": expired_decision,
        "allow_keep": allow_keep,
        "keep_result_at": keep_result_at,
        "keep_count": keep_count,
        "keep_limit": limit,
        "keep_limit_reached": limit_reached,
        "error": error,
        "error_title": "Password not accepted",
        "policy": policy,
        "admin": account["username"] if account else None,
        "is_administrator": bool(account and any(permission_context(account["role"]).values())),
        "permissions": permission_context(account["role"]) if account else permission_context("user"),
        "home_url": default_home(account["role"]) if account else "/account",
    })


@router.get("/account")
async def account_page(request: Request):
    from na_sso.main import templates

    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["restricted"]:
        return RedirectResponse("/account/password-decision", status_code=303)
    from na_sso.db import get_session
    from na_sso.lifecycle import sync_state_payload
    from na_sso.models import ManagedUser, as_utc, utcnow
    settings = get_settings()
    with get_session() as db:
        stored = db.get(ManagedUser, account["id"])
        assigned_states = [
            state for state in stored.sync_states if state.assigned and not state.retired
        ]
        now = utcnow()
        ssh_keys = [{
            "id": key.id,
            "name": key.name,
            "fingerprint": key.fingerprint,
            "algorithm": key.algorithm,
            "enrolled_at": key.enrolled_at,
            "expires_at": key.expires_at,
            "last_used_at": key.last_used_at,
            "last_used_source": key.last_used_source,
            "revoked_at": key.revoked_at,
            "replaced_by_id": key.replaced_by_id,
            "status": (
                "revoked" if key.revoked_at else
                "expired" if key.expires_at and as_utc(key.expires_at) <= now else
                "active"
            ),
        } for key in sorted(stored.ssh_keys, key=lambda item: item.enrolled_at)]
        active_ssh_keys = [key for key in ssh_keys if key["status"] == "active"]
        ssh_fingerprint = active_ssh_keys[-1]["fingerprint"] if active_ssh_keys else None
    definitions = {target.id: target for target in settings.file.targets}
    access = []
    for state in assigned_states:
        target = definitions.get(state.target)
        target_type = target.type if target else (state.target_type or "target")
        if target_type == "ssh":
            mode = getattr(target, "mode", "password_and_key") if target else "password_and_key"
            credential_mode = {
                "password": "Password",
                "key": "SSH key",
                "password_and_key": "Password and SSH key",
            }.get(mode, "Password and SSH key")
        else:
            credential_mode = "Password"
        view = sync_state_payload(
            state.state,
            assigned=state.assigned,
            retired=state.retired,
            desired_action=stored.desired_action,
            next_retry_at=state.next_retry_at,
        )
        access.append({
            "id": state.target,
            "name": target.display_name if target else state.target,
            "type": target_type,
            "credential_mode": credential_mode,
            "view": view,
            "next_retry_at": state.next_retry_at,
            "needs_support": view["state"] in {
                "failed", "unsupported", "retired", "expired_disabled", "pending_expiry_disable"
            },
        })
    return template_response(templates, request, "account.html", {
        "account": account,
        "admin": account["username"],
        "home_url": default_home(account["role"]),
        "permissions": permission_context(account["role"]),
        "role_info": role_definition(account["role"]),
        "fallback_enabled": settings.file.ssh_key_policy.allow_server_fallback,
        "access": access,
        "support": settings.file.support_policy,
        "ssh_fingerprint": ssh_fingerprint,
        "ssh_keys": ssh_keys,
        "active_ssh_keys": active_ssh_keys,
        "ssh_key_policy": settings.file.ssh_key_policy,
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
async def password_decision(request: Request, choice: str = Form(...), current_password: str = Form(""), new_password: str = Form(""), confirm_password: str | None = Form(None), password_generated: str = Form("false"), credential_handoff_confirmed: str = Form("false")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return await _complete_password_action(request, account, choice, current_password, new_password, confirm_password, password_generated, credential_handoff_confirmed, decision=True)


@router.get("/account/password")
async def password_page(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return _password_form(request, "Change password")


@router.post("/account/password")
async def password_change(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str | None = Form(None), password_generated: str = Form("false"), credential_handoff_confirmed: str = Form("false"), choice: str = Form("change")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return await _complete_password_action(request, account, "change", current_password, new_password, confirm_password, password_generated, credential_handoff_confirmed, decision=False)


@router.post("/account/ssh-key/generate")
async def server_generate_ssh_key(request: Request):
    from na_sso.main import templates

    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    from na_sso.config import get_settings
    if account["role"] == "root" or not get_settings().file.ssh_key_policy.allow_server_fallback:
        return HTMLResponse("Server-handled fallback is disabled.", status_code=403)
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import generate_ssh_keypair
    from na_sso.ssh_keys import add_key
    private, public = generate_ssh_keypair()
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        add_key(
            db, user, name="Generated key", public_key=public, expires_on="",
            actor=account["username"],
        )
        from na_sso.audit import record_audit
        record_audit(db, user.username, "ssh_key.generated_once", user.username, "ed25519 server-handled non-persistent")
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    content = templates.get_template("private_key_once.html").render({
        "request": request,
        "admin": account["username"],
        "is_administrator": any(permission_context(account["role"]).values()),
        "permissions": permission_context(account["role"]),
        "home_url": default_home(account["role"]),
        "private_key": private,
    })
    return HTMLResponse(content, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _complete_password_action(request: Request, account: dict, choice: str, current_password: str, new_password: str, confirm_password: str | None, password_generated: str, credential_handoff_confirmed: str, *, decision: bool):
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
            policy = get_settings().file.password_policy
            limit = policy.expiry_acknowledgement_limit
            if policy.expiry_acknowledgement_mode == "disabled":
                return _password_form(
                    request,
                    "Accept or change your password",
                    decision=True,
                    error="Keeping an expired password is disabled by policy. Change it to continue.",
                )
            if limit is not None and user.password_keep_count >= limit:
                return _password_form(
                    request,
                    "Accept or change your password",
                    decision=True,
                    error="This password has reached its acknowledgement limit. Change it to continue.",
                )
            keep_result_at = _acknowledgement_result(policy, utcnow())
            if keep_result_at is None:
                return _password_form(
                    request,
                    "Accept or change your password",
                    decision=True,
                    error="The acknowledgement policy cannot calculate a new expiry. Change the password to continue.",
                )
            user.password_decision_required = False
            user.password_decision_kind = ""
            user.password_keep_until = keep_result_at
            user.password_keep_count += 1
            limit_label = str(limit) if limit is not None else "unlimited"
            record_audit(
                db,
                user.username,
                "password.keep_acknowledged",
                user.username,
                (
                    f"mode={policy.expiry_acknowledgement_mode}; "
                    f"next_expiry={keep_result_at.isoformat()}; "
                    f"acknowledgement={user.password_keep_count}/{limit_label}"
                ),
            )
        elif choice == "change":
            if password_generated == "true" and credential_handoff_confirmed != "true":
                return _password_form(
                    request,
                    "Accept or change your password" if decision else "Change password",
                    decision=decision,
                    error="Generate the password again, save the full value, and confirm the handoff before changing it.",
                )
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
            user.password_keep_until = None
            user.password_keep_count = 0
            user.session_version += 1
            record_audit(db, user.username, "password.changed", user.username)
        else:
            return _password_form(request, "Accept or change your password", decision=True, error="Choose keep or change.")
        db.commit()
        user_id = user.id
    from na_sso.sync import credential_handoff
    await credential_handoff(user_id, new_password if choice == "change" else current_password)
    response = redirect_with_feedback(
        "/login" if choice == "change" else "/account",
        title="Password changed" if choice == "change" else "Password kept",
        message=(
            "Sign in again with the new password. Target synchronization has started."
            if choice == "change"
            else (
                "The current password remains active until "
                f"{keep_result_at.strftime('%Y-%m-%d')}. The acknowledgement was recorded."
            )
        ),
    )
    if choice == "change":
        response.delete_cookie(COOKIE)
    return response
