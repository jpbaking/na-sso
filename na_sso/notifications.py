from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.config import NotificationPolicy, WebhookEndpoint, get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import (
    AuditEvent,
    ManagedUser,
    WebhookDelivery,
    WebhookEndpointState,
)
from na_sso.permissions import MANAGE_SECURITY, permission_context


router = APIRouter()
DELIVERY_STATUSES = frozenset({"pending", "retrying", "delivered", "failed", "disabled"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _endpoint_state(db, endpoint_id: str) -> WebhookEndpointState | None:
    return db.query(WebhookEndpointState).filter_by(endpoint_id=endpoint_id).one_or_none()


def _endpoint_active(db, endpoint: WebhookEndpoint) -> bool:
    state = _endpoint_state(db, endpoint.id)
    return endpoint.enabled and not (state and state.disabled)


def _render_email_notification(
    event_type: str,
    *,
    subject: str,
) -> tuple[str, str] | None:
    templates = {
        "lifecycle.completed": (
            "Your NA-SSO account is ready",
            f"Hello {subject},\n\n"
            "Your NA-SSO account has been provisioned or updated and is ready "
            "to use.",
        ),
        "password.expired": (
            "Your NA-SSO password has expired",
            f"Hello {subject},\n\n"
            "Your NA-SSO password has expired. Sign in and set a new password.",
        ),
        "approval.completed": (
            "Your NA-SSO access request was approved",
            f"Hello {subject},\n\nYour access request was approved.",
        ),
    }
    return templates.get(event_type)


def enqueue_notification(
    db,
    event_type: str,
    *,
    actor: str,
    subject: str,
    dedupe_key: str,
    operation_id: str | None = None,
    target_id: str | None = None,
    outcome: str | None = None,
) -> int:
    """Queue allowlisted webhook and email payloads without sensitive detail."""
    policy = get_settings().file.notification_policy
    if not policy.enabled:
        return 0
    endpoints = [
        endpoint for endpoint in policy.endpoints
        if event_type in endpoint.events and _endpoint_active(db, endpoint)
    ]
    event_id = str(uuid4())
    queued = 0
    for endpoint in endpoints:
        endpoint_key = f"{event_type}:{dedupe_key}"[:256]
        if db.query(WebhookDelivery).filter_by(
            endpoint_id=endpoint.id, dedupe_key=endpoint_key
        ).first():
            continue
        delivery_id = str(uuid4())
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "delivery_id": delivery_id,
            "event_type": event_type,
            "occurred_at_utc": _now().isoformat(),
            "actor": actor[:64],
            "subject": subject[:128],
            "operation_id": operation_id,
            "target_id": target_id,
            "outcome": outcome,
        }
        db.add(WebhookDelivery(
            id=delivery_id,
            endpoint_id=endpoint.id,
            event_type=event_type,
            dedupe_key=endpoint_key,
            payload=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            status="pending",
            next_attempt_at=_now(),
        ))
        queued += 1

    email_channel = policy.email_channel
    if (
        email_channel is None
        or not email_channel.enabled
        or event_type not in email_channel.events
    ):
        return queued
    email_content = _render_email_notification(event_type, subject=subject)
    if email_content is None:
        db.add(AuditEvent(
            actor=actor,
            action="email.skipped_no_template",
            subject=subject,
            detail=f"event={event_type}",
        ))
        return queued
    user = db.query(ManagedUser).filter_by(username=subject).one_or_none()
    if user is None or not user.email:
        db.add(AuditEvent(
            actor=actor,
            action="email.skipped_no_recipient",
            subject=subject,
            detail=f"event={event_type}",
        ))
        return queued
    email_key = f"{event_type}:{dedupe_key}:{user.email}"[:256]
    if db.query(WebhookDelivery).filter_by(
        endpoint_id="email", dedupe_key=email_key
    ).first():
        return queued
    email_subject, email_body = email_content
    db.add(WebhookDelivery(
        id=str(uuid4()),
        endpoint_id="email",
        channel="email",
        recipient=user.email,
        event_type=event_type,
        dedupe_key=email_key,
        payload=json.dumps(
            {"body": email_body, "subject": email_subject},
            separators=(",", ":"),
            sort_keys=True,
        ),
        status="pending",
        next_attempt_at=_now(),
    ))
    queued += 1
    return queued


def _signature(secret: str, timestamp: str, body: str) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"v1={digest}"


def _retry_at(policy: NotificationPolicy, attempt_count: int) -> datetime:
    seconds = min(
        policy.retry_base_seconds * (2 ** max(0, attempt_count - 1)),
        policy.retry_max_seconds,
    )
    return _now() + timedelta(seconds=seconds)


async def _deliver_email(db, delivery: WebhookDelivery, policy: NotificationPolicy) -> None:
    email_channel = policy.email_channel
    if email_channel is None or not email_channel.enabled:
        delivery.status = "disabled"
        delivery.next_attempt_at = None
        delivery.last_error = "destination disabled or no longer configured"
        db.commit()
        return

    try:
        from na_sso.email_delivery import send_email

        payload = json.loads(delivery.payload)
        if not delivery.recipient:
            raise ValueError("email delivery has no recipient")
        await send_email(
            email_channel,
            to=delivery.recipient,
            subject=payload["subject"],
            body=payload["body"],
        )
        succeeded = True
        failure = ""
    except Exception as error:
        succeeded = False
        failure = type(error).__name__

    delivery.attempt_count += 1
    if succeeded:
        delivery.status = "delivered"
        delivery.delivered_at = _now()
        delivery.next_attempt_at = None
        delivery.last_error = ""
        db.add(AuditEvent(
            actor="email-worker",
            action="email.delivered",
            subject=delivery.recipient or "",
            detail=(
                f"event={delivery.event_type}; delivery={delivery.id}; "
                f"attempt={delivery.attempt_count}"
            ),
        ))
    elif delivery.attempt_count >= policy.max_attempts:
        delivery.status = "failed"
        delivery.next_attempt_at = None
        delivery.last_error = failure[:300]
        db.add(AuditEvent(
            actor="email-worker",
            action="email.failed",
            subject=delivery.recipient or "",
            detail=(
                f"event={delivery.event_type}; delivery={delivery.id}; "
                f"attempts={delivery.attempt_count}; error={failure[:80]}"
            ),
        ))
    else:
        delivery.status = "retrying"
        delivery.next_attempt_at = _retry_at(policy, delivery.attempt_count)
        delivery.last_error = failure[:300]
    db.commit()


async def deliver_due_once(*, client: httpx.AsyncClient | None = None) -> int:
    policy = get_settings().file.notification_policy
    if not policy.enabled:
        return 0
    endpoints = {endpoint.id: endpoint for endpoint in policy.endpoints}
    now = _now()
    with get_session() as db:
        ids = [
            item.id for item in db.query(WebhookDelivery).filter(
                WebhookDelivery.status.in_(("pending", "retrying")),
                WebhookDelivery.next_attempt_at <= now,
            ).order_by(WebhookDelivery.created_at, WebhookDelivery.id).limit(100).all()
        ]
    owned_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    processed = 0
    try:
        for delivery_id in ids:
            with get_session() as db:
                delivery = db.get(WebhookDelivery, delivery_id)
                endpoint = endpoints.get(delivery.endpoint_id) if delivery else None
                if not delivery or delivery.status not in {"pending", "retrying"}:
                    continue
                if delivery.channel == "email":
                    await _deliver_email(db, delivery, policy)
                    processed += 1
                    continue
                if endpoint is None or not _endpoint_active(db, endpoint):
                    delivery.status = "disabled"
                    delivery.next_attempt_at = None
                    delivery.last_error = "destination disabled or no longer configured"
                    db.commit()
                    processed += 1
                    continue
                timestamp = str(int(_now().timestamp()))
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "NA-SSO-Webhook/1",
                    "X-NA-SSO-Event": delivery.event_type,
                    "X-NA-SSO-Delivery": delivery.id,
                    "X-NA-SSO-Timestamp": timestamp,
                    "X-NA-SSO-Signature": _signature(
                        endpoint.secret.get_secret_value(), timestamp, delivery.payload
                    ),
                }
                try:
                    response = await client.post(endpoint.url, content=delivery.payload, headers=headers)
                    succeeded = 200 <= response.status_code < 300
                    failure = "" if succeeded else f"HTTP {response.status_code}"
                except httpx.HTTPError as error:
                    succeeded = False
                    failure = type(error).__name__
                delivery.attempt_count += 1
                if succeeded:
                    delivery.status = "delivered"
                    delivery.delivered_at = _now()
                    delivery.next_attempt_at = None
                    delivery.last_error = ""
                    db.add(AuditEvent(
                        actor="webhook-worker", action="webhook.delivered",
                        subject=endpoint.id,
                        detail=f"event={delivery.event_type}; delivery={delivery.id}; attempt={delivery.attempt_count}",
                    ))
                elif delivery.attempt_count >= policy.max_attempts:
                    delivery.status = "failed"
                    delivery.next_attempt_at = None
                    delivery.last_error = failure[:300]
                    db.add(AuditEvent(
                        actor="webhook-worker", action="webhook.failed",
                        subject=endpoint.id,
                        detail=f"event={delivery.event_type}; delivery={delivery.id}; attempts={delivery.attempt_count}; error={failure[:80]}",
                    ))
                else:
                    delivery.status = "retrying"
                    delivery.next_attempt_at = _retry_at(policy, delivery.attempt_count)
                    delivery.last_error = failure[:300]
                db.commit()
                processed += 1
    finally:
        if owned_client:
            await client.aclose()
    return processed


