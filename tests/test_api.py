from types import SimpleNamespace

from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
from na_sso.models import ManagedUser, SyncState
from na_sso.reconciliation import (
    InspectionCapabilities,
    RemoteIdentitySnapshot,
    compare_snapshot,
)


class ApiConnector(Connector):
    target_id = "cloud"
    target_type = "nextcloud"
    display_name = "Cloud access"
    capabilities = IdentityCapabilities(email=True, display_name=True, password=False)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True,
        memberships_exact=False,
    )

    def __init__(self):
        self.ensure_calls = 0
        self.probe_calls = 0

    async def ensure_user(self, user, password):
        self.ensure_calls += 1
        return SyncResult(True, "saved password=should-not-leak")

    async def disable_user(self, user):
        return SyncResult(True, "disabled")

    async def delete_user(self, user):
        return SyncResult(True, "deleted")

    async def probe(self):
        self.probe_calls += 1
        return SyncResult(True, "reachable token=should-not-leak")

    async def inspect_user(self, user):
        return compare_snapshot(
            target_id=self.target_id,
            target_name=self.display_name,
            user=user,
            capabilities=self.inspection_capabilities,
            snapshot=RemoteIdentitySnapshot(
                present=True,
                username=user.username,
                display_name="Drifted name",
                email=user.email,
                status="active",
                memberships=frozenset(),
            ),
        )


def _managed_user(username="api_user") -> int:
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username=username, display_name="API User", email=f"{username}@example.test",
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud",
            assigned=True, state="ok", detail="password=never-export",
        ))
        db.commit()
        return user.id


