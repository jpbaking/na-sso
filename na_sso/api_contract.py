"""Shared versioned API authentication, errors, rate limits, and idempotency."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
import hashlib
import json
import re
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from na_sso.auth import current_user
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.models import ApiIdempotencyRecord, utcnow
from na_sso.permissions import has_permission


API_VERSION = "v1"
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_rate_lock = Lock()
_rate_windows: dict[str, deque[float]] = defaultdict(deque)


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "api_request_id", None)
    if existing:
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    value = supplied if REQUEST_ID_RE.fullmatch(supplied) else str(uuid4())
    request.state.api_request_id = value
    return value


def api_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: object | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        {
            "api_version": API_VERSION,
            "request_id": _request_id(request),
            "error": error,
        },
        status_code=status_code,
        headers={**getattr(request.state, "api_rate_headers", {}), **(headers or {})},
    )


def api_response(
    request: Request,
    data: object,
    *,
    status_code: int = 200,
    meta: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = {
        "api_version": API_VERSION,
        "request_id": _request_id(request),
        "data": data,
    }
    if meta is not None:
        payload["meta"] = meta
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={**getattr(request.state, "api_rate_headers", {}), **(headers or {})},
    )


def _rate_limit(request: Request, identity: str) -> JSONResponse | None:
    policy = get_settings().file.automation_api_policy
    now = monotonic()
    with _rate_lock:
        window = _rate_windows[identity]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= policy.requests_per_minute:
            retry_after = max(1, int(60 - (now - window[0])))
            request.state.api_rate_headers = {
                "X-RateLimit-Limit": str(policy.requests_per_minute),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(retry_after),
            }
            return api_error(
                request, 429, "rate_limit_exceeded",
                "The API request rate limit has been reached.",
            )
        window.append(now)
        request.state.api_rate_headers = {
            "X-RateLimit-Limit": str(policy.requests_per_minute),
            "X-RateLimit-Remaining": str(policy.requests_per_minute - len(window)),
        }
    return None


def reset_api_rate_limits() -> None:
    """Test and process-lifecycle hook; rate windows are intentionally not durable."""
    with _rate_lock:
        _rate_windows.clear()


def api_guard(request: Request, permission: str | None = None) -> dict | JSONResponse:
    if not get_settings().file.automation_api_policy.enabled:
        return api_error(request, 503, "api_disabled", "The automation API is disabled.")
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        from na_sso.service_accounts import authenticate_service_account
        account = authenticate_service_account(authorization[7:].strip())
    else:
        account = current_user(request)
    identity = (
        f"{account.get('principal_type', 'account')}:{account['id']}"
        if account else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    limited = _rate_limit(request, identity)
    if limited:
        return limited
    if not account:
        return api_error(
            request, 401, "authentication_required", "API authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if permission and not principal_has_permission(account, permission):
        return api_error(request, 403, "forbidden", "The authenticated principal lacks this capability.")
    if account.get("principal_type") != "service_account":
        from na_sso.mfa import account_requires_mfa
        if account_requires_mfa(account["id"], account["role"]) and not account["mfa_verified"]:
            return api_error(request, 403, "mfa_required", "Complete administrator MFA before using the API.")
    return account


def principal_has_permission(principal: dict, permission: str) -> bool:
    if principal.get("principal_type") == "service_account":
        return permission in principal.get("permissions", ())
    return has_permission(principal["role"], permission)


def page_meta(*, page: int, per_page: int, total: int, pages: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_previous": page > 1,
        "has_next": page < pages,
    }


def request_fingerprint(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def begin_idempotent_request(
    request: Request,
    *,
    actor: str,
    idempotency_key: str,
    payload: object,
) -> tuple[ApiIdempotencyRecord | None, JSONResponse | None]:
    if not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
        return None, api_error(
            request, 422, "invalid_idempotency_key",
            "Idempotency keys must contain 8–128 safe characters.",
        )
    fingerprint = request_fingerprint(payload)
    with get_session() as db:
        now = utcnow()
        db.query(ApiIdempotencyRecord).filter(
            ApiIdempotencyRecord.expires_at <= now
        ).delete(synchronize_session=False)
        existing = db.query(ApiIdempotencyRecord).filter_by(
            actor=actor,
            method=request.method,
            path=request.url.path,
            idempotency_key=idempotency_key,
        ).one_or_none()
        if existing:
            if existing.request_hash != fingerprint:
                return None, api_error(
                    request, 409, "idempotency_conflict",
                    "This idempotency key was already used with a different request.",
                )
            if existing.response_status is None or existing.response_body is None:
                return None, api_error(
                    request, 409, "request_in_progress",
                    "The matching idempotent request is still in progress.",
                )
            body = json.loads(existing.response_body)
            return None, JSONResponse(
                body,
                status_code=existing.response_status,
                headers={
                    **getattr(request.state, "api_rate_headers", {}),
                    "Idempotent-Replay": "true",
                },
            )
        record = ApiIdempotencyRecord(
            actor=actor,
            method=request.method,
            path=request.url.path,
            idempotency_key=idempotency_key,
            request_hash=fingerprint,
            expires_at=now + timedelta(
                hours=get_settings().file.automation_api_policy.idempotency_retention_hours
            ),
        )
        db.add(record)
        db.commit()
        db.expunge(record)
        return record, None


def finish_idempotent_request(
    record_id: str,
    response: JSONResponse,
    *,
    operation_id: str | None = None,
) -> JSONResponse:
    body = bytes(response.body).decode()
    with get_session() as db:
        record = db.get(ApiIdempotencyRecord, record_id)
        if record:
            record.response_status = response.status_code
            record.response_body = body
            record.operation_id = operation_id
            db.commit()
    return response