async def notification_worker() -> None:
    while True:
        try:
            await deliver_due_once()
        except Exception:
            # Preserve the worker after transient database/network failures; individual
            # delivery errors are persisted without exposing response bodies.
            pass
        await asyncio.sleep(get_settings().file.notification_policy.delivery_scan_seconds)


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_SECURITY)


@router.get("/notifications")
async def notification_page(request: Request):
    from na_sso.main import templates

    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    policy = get_settings().file.notification_policy
    with get_session() as db:
        states = {
            state.endpoint_id: state
            for state in db.query(WebhookEndpointState).all()
        }
        deliveries = db.query(WebhookDelivery).order_by(
            WebhookDelivery.created_at.desc()
        ).limit(100).all()
    return template_response(templates, request, "notifications.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "policy": policy, "states": states, "deliveries": deliveries,
    })


@router.post("/notifications/endpoints/{endpoint_id}/toggle")
async def notification_endpoint_toggle(
    request: Request, endpoint_id: str, enabled: str = Form("false")
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    endpoint = next((
        item for item in get_settings().file.notification_policy.endpoints
        if item.id == endpoint_id
    ), None)
    if endpoint is None:
        return RedirectResponse("/notifications", status_code=303)
    active = enabled == "true"
    if active and not endpoint.enabled:
        return redirect_with_feedback(
            "/notifications", title="Enable unavailable",
            message="This destination is disabled by YAML configuration.", level="danger",
        )
    with get_session() as db:
        state = _endpoint_state(db, endpoint_id)
        if state is None:
            state = WebhookEndpointState(endpoint_id=endpoint_id)
            db.add(state)
        state.disabled = not active
        state.updated_by = principal["username"]
        if active:
            db.query(WebhookDelivery).filter_by(
                endpoint_id=endpoint_id, status="disabled"
            ).update({
                "status": "pending", "next_attempt_at": _now(), "last_error": ""
            }, synchronize_session=False)
        else:
            db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == endpoint_id,
                WebhookDelivery.status.in_(("pending", "retrying")),
            ).update({
                "status": "disabled", "next_attempt_at": None,
                "last_error": "destination disabled by operator",
            }, synchronize_session=False)
        db.add(AuditEvent(
            actor=principal["username"], action="webhook.enabled" if active else "webhook.disabled",
            subject=endpoint_id,
        ))
        db.commit()
    return redirect_with_feedback(
        "/notifications",
        title="Destination enabled" if active else "Destination disabled",
        message=f"{endpoint_id} will {'accept queued events' if active else 'receive no deliveries'}.",
    )


