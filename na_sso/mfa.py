from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
from time import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import AdminMfa, AuditEvent, ManagedUser, WebAuthnCredential, utcnow
from na_sso.permissions import default_home, permission_context
from na_sso.security import decrypt_secret, encrypt_secret, verify_password


router = APIRouter()
MFA_PENDING_COOKIE = "na-sso-mfa-pending"
WEBAUTHN_CHALLENGE_COOKIE = "na-sso-webauthn-challenge"
TOTP_SETUP_COOKIE = "na-sso-totp-setup"
CHALLENGE_MAX_AGE = 300
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=salt)


def _operator(role: str) -> bool:
    return any(permission_context(role).values())


def _mfa_row(db, user_id: int, *, create: bool = False) -> AdminMfa | None:
    row = db.query(AdminMfa).filter_by(user_id=user_id).one_or_none()
    if row is None and create:
        row = AdminMfa(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def account_has_mfa(user_id: int) -> bool:
    with get_session() as db:
        row = _mfa_row(db, user_id)
        return bool(
            (row and row.totp_secret)
            or db.query(WebAuthnCredential).filter_by(user_id=user_id).first()
        )


def account_requires_mfa(user_id: int, role: str) -> bool:
    return bool(
        _operator(role)
        and (get_settings().file.admin_mfa_policy.required or account_has_mfa(user_id))
    )


def begin_mfa_login(account: ManagedUser) -> RedirectResponse:
    response = RedirectResponse("/login/mfa", status_code=303)
    response.set_cookie(
        MFA_PENDING_COOKIE,
        _serializer("mfa-pending").dumps({
            "id": account.id,
            "v": account.session_version,
            "auth_at": int(time()),
        }),
        max_age=CHALLENGE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=get_settings().session_cookie_secure,
    )
    return response


def _pending_account(request: Request) -> tuple[ManagedUser, dict] | None:
    token = request.cookies.get(MFA_PENDING_COOKIE)
    if not token:
        return None
    try:
        data = _serializer("mfa-pending").loads(token, max_age=CHALLENGE_MAX_AGE)
    except (BadSignature, KeyError, TypeError):
        return None
    with get_session() as db:
        account = db.get(ManagedUser, data.get("id"))
        if (
            not account or not _operator(account.role) or account.status != "active"
            or account.desired_action == "delete" or account.session_version != data.get("v")
        ):
            return None
        db.expunge(account)
    return account, data


def _finish_login(account: ManagedUser, authenticated_at: int) -> RedirectResponse:
    from na_sso.auth import set_session_cookie

    response = RedirectResponse(default_home(account.role), status_code=303)
    set_session_cookie(
        response,
        account_id=account.id,
        session_version=account.session_version,
        mfa_verified=True,
        authenticated_at=authenticated_at,
    )
    response.delete_cookie(MFA_PENDING_COOKIE)
    response.delete_cookie(WEBAUTHN_CHALLENGE_COOKIE)
    return response


def _finish_login_json(account: ManagedUser, authenticated_at: int) -> JSONResponse:
    from na_sso.auth import set_session_cookie

    response = JSONResponse({"ok": True, "redirect": default_home(account.role)})
    set_session_cookie(
        response,
        account_id=account.id,
        session_version=account.session_version,
        mfa_verified=True,
        authenticated_at=authenticated_at,
    )
    response.delete_cookie(MFA_PENDING_COOKIE)
    response.delete_cookie(WEBAUTHN_CHALLENGE_COOKIE)
    return response


def _totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp(secret: str, timestamp: int | None = None) -> str:
    counter = (timestamp or int(time())) // 30
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    digest = hmac.new(
        base64.b32decode(padded), struct.pack(">Q", counter), hashlib.sha1
    ).digest()
    offset = digest[-1] & 15
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, timestamp: int | None = None) -> bool:
    return matching_totp_counter(secret, code, timestamp) is not None