def _install_connector(monkeypatch, connector):
    monkeypatch.setattr("na_sso.api.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.reconcile.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])


def test_api_uses_json_auth_permission_validation_and_rate_limit_contract(
    client, admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.security import hash_password

    client.cookies.clear()
    unauthenticated = client.get("/api/v1", follow_redirects=False)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "authentication_required"
    assert unauthenticated.headers["x-ratelimit-limit"] == "120"

    admin_client.post("/login", data={"username": "admin", "password": "admin-pass"})
    index = admin_client.get("/api/v1", headers={"X-Request-ID": "api-contract-test"})
    assert index.status_code == 200
    assert index.json()["request_id"] == "api-contract-test"
    assert index.json()["data"]["api_version"] == "v1"

    invalid = admin_client.post("/api/v1/reconciliation/preview", json={})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert all("input" not in item for item in invalid.json()["error"]["details"])

    with get_session() as db:
        db.add(ManagedUser(
            username="operator", password_hash=hash_password("Operator-Pass!2026"),
            role="user_operator", password_decision_required=False,
        ))
        db.commit()
    admin_client.post("/logout")
    admin_client.post("/login", data={
        "username": "operator", "password": "Operator-Pass!2026",
    })
    assert admin_client.get("/api/v1/users").status_code == 200
    forbidden = admin_client.get("/api/v1/audit")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    policy = SimpleNamespace(
        enabled=True, requests_per_minute=10, max_page_size=100,
        idempotency_retention_hours=24,
    )
    monkeypatch.setattr(
        "na_sso.api_contract.get_settings",
        lambda: SimpleNamespace(file=SimpleNamespace(automation_api_policy=policy)),
    )
    from na_sso.api_contract import reset_api_rate_limits
    reset_api_rate_limits()
    for _ in range(10):
        assert admin_client.get("/api/v1").status_code == 200
    limited = admin_client.get("/api/v1")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert limited.headers["retry-after"]


def test_api_user_operation_and_audit_resources_are_paginated_and_redacted(admin_client):
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import LifecycleOperation, OperationTargetAttempt, utcnow

    user_id = _managed_user()
    with get_session() as db:
        operation = LifecycleOperation(
            user_id=user_id, command="update", status="failed", actor="admin",
            subject="api_user", total_targets=1, failed_targets=1,
            detail="password=operation-secret", started_at=utcnow(), completed_at=utcnow(),
        )
        db.add(operation)
        db.flush()
        db.add(OperationTargetAttempt(
            operation_id=operation.id, target="cloud", target_type="nextcloud",
            status="failed", result_state="failed", detail="token=attempt-secret",
        ))
        record_audit(
            db, "admin", "api.test", "api_user", "api_secret=audit-secret", operation.id,
        )
        db.commit()
        operation_id = operation.id

    users = admin_client.get("/api/v1/users?per_page=25&q=api_user")
    assert users.status_code == 200
    assert users.json()["meta"]["total"] == 1
    assert users.json()["data"][0]["summary"]["assigned_targets"] == 1
    detail = admin_client.get(f"/api/v1/users/{user_id}")
    assert detail.json()["data"]["targets"][0]["state"] == "ok"
    assert "never-export" not in detail.text
    assert "password_hash" not in detail.text and "pending_secret" not in detail.text

    operations = admin_client.get("/api/v1/operations?status=failed&per_page=25")
    assert operations.json()["meta"]["total"] == 1
    operation = admin_client.get(f"/api/v1/operations/{operation_id}")
    assert operation.status_code == 200
    assert "operation-secret" not in operation.text
    assert "attempt-secret" not in operation.text
    assert "[redacted]" in operation.text

    audit = admin_client.get("/api/v1/audit?action=api.test&per_page=25")
    assert audit.json()["meta"]["total"] == 1
    assert "audit-secret" not in audit.text and "[redacted]" in audit.text
    assert admin_client.get("/api/v1/users/99999").json()["error"]["code"] == "not_found"


def test_target_health_probe_is_correlated_redacted_and_idempotent(
    admin_client, monkeypatch,
):
    connector = ApiConnector()
    target = SimpleNamespace(
        id="cloud", type="nextcloud", display_name="Cloud access", enabled=True,
    )
    readiness = SimpleNamespace(
        configured=True, verified=True, reachable=True, failure_kind="",
        detail="reachable", revision=2, last_checked_at=None,
        last_success_at=None, next_probe_at=None,
    )
    monkeypatch.setattr("na_sso.api.target_definitions", lambda: [target])
    monkeypatch.setattr("na_sso.api.readiness_map", lambda: {"cloud": readiness})
    monkeypatch.setattr("na_sso.api.get_connectors", lambda: [connector])
    monkeypatch.setattr(
        "na_sso.connectors.base.build_unverified_connector", lambda target_id: connector,
    )
    monkeypatch.setattr("na_sso.target_credentials.record_probe", lambda *args: None)

    targets = admin_client.get("/api/v1/targets")
    assert targets.status_code == 200
    assert targets.json()["data"][0]["inspection_capabilities"]["memberships"] is True
    contract = targets.json()["data"][0]["connector_contract"]
    assert contract["version"] == "1.0" and contract["inspect"] and contract["dry_run"]
    assert contract["account_discovery"] is False
    assert "timeout" in contract["error_kinds"]
    assert "base_url" not in targets.text and "credential" not in targets.text.lower()

    payload = {"idempotency_key": "probe-cloud-0001"}
    first = admin_client.post("/api/v1/targets/cloud/probe", json=payload)
    replay = admin_client.post("/api/v1/targets/cloud/probe", json=payload)
    assert first.status_code == replay.status_code == 200
    assert replay.headers["idempotent-replay"] == "true"
    assert connector.probe_calls == 1
    assert "should-not-leak" not in first.text and "[redacted]" in first.text
    operation_id = first.json()["data"]["operation_id"]
    operation = admin_client.get(f"/api/v1/operations/{operation_id}")
    assert operation.json()["data"]["command"] == "target_probe"


def test_reconciliation_api_preview_approval_and_replay_share_operation_model(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import ApiIdempotencyRecord, LifecycleOperation, ReconciliationRun

    connector = ApiConnector()
    _install_connector(monkeypatch, connector)
    user_id = _managed_user("reconcile_api")
    preview_payload = {
        "idempotency_key": "reconcile-preview-0001",
        "user_id": user_id,
        "target_id": "cloud",
    }
    first = admin_client.post("/api/v1/reconciliation/preview", json=preview_payload)
    replay = admin_client.post("/api/v1/reconciliation/preview", json=preview_payload)
    assert first.status_code == replay.status_code == 201
    assert replay.headers["idempotent-replay"] == "true"
    run = first.json()["data"]
    assert run["drifted_targets"] == 1
    assert any(item["field"] == "display_name" and item["state"] == "drift" for item in run["findings"])

    approval = {
        "idempotency_key": "reconcile-approve-0001",
        "approval_token": run["approval_token"],
        "confirm_destructive": False,
    }
    approved = admin_client.post(f"/api/v1/reconciliation/{run['id']}/approve", json=approval)
    approved_replay = admin_client.post(
        f"/api/v1/reconciliation/{run['id']}/approve", json=approval
    )
    assert approved.status_code == approved_replay.status_code == 202
    assert approved_replay.headers["idempotent-replay"] == "true"
    detail = admin_client.get(f"/api/v1/reconciliation/{run['id']}")
    assert detail.json()["data"]["status"] == "completed"
    assert connector.ensure_calls == 1
    with get_session() as db:
        stored = db.get(ReconciliationRun, run["id"])
        assert db.query(LifecycleOperation).filter_by(parent_id=stored.operation_id).count() == 1
        assert db.query(ApiIdempotencyRecord).count() == 2

    conflict = admin_client.post("/api/v1/reconciliation/preview", json={
        **preview_payload, "target_id": None,
    })
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