@router.post("/notifications/deliveries/{delivery_id}/retry")
async def notification_delivery_retry(request: Request, delivery_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        if not delivery or delivery.status not in {"failed", "disabled"}:
            return RedirectResponse("/notifications", status_code=303)
        if delivery.channel == "email":
            email_channel = get_settings().file.notification_policy.email_channel
            if email_channel is None or not email_channel.enabled:
                return redirect_with_feedback(
                    "/notifications", title="Retry unavailable",
                    message="Enable the configured destination before retrying.",
                    level="danger",
                )
            delivery.status = "pending"
            delivery.attempt_count = 0
            delivery.next_attempt_at = _now()
            delivery.last_error = ""
            db.add(AuditEvent(
                actor=principal["username"], action="email.retry_requested",
                subject=delivery.recipient or "",
                detail=f"delivery={delivery.id}",
            ))
            db.commit()
            return redirect_with_feedback(
                "/notifications", title="Delivery queued",
                message=f"Delivery {delivery_id[:8]} will be attempted again.",
            )
        endpoint = next((
            item for item in get_settings().file.notification_policy.endpoints
            if item.id == delivery.endpoint_id
        ), None)
        if endpoint is None or not _endpoint_active(db, endpoint):
            return redirect_with_feedback(
                "/notifications", title="Retry unavailable",
                message="Enable the configured destination before retrying.", level="danger",
            )
        delivery.status = "pending"
        delivery.attempt_count = 0
        delivery.next_attempt_at = _now()
        delivery.last_error = ""
        db.add(AuditEvent(
            actor=principal["username"], action="webhook.retry_requested",
            subject=delivery.endpoint_id, detail=f"delivery={delivery.id}",
        ))
        db.commit()
    return redirect_with_feedback(
        "/notifications", title="Delivery queued",
        message=f"Delivery {delivery_id[:8]} will be attempted again.",
    )