def matching_totp_counter(
    secret: str, code: str, timestamp: int | None = None
) -> int | None:
    now = timestamp or int(time())
    normalised = "".join(character for character in code if character.isdigit())
    if len(normalised) != 6:
        return None
    for offset in (-1, 0, 1):
        candidate_time = now + offset * 30
        if hmac.compare_digest(_totp(secret, candidate_time), normalised):
            return candidate_time // 30
    return None


def _recovery_hash(code: str) -> str:
    normalised = code.upper().replace("-", "").replace(" ", "")
    return hmac.new(
        get_settings().secret_key.encode(), normalised.encode(), hashlib.sha256
    ).hexdigest()


def _emergency_hash(code: str) -> str:
    normalised = code.upper().replace("-", "").replace(" ", "")
    return hashlib.sha256(normalised.encode()).hexdigest()


def _new_recovery_codes() -> tuple[list[str], list[str]]:
    codes = [
        "-".join(
            "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(4))
            for _ in range(3)
        )
        for _ in range(10)
    ]
    return codes, [_recovery_hash(code) for code in codes]


def _consume_recovery(db, account: ManagedUser, code: str) -> str | None:
    row = _mfa_row(db, account.id)
    if row:
        candidate = _recovery_hash(code)
        hashes = json.loads(row.recovery_code_hashes or "[]")
        for stored in hashes:
            if hmac.compare_digest(candidate, stored):
                hashes.remove(stored)
                row.recovery_code_hashes = json.dumps(hashes)
                return "recovery_code"
    configured = get_settings().root_recovery_code
    if account.role == "root" and configured:
        configured_value = configured.get_secret_value()
        candidate = _emergency_hash(code)
        configured_hash = _emergency_hash(configured_value)
        if hmac.compare_digest(candidate, configured_hash) and (
            not row or row.emergency_code_used_hash != configured_hash
        ):
            row = row or _mfa_row(db, account.id, create=True)
            row.emergency_code_used_hash = configured_hash
            return "root_emergency_code"
    return None


def _webauthn_context(request: Request) -> tuple[str, str]:
    policy = get_settings().file.admin_mfa_policy
    rp_id = policy.rp_id or request.url.hostname or "localhost"
    origin = policy.expected_origin or f"{request.url.scheme}://{request.url.netloc}"
    return rp_id, origin


def _fresh(account: dict) -> bool:
    minutes = get_settings().file.admin_mfa_policy.reauthentication_minutes
    return int(time()) - account["authenticated_at"] <= minutes * 60


def _current_operator(request: Request) -> dict | Response:
    from na_sso.auth import current_user

    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if not _operator(account["role"]):
        return Response("Forbidden", status_code=403)
    return account


def _mfa_page_context(account: dict, *, error: str = "") -> dict:
    policy = get_settings().file.admin_mfa_policy
    with get_session() as db:
        row = _mfa_row(db, account["id"])
        credentials = db.query(WebAuthnCredential).filter_by(
            user_id=account["id"]
        ).order_by(WebAuthnCredential.created_at).all()
        recovery_remaining = len(json.loads(row.recovery_code_hashes or "[]")) if row else 0
    return {
        "admin": account["username"],
        "account": account,
        "admin_area": True,
        "permissions": permission_context(account["role"]),
        "policy": policy,
        "totp_enrolled": bool(row and row.totp_secret),
        "credentials": credentials,
        "recovery_remaining": recovery_remaining,
        "fresh": _fresh(account),
        "error": error or None,
        "error_title": "MFA update not completed",
    }


@router.get("/login/mfa")
async def mfa_login_page(request: Request):
    from na_sso.main import templates

    pending = _pending_account(request)
    if not pending:
        return RedirectResponse("/login", status_code=303)
    account, _ = pending
    with get_session() as db:
        row = _mfa_row(db, account.id)
        passkeys = db.query(WebAuthnCredential).filter_by(user_id=account.id).count()
    return template_response(templates, request, "mfa_login.html", {
        "admin": None,
        "username": account.username,
        "totp": bool(row and row.totp_secret),
        "passkeys": passkeys,
        "root_recovery": bool(account.role == "root" and get_settings().root_recovery_code),
        "error": None,
    })


