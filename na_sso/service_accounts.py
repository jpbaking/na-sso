"""Root-managed scoped service accounts and one-time Bearer credentials."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import ServiceAccount, ServiceAccountCredential, as_utc, utcnow
from na_sso.permissions import (
    MANAGE_SECURITY,
    MANAGE_TARGETS,
    MANAGE_USERS,
    VIEW_AUDIT,
    permission_context,
)


router = APIRouter(prefix="/service-accounts")
SERVICE_ACCOUNT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
SERVICE_ACCOUNT_PERMISSIONS = {
    MANAGE_USERS: "Users, bulk workflows, and reconciliation",
    MANAGE_TARGETS: "Target health and connection probes",
    VIEW_AUDIT: "Operations and audit records",
}
TOKEN_RE = re.compile(r"^nas_([a-f0-9]{12})_([A-Za-z0-9_-]{32,})$")


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_SECURITY)


def _token_hash(raw_token: str) -> str:
    key = hashlib.sha256(
        (get_settings().secret_key + ":service-account-token").encode()
    ).digest()
    return hmac.new(key, raw_token.encode(), hashlib.sha256).hexdigest()


def service_account_permissions(account: ServiceAccount) -> frozenset[str]:
    try:
        values = json.loads(account.permissions)
    except (TypeError, json.JSONDecodeError):
        return frozenset()
    return frozenset(item for item in values if item in SERVICE_ACCOUNT_PERMISSIONS)


def issue_service_account_credential(
    account_id: str,
    *,
    actor: str,
    label: str,
    expires_in_days: int,
) -> tuple[ServiceAccountCredential, str]:
    policy = get_settings().file.automation_api_policy
    clean_label = label.strip()
    if not clean_label or len(clean_label) > 80:
        raise ValueError("credential label is required and must be at most 80 characters")
    if not 1 <= expires_in_days <= policy.max_token_days:
        raise ValueError(f"credential lifetime must be 1–{policy.max_token_days} days")
    prefix = secrets.token_hex(6)
    raw_token = f"nas_{prefix}_{secrets.token_urlsafe(32)}"
    with get_session() as db:
        account = db.get(ServiceAccount, account_id)
        now = utcnow()
        if account is None or account.revoked_at is not None:
            raise ValueError("service account is unavailable")
        if account.expires_at and as_utc(account.expires_at) <= now:
            raise ValueError("service account has expired")
        expires_at = now + timedelta(days=expires_in_days)
        if account.expires_at and expires_at > as_utc(account.expires_at):
            expires_at = as_utc(account.expires_at)
        credential = ServiceAccountCredential(
            service_account_id=account.id,
            label=clean_label,
            token_prefix=prefix,
            token_hash=_token_hash(raw_token),
            created_by=actor,
            expires_at=expires_at,
        )
        db.add(credential)
        db.flush()
        record_audit(
            db, actor, "service_account.credential_issued", account.name,
            f"credential={credential.id}; prefix={prefix}; expires={expires_at.isoformat()}",
        )
        db.commit()
        db.expunge(credential)
        return credential, raw_token


def authenticate_service_account(raw_token: str) -> dict | None:
    match = TOKEN_RE.fullmatch(raw_token)
    if not match:
        return None
    prefix = match.group(1)
    supplied_hash = _token_hash(raw_token)
    with get_session() as db:
        credential = db.query(ServiceAccountCredential).filter_by(
            token_prefix=prefix
        ).one_or_none()
        if credential is None or not hmac.compare_digest(credential.token_hash, supplied_hash):
            return None
        account = db.get(ServiceAccount, credential.service_account_id)
        now = utcnow()
        if (
            account is None
            or account.revoked_at is not None
            or credential.revoked_at is not None
            or as_utc(credential.expires_at) <= now
            or (account.expires_at and as_utc(account.expires_at) <= now)
        ):
            return None
        if credential.last_used_at is None or as_utc(credential.last_used_at) < now - timedelta(minutes=5):
            credential.last_used_at = now
            db.commit()
        return {
            "id": account.id,
            "username": f"service:{account.name}",
            "role": "service_account",
            "permissions": service_account_permissions(account),
            "mfa_verified": True,
            "restricted": False,
            "principal_type": "service_account",
            "service_account_id": account.id,
            "credential_id": credential.id,
        }


def _parse_expiry(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("account expiry must use a valid UTC date and time") from error
    result = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    if result <= utcnow():
        raise ValueError("account expiry must be in the future")
    return result


@router.get("")
async def service_accounts_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        accounts = db.query(ServiceAccount).order_by(
            ServiceAccount.created_at.desc(), ServiceAccount.name
        ).limit(250).all()
        for account in accounts:
            account.credentials
    return template_response(templates, request, "service_accounts.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "accounts": accounts,
        "permission_options": SERVICE_ACCOUNT_PERMISSIONS,
    })


@router.post("")
async def service_account_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    permissions: list[str] = Form(default=[]),
    expires_at: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    clean_name = name.strip().lower()
    selected = sorted(set(permissions))
    try:
        if not SERVICE_ACCOUNT_NAME_RE.fullmatch(clean_name):
            raise ValueError("name must be 3–64 lowercase letters, digits, underscores, or hyphens")
        if len(description) > 500:
            raise ValueError("description must be at most 500 characters")
        if not selected or any(item not in SERVICE_ACCOUNT_PERMISSIONS for item in selected):
            raise ValueError("select at least one supported service-account capability")
        expiry = _parse_expiry(expires_at)
        with get_session() as db:
            if db.query(ServiceAccount).filter_by(name=clean_name).first():
                raise ValueError("service-account name already exists")
            account = ServiceAccount(
                name=clean_name,
                description=description.strip(),
                permissions=json.dumps(selected, separators=(",", ":")),
                created_by=principal["username"],
                expires_at=expiry,
            )
            db.add(account)
            db.flush()
            record_audit(
                db, principal["username"], "service_account.created", account.name,
                f"permissions={','.join(selected)}; expires={expiry.isoformat() if expiry else 'never'}",
            )
            db.commit()
            account_id = account.id
    except ValueError as error:
        return redirect_with_feedback(
            "/service-accounts", title="Service account rejected",
            message=str(error), level="danger",
        )
    return redirect_with_feedback(
        f"/service-accounts/{account_id}", title="Service account created",
        message="Issue an expiring credential before using this account.",
    )


@router.get("/{account_id}")
async def service_account_detail(request: Request, account_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        account = db.get(ServiceAccount, account_id)
        if account is None:
            return RedirectResponse("/service-accounts", status_code=303)
        account.credentials
    return template_response(templates, request, "service_account_detail.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "account": account,
        "account_permissions": service_account_permissions(account),
        "permission_options": SERVICE_ACCOUNT_PERMISSIONS,
        "default_token_days": get_settings().file.automation_api_policy.default_token_days,
        "max_token_days": get_settings().file.automation_api_policy.max_token_days,
    })


@router.post("/{account_id}/credentials")
async def service_account_issue(
    request: Request,
    account_id: str,
    label: str = Form(...),
    expires_in_days: int = Form(...),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    try:
        credential, raw_token = issue_service_account_credential(
            account_id,
            actor=principal["username"],
            label=label,
            expires_in_days=expires_in_days,
        )
    except ValueError as error:
        return redirect_with_feedback(
            f"/service-accounts/{account_id}", title="Credential not issued",
            message=str(error), level="danger",
        )
    from na_sso.main import templates
    response = template_response(templates, request, "service_account_token_once.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "account_id": account_id,
        "credential": credential,
        "raw_token": raw_token,
    })
    response.headers.update({
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    })
    return response


@router.post("/{account_id}/credentials/{credential_id}/revoke")
async def service_account_credential_revoke(
    request: Request, account_id: str, credential_id: str,
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        account = db.get(ServiceAccount, account_id)
        credential = db.get(ServiceAccountCredential, credential_id)
        if account is None or credential is None or credential.service_account_id != account.id:
            return RedirectResponse("/service-accounts", status_code=303)
        if credential.revoked_at is None:
            credential.revoked_at = utcnow()
            credential.revoked_by = principal["username"]
            record_audit(
                db, principal["username"], "service_account.credential_revoked",
                account.name, f"credential={credential.id}; prefix={credential.token_prefix}",
            )
            db.commit()
    return redirect_with_feedback(
        f"/service-accounts/{account_id}", title="Credential revoked",
        message="Requests using that Bearer credential are now rejected.",
    )


@router.post("/{account_id}/revoke")
async def service_account_revoke(request: Request, account_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        account = db.get(ServiceAccount, account_id)
        if account is None:
            return RedirectResponse("/service-accounts", status_code=303)
        if account.revoked_at is None:
            account.revoked_at = utcnow()
            account.revoked_by = principal["username"]
            for credential in account.credentials:
                if credential.revoked_at is None:
                    credential.revoked_at = account.revoked_at
                    credential.revoked_by = principal["username"]
            record_audit(
                db, principal["username"], "service_account.revoked", account.name,
                f"credentials={len(account.credentials)}",
            )
            db.commit()
    return redirect_with_feedback(
        f"/service-accounts/{account_id}", title="Service account revoked",
        message="All credentials for this account are now rejected.",
    )
