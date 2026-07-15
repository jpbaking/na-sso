import hashlib
import hmac
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from na_sso.connectors.base import Connector, SyncResult
from na_sso.models import (
    AuditEvent,
    ManagedUser,
    SyncState,
    WebhookDelivery,
    WebhookEndpointState,
    utcnow,
)
from na_sso.notifications import deliver_due_once, enqueue_notification
from na_sso.security import encrypt_secret, hash_password


@pytest.fixture()
def notification_client(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.yaml"
    config_path.write_text("""
notification_policy:
  enabled: true
  persistent_failure_attempts: 1
  max_attempts: 2
  retry_base_seconds: 1
  retry_max_seconds: 2
  delivery_scan_seconds: 3600
  endpoints:
    - id: ops_hook
      url: http://localhost/hook
      secret: webhook-test-secret
      events: [sync.persistent_failure, password.expired, lifecycle.completed, approval.completed]
""")
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(tmp_path / "notifications.db"))
    monkeypatch.setenv("NA_SSO_SECRET_KEY", "notification-app-secret")
    monkeypatch.setenv("NA_SSO_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("NA_SSO_ADMIN_BOOTSTRAP_PASSWORD", "admin-pass")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    import na_sso.config as config
    import na_sso.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    from fastapi.testclient import TestClient
    from na_sso.main import app
    with TestClient(app) as client:
        yield client
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()


def _admin_login(client):
    response = client.post(
        "/login", data={"username": "admin", "password": "admin-pass"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_queue_is_preference_filtered_deduplicated_and_redacted(notification_client):
    from na_sso.db import get_session
    with get_session() as db:
        assert enqueue_notification(
            db, "sync.persistent_failure", actor="alice", subject="person-a",
            dedupe_key="operation-a:cloud", operation_id="operation-a",
            target_id="cloud", outcome="failed",
        ) == 1
        assert enqueue_notification(
            db, "sync.persistent_failure", actor="alice", subject="person-a",
            dedupe_key="operation-a:cloud", operation_id="operation-a",
            target_id="cloud", outcome="failed",
        ) == 0
        assert enqueue_notification(
            db, "unsupported.event", actor="alice", subject="person-a",
            dedupe_key="ignored",
        ) == 0
        db.commit()
        delivery = db.query(WebhookDelivery).one()
        payload = json.loads(delivery.payload)
        assert set(payload) == {
            "schema_version", "event_id", "delivery_id", "event_type",
            "occurred_at_utc", "actor", "subject", "operation_id",
            "target_id", "outcome",
        }
        assert payload["subject"] == "person-a" and payload["target_id"] == "cloud"
        assert "secret" not in delivery.payload.lower()
        assert delivery.status == "pending" and delivery.attempt_count == 0


@pytest.mark.asyncio
async def test_delivery_is_signed_retried_bounded_and_audited(notification_client):
    from na_sso.db import get_session
    with get_session() as db:
        enqueue_notification(
            db, "lifecycle.completed", actor="alice", subject="person-a",
            dedupe_key="operation-b:succeeded", operation_id="operation-b",
            outcome="succeeded",
        )
        db.commit()

    captured = []
    statuses = iter((503, 204))
    async def handler(request):
        body = request.content.decode()
        timestamp = request.headers["X-NA-SSO-Timestamp"]
        expected = "v1=" + hmac.new(
            b"webhook-test-secret", f"{timestamp}.{body}".encode(), hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(request.headers["X-NA-SSO-Signature"], expected)
        assert request.headers["X-NA-SSO-Event"] == "lifecycle.completed"
        captured.append(request)
        return httpx.Response(next(statuses), text="response body must not persist")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await deliver_due_once(client=client) == 1
        with get_session() as db:
            delivery = db.query(WebhookDelivery).one()
            assert delivery.status == "retrying" and delivery.attempt_count == 1
            assert delivery.last_error == "HTTP 503"
            assert "response body" not in delivery.last_error
            delivery.next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert await deliver_due_once(client=client) == 1
    with get_session() as db:
        delivery = db.query(WebhookDelivery).one()
        assert delivery.status == "delivered" and delivery.attempt_count == 2
        assert delivery.delivered_at is not None
        event = db.query(AuditEvent).filter_by(action="webhook.delivered").one()
        assert event.subject == "ops_hook" and delivery.id in event.detail
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_terminal_failure_omits_response_body_and_can_be_manually_requeued(
    notification_client
):
    from na_sso.db import get_session
    with get_session() as db:
        enqueue_notification(
            db, "approval.completed", actor="root", subject="bulk:one",
            dedupe_key="bulk:one", outcome="approved",
        )
        db.commit()
    async def handler(_request):
        return httpx.Response(500, text="token=should-never-be-stored")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await deliver_due_once(client=client)
        with get_session() as db:
            delivery = db.query(WebhookDelivery).one()
            delivery.next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()
        await deliver_due_once(client=client)
    with get_session() as db:
        delivery = db.query(WebhookDelivery).one()
        assert delivery.status == "failed" and delivery.last_error == "HTTP 500"
        delivery_id = delivery.id
        assert db.query(AuditEvent).filter_by(action="webhook.failed").count() == 1
    _admin_login(notification_client)
    retry = notification_client.post(
        f"/notifications/deliveries/{delivery_id}/retry", follow_redirects=False
    )
    assert retry.status_code == 303
    with get_session() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        assert delivery.status == "pending" and delivery.attempt_count == 0


def test_root_can_disable_destination_without_secret_disclosure(notification_client):
    from na_sso.db import get_session
    _admin_login(notification_client)
    page = notification_client.get("/notifications")
    assert page.status_code == 200
    assert "ops_hook" in page.text and "HMAC-SHA256" in page.text
    assert "webhook-test-secret" not in page.text
    with get_session() as db:
        enqueue_notification(
            db, "password.expired", actor="system", subject="person-a",
            dedupe_key="person-a:expiry", outcome="expired",
        )
        db.commit()
    disabled = notification_client.post(
        "/notifications/endpoints/ops_hook/toggle",
        data={"enabled": "false"}, follow_redirects=False,
    )
    assert disabled.status_code == 303
    with get_session() as db:
        state = db.query(WebhookEndpointState).one()
        delivery = db.query(WebhookDelivery).one()
        assert state.disabled and state.updated_by == "admin"
        assert delivery.status == "disabled"
        assert db.query(AuditEvent).filter_by(action="webhook.disabled").count() == 1
    notification_client.post(
        "/notifications/endpoints/ops_hook/toggle", data={"enabled": "true"}
    )
    with get_session() as db:
        assert db.query(WebhookEndpointState).one().disabled is False
        assert db.query(WebhookDelivery).one().status == "pending"


class FailingConnector(Connector):
    target_id = "cloud"
    target_type = "nextcloud"
    display_name = "Cloud"

    async def ensure_user(self, user, password):
        return SyncResult(False, "offline with sensitive upstream detail")

    async def disable_user(self, user):
        return SyncResult(False, "offline")

    async def delete_user(self, user):
        return SyncResult(False, "offline")

    async def probe(self):
        return SyncResult(False, "offline")


@pytest.mark.asyncio
async def test_failure_expiry_lifecycle_and_approval_sources_enqueue_events(
    notification_client, monkeypatch
):
    from na_sso.db import get_session
    from na_sso.sync import expire_due, sync_user
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [FailingConnector()])
    with get_session() as db:
        failing = ManagedUser(
            username="failing", password_hash=hash_password("V4lid!Failing-2026"),
            password_changed_at=utcnow(), pending_secret=encrypt_secret("not-exported"),
        )
        expired = ManagedUser(
            username="expired", password_hash=hash_password("V4lid!Expired-2026"),
            password_changed_at=utcnow() - timedelta(days=100),
            password_decision_required=False,
        )
        db.add_all([failing, expired])
        db.flush()
        db.add(SyncState(
            user_id=failing.id, target="cloud", target_type="nextcloud",
            assigned=True, state="pending",
        ))
        db.commit()
        failing_id, expired_id = failing.id, expired.id
    await sync_user(failing_id)
    assert await expire_due() == 1

    _admin_login(notification_client)
    executed = notification_client.post("/users/bulk/execute", data={
        "user_ids": expired_id, "action": "disable", "target_id": "",
        "replay_token": str(uuid4()),
    }, follow_redirects=False)
    assert executed.status_code == 303
    with get_session() as db:
        types = {item.event_type for item in db.query(WebhookDelivery).all()}
        assert {
            "sync.persistent_failure", "password.expired",
            "lifecycle.completed", "approval.completed",
        } <= types
        assert all("not-exported" not in item.payload for item in db.query(WebhookDelivery).all())