@router.post("/login/mfa/code")
async def mfa_login_code(request: Request, code: str = Form(...)):
    from na_sso.main import templates

    pending = _pending_account(request)
    if not pending:
        return RedirectResponse("/login", status_code=303)
    account, data = pending
    method = None
    with get_session() as db:
        row = _mfa_row(db, account.id)
        counter = (
            matching_totp_counter(decrypt_secret(row.totp_secret), code)
            if row and row.totp_secret else None
        )
        if counter is not None and counter > row.totp_last_counter:
            row.totp_last_counter = counter
            method = "totp"
        else:
            method = _consume_recovery(db, account, code)
        if method:
            db.add(AuditEvent(
                actor=account.username, action="mfa.login", subject=account.username,
                detail=f"method={method}",
            ))
            db.commit()
    if method:
        return _finish_login(account, data["auth_at"])
    with get_session() as db:
        passkeys = db.query(WebAuthnCredential).filter_by(user_id=account.id).count()
    return template_response(templates, request, "mfa_login.html", {
        "admin": None, "username": account.username,
        "totp": bool(row and row.totp_secret),
        "passkeys": passkeys,
        "root_recovery": bool(account.role == "root" and get_settings().root_recovery_code),
        "error": "The authenticator or recovery code was not accepted.",
    }, status_code=401)


@router.post("/login/mfa/webauthn/options")
async def mfa_authentication_options(request: Request):
    pending = _pending_account(request)
    if not pending:
        return JSONResponse({"error": "Login expired"}, status_code=401)
    account, _ = pending
    with get_session() as db:
        credentials = db.query(WebAuthnCredential).filter_by(user_id=account.id).all()
    if not credentials:
        return JSONResponse({"error": "No passkey is enrolled"}, status_code=409)
    rp_id, _ = _webauthn_context(request)
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
            for item in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    response = Response(options_to_json(options), media_type="application/json")
    response.set_cookie(
        WEBAUTHN_CHALLENGE_COOKIE,
        _serializer("webauthn-authentication").dumps({
            "id": account.id, "challenge": bytes_to_base64url(options.challenge),
        }),
        max_age=CHALLENGE_MAX_AGE, httponly=True, samesite="strict",
        secure=get_settings().session_cookie_secure,
    )
    return response


@router.post("/login/mfa/webauthn/verify")
async def mfa_authentication_verify(request: Request):
    pending = _pending_account(request)
    if not pending:
        return JSONResponse({"error": "Login expired"}, status_code=401)
    account, data = pending
    try:
        challenge = _serializer("webauthn-authentication").loads(
            request.cookies.get(WEBAUTHN_CHALLENGE_COOKIE, ""), max_age=CHALLENGE_MAX_AGE
        )
        payload = await request.json()
        credential_id = payload.get("id", "")
        with get_session() as db:
            credential = db.query(WebAuthnCredential).filter_by(
                user_id=account.id, credential_id=credential_id
            ).one()
            rp_id, origin = _webauthn_context(request)
            verified = verify_authentication_response(
                credential=payload,
                expected_challenge=base64url_to_bytes(challenge["challenge"]),
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=base64url_to_bytes(credential.public_key),
                credential_current_sign_count=credential.sign_count,
                require_user_verification=True,
            )
            credential.sign_count = verified.new_sign_count
            credential.last_used_at = utcnow()
            db.add(AuditEvent(
                actor=account.username, action="mfa.login", subject=account.username,
                detail=f"method=webauthn; credential={credential.id}",
            ))
            db.commit()
    except (
        BadSignature, KeyError, TypeError, ValueError, WebAuthnException,
        NoResultFound, IntegrityError,
    ):
        # SQLAlchemy lookup failures and malformed browser payloads are equally non-specific.
        return JSONResponse({"error": "Passkey verification failed"}, status_code=401)
    return _finish_login_json(account, data["auth_at"])


@router.get("/account/mfa")
async def mfa_account_page(request: Request):
    from na_sso.main import templates

    account = _current_operator(request)
    if isinstance(account, Response):
        return account
    return template_response(
        templates, request, "mfa_account.html", _mfa_page_context(account)
    )


@router.post("/account/mfa/reauth")
async def mfa_reauthenticate(request: Request, password: str = Form(...)):
    account = _current_operator(request)
    if isinstance(account, Response):
        return account
    with get_session() as db:
        stored = db.get(ManagedUser, account["id"])
        valid = bool(stored and stored.password_hash and verify_password(password, stored.password_hash))
    if not valid:
        from na_sso.main import templates
        return template_response(
            templates, request, "mfa_account.html",
            _mfa_page_context(account, error="Current password was not accepted."),
            status_code=401,
        )
    from na_sso.auth import set_session_cookie
    response = redirect_with_feedback(
        "/account/mfa", title="Identity confirmed",
        message="Sensitive MFA changes are unlocked for a short time.",
    )
    set_session_cookie(
        response, account_id=account["id"], session_version=account["session_version"],
        mfa_verified=account["mfa_verified"], authenticated_at=int(time()),
    )
    return response


def _require_fresh(request: Request) -> dict | Response:
    account = _current_operator(request)
    if isinstance(account, Response):
        return account
    if not _fresh(account):
        return redirect_with_feedback(
            "/account/mfa", title="Reauthentication required",
            message="Confirm your current password before changing MFA.", level="danger",
        )
    return account


@router.post("/account/mfa/totp/start")
async def mfa_totp_start(request: Request):
    from na_sso.main import templates

    account = _require_fresh(request)
    if isinstance(account, Response):
        return account
    policy = get_settings().file.admin_mfa_policy
    if "totp" not in policy.allowed_methods:
        return Response("TOTP is disabled by policy", status_code=403)
    secret = _totp_secret()
    uri = (
        f"otpauth://totp/{quote(policy.issuer)}:{quote(account['username'])}"
        f"?secret={secret}&issuer={quote(policy.issuer)}&algorithm=SHA1&digits=6&period=30"
    )
    response = template_response(templates, request, "mfa_totp_setup.html", {
        "admin": account["username"], "admin_area": True,
        "permissions": permission_context(account["role"]),
        "secret": secret, "uri": uri, "error": None,
    })
    response.set_cookie(
        TOTP_SETUP_COOKIE,
        _serializer("totp-setup").dumps({"id": account["id"], "secret": secret}),
        max_age=CHALLENGE_MAX_AGE, httponly=True, samesite="strict",
        secure=get_settings().session_cookie_secure,
    )
    return response


@router.post("/account/mfa/totp/confirm")
async def mfa_totp_confirm(request: Request, code: str = Form(...)):
    from na_sso.main import templates

    account = _require_fresh(request)
    if isinstance(account, Response):
        return account
    try:
        setup = _serializer("totp-setup").loads(
            request.cookies.get(TOTP_SETUP_COOKIE, ""), max_age=CHALLENGE_MAX_AGE
        )
        counter = matching_totp_counter(setup["secret"], code)
        if setup["id"] != account["id"] or counter is None:
            raise ValueError
    except (BadSignature, KeyError, TypeError, ValueError):
        return redirect_with_feedback(
            "/account/mfa", title="TOTP not enrolled",
            message="The setup expired or the authenticator code was invalid. Start again.",
            level="danger",
        )
    with get_session() as db:
        row = _mfa_row(db, account["id"], create=True)
        first_method = not row.totp_secret and not db.query(WebAuthnCredential).filter_by(
            user_id=account["id"]
        ).first()
        row.totp_secret = encrypt_secret(setup["secret"])
        row.totp_last_counter = counter
        codes = []
        if first_method or not json.loads(row.recovery_code_hashes or "[]"):
            codes, hashes = _new_recovery_codes()
            row.recovery_code_hashes = json.dumps(hashes)
        db.add(AuditEvent(
            actor=account["username"], action="mfa.totp.enrolled",
            subject=account["username"],
        ))
        db.commit()
    response = template_response(templates, request, "mfa_recovery_codes.html", {
        "admin": account["username"], "admin_area": True,
        "permissions": permission_context(account["role"]), "codes": codes,
        "title": "TOTP enrolled", "message": "Save these one-time recovery codes now.",
    })
    from na_sso.auth import set_session_cookie
    set_session_cookie(
        response, account_id=account["id"], session_version=account["session_version"],
        mfa_verified=True, authenticated_at=account["authenticated_at"],
    )
    response.delete_cookie(TOTP_SETUP_COOKIE)
    return response


@router.post("/account/mfa/webauthn/options")
async def mfa_registration_options(request: Request):
    account = _require_fresh(request)
    if isinstance(account, Response):
        return JSONResponse({"error": "Reauthentication required"}, status_code=401)
    policy = get_settings().file.admin_mfa_policy
    if "webauthn" not in policy.allowed_methods:
        return JSONResponse({"error": "Passkeys are disabled by policy"}, status_code=403)
    with get_session() as db:
        existing = db.query(WebAuthnCredential).filter_by(user_id=account["id"]).all()
    rp_id, _ = _webauthn_context(request)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=policy.issuer,
        user_id=str(account["id"]).encode(),
        user_name=account["username"],
        user_display_name=account["display_name"] or account["username"],
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
            for item in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    response = Response(options_to_json(options), media_type="application/json")
    response.set_cookie(
        WEBAUTHN_CHALLENGE_COOKIE,
        _serializer("webauthn-registration").dumps({
            "id": account["id"], "challenge": bytes_to_base64url(options.challenge),
        }),
        max_age=CHALLENGE_MAX_AGE, httponly=True, samesite="strict",
        secure=get_settings().session_cookie_secure,
    )
    return response


@router.post("/account/mfa/webauthn/verify")
async def mfa_registration_verify(request: Request):
    account = _require_fresh(request)
    if isinstance(account, Response):
        return JSONResponse({"error": "Reauthentication required"}, status_code=401)
    try:
        challenge = _serializer("webauthn-registration").loads(
            request.cookies.get(WEBAUTHN_CHALLENGE_COOKIE, ""), max_age=CHALLENGE_MAX_AGE
        )
        if challenge["id"] != account["id"]:
            raise ValueError
        payload = await request.json()
        credential_payload = payload.get("credential", payload)
        rp_id, origin = _webauthn_context(request)
        verified = verify_registration_response(
            credential=credential_payload,
            expected_challenge=base64url_to_bytes(challenge["challenge"]),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
        name = str(payload.get("name", "Passkey")).strip()[:80] or "Passkey"
        transports = credential_payload.get("response", {}).get("transports", [])
        with get_session() as db:
            first_method = not account_has_mfa(account["id"])
            db.add(WebAuthnCredential(
                user_id=account["id"], credential_id=bytes_to_base64url(verified.credential_id),
                name=name, public_key=bytes_to_base64url(verified.credential_public_key),
                sign_count=verified.sign_count,
                transports=json.dumps([str(item)[:32] for item in transports[:10]]),
                backed_up=verified.credential_backed_up,
            ))
            row = _mfa_row(db, account["id"], create=True)
            codes = []
            if first_method or not json.loads(row.recovery_code_hashes or "[]"):
                codes, hashes = _new_recovery_codes()
                row.recovery_code_hashes = json.dumps(hashes)
            db.add(AuditEvent(
                actor=account["username"], action="mfa.webauthn.enrolled",
                subject=account["username"], detail=f"name={name}",
            ))
            db.commit()
    except (
        BadSignature, KeyError, TypeError, ValueError, WebAuthnException,
        NoResultFound, IntegrityError,
    ):
        return JSONResponse({"error": "Passkey enrolment failed"}, status_code=400)
    from na_sso.auth import set_session_cookie
    response = JSONResponse({"ok": True, "recovery_codes": codes})
    set_session_cookie(
        response, account_id=account["id"], session_version=account["session_version"],
        mfa_verified=True, authenticated_at=account["authenticated_at"],
    )
    response.delete_cookie(WEBAUTHN_CHALLENGE_COOKIE)
    return response


def _method_count(db, user_id: int) -> int:
    row = _mfa_row(db, user_id)
    return int(bool(row and row.totp_secret)) + db.query(WebAuthnCredential).filter_by(
        user_id=user_id
    ).count()


def _can_revoke(db, user_id: int) -> bool:
    return not get_settings().file.admin_mfa_policy.required or _method_count(db, user_id) > 1


@router.post("/account/mfa/totp/revoke")
async def mfa_totp_revoke(request: Request):
    account = _require_fresh(request)
    if isinstance(account, Response):
        return account
    with get_session() as db:
        row = _mfa_row(db, account["id"])
        if not row or not row.totp_secret:
            return RedirectResponse("/account/mfa", status_code=303)
        if not _can_revoke(db, account["id"]):
            return redirect_with_feedback(
                "/account/mfa", title="TOTP retained",
                message="Enrol another method before removing the final required factor.",
                level="danger",
            )
        row.totp_secret = None
        if _method_count(db, account["id"]) == 0:
            row.recovery_code_hashes = "[]"
        db.add(AuditEvent(actor=account["username"], action="mfa.totp.revoked", subject=account["username"]))
        db.commit()
    return redirect_with_feedback(
        "/account/mfa", title="TOTP removed", message="The authenticator secret was revoked."
    )


@router.post("/account/mfa/webauthn/{credential_id}/revoke")
async def mfa_passkey_revoke(request: Request, credential_id: int):
    account = _require_fresh(request)
    if isinstance(account, Response):
        return account
    with get_session() as db:
        credential = db.query(WebAuthnCredential).filter_by(
            id=credential_id, user_id=account["id"]
        ).one_or_none()
        if not credential:
            return RedirectResponse("/account/mfa", status_code=303)
        if not _can_revoke(db, account["id"]):
            return redirect_with_feedback(
                "/account/mfa", title="Passkey retained",
                message="Enrol another method before removing the final required factor.",
                level="danger",
            )
        name = credential.name
        db.delete(credential)
        db.flush()
        row = _mfa_row(db, account["id"])
        if row and _method_count(db, account["id"]) == 0:
            row.recovery_code_hashes = "[]"
        db.add(AuditEvent(
            actor=account["username"], action="mfa.webauthn.revoked",
            subject=account["username"], detail=f"name={name}",
        ))
        db.commit()
    return redirect_with_feedback(
        "/account/mfa", title="Passkey removed", message=f"{name} was revoked."
    )


@router.post("/account/mfa/recovery/regenerate")
async def mfa_recovery_regenerate(request: Request):
    from na_sso.main import templates

    account = _require_fresh(request)
    if isinstance(account, Response):
        return account
    if not account["mfa_verified"] or not account_has_mfa(account["id"]):
        return Response("Complete MFA before regenerating recovery codes", status_code=403)
    codes, hashes = _new_recovery_codes()
    with get_session() as db:
        row = _mfa_row(db, account["id"], create=True)
        row.recovery_code_hashes = json.dumps(hashes)
        db.add(AuditEvent(
            actor=account["username"], action="mfa.recovery.regenerated",
            subject=account["username"],
        ))
        db.commit()
    return template_response(templates, request, "mfa_recovery_codes.html", {
        "admin": account["username"], "admin_area": True,
        "permissions": permission_context(account["role"]), "codes": codes,
        "title": "Recovery codes replaced",
        "message": "All previous recovery codes are invalid. Save this new set now.",
    })
