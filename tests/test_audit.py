import csv
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from na_sso.audit_query import AuditParams, query_audit, safe_detail
from na_sso.audit_retention import enforce_audit_retention
from na_sso.config import AuditPolicy
from na_sso.models import AuditEvent, LifecycleOperation, OperationTargetAttempt


def _audit_fixture(client):
    from na_sso.db import get_session
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    with get_session() as db:
        succeeded = LifecycleOperation(
            command="update", status="succeeded", actor="alice", subject="person-a",
            total_targets=1, completed_targets=1, started_at=now, completed_at=now,
        )
        failed = LifecycleOperation(
            command="update", status="partially_failed", actor="bob", subject="person-b",
            total_targets=2, completed_targets=1, failed_targets=1,
            started_at=now, completed_at=now,
        )
        db.add_all([succeeded, failed])
        db.flush()
        db.add_all([
            OperationTargetAttempt(
                operation_id=succeeded.id, target="cloud", target_type="nextcloud",
                attempt_number=1, status="succeeded", result_state="ok", detail="saved", started_at=now,
            ),
            OperationTargetAttempt(
                operation_id=failed.id, target="shell", target_type="ssh",
                attempt_number=1, status="failed", result_state="failed", detail="offline", started_at=now,
            ),
            AuditEvent(at=now, actor="alice", action="user.update", subject="person-a", detail="target=cloud", operation_id=succeeded.id),
            AuditEvent(at=now + timedelta(minutes=1), actor="bob", action="sync.ensure", subject="person-b", detail="target=shell offline", operation_id=failed.id),
            AuditEvent(at=now + timedelta(minutes=2), actor="system", action="password.expired", subject="person-c", detail=""),
        ])
        db.commit()
        return succeeded.id, failed.id


def test_audit_params_are_bounded_and_stable():
    params = AuditParams.parse({
        "date_from": "bad", "date_to": "2026-07-15", "outcome": "bad",
        "actor": "a" * 80, "page": "-2", "per_page": "999",
    })
    assert params.date_from == "" and params.date_to == "2026-07-15"
    assert params.outcome == "all" and len(params.actor) == 64
    assert params.page == 1 and params.per_page == 100
    assert "date_to=2026-07-15" in params.url(page=2)
    assert "page=2&per_page=100" in params.url(page=2)


def test_audit_query_filters_correlation_target_outcome_and_dates(client):
    succeeded_id, failed_id = _audit_fixture(client)
    from na_sso.db import get_session
    with get_session() as db:
        assert query_audit(db, AuditParams()).total == 3
        assert [item.event.subject for item in query_audit(
            db, AuditParams(actor="alice")
        ).items] == ["person-a"]
        assert [item.event.subject for item in query_audit(
            db, AuditParams(target="shell")
        ).items] == ["person-b"]
        assert query_audit(db, AuditParams(operation=succeeded_id[:8])).items[0].operation.id == succeeded_id
        failed = query_audit(db, AuditParams(outcome="failed"))
        assert len(failed.items) == 1 and failed.items[0].operation.id == failed_id
        assert failed.items[0].outcome == "failed"
        assert query_audit(db, AuditParams(outcome="uncorrelated")).items[0].event.subject == "person-c"
        day = query_audit(db, AuditParams(date_from="2026-07-15", date_to="2026-07-15"))
        assert day.total == 3


def test_audit_page_and_operation_drilldown_are_investigable(admin_client):
    succeeded_id, _ = _audit_fixture(admin_client)
    page = admin_client.get("/audit?actor=alice&target=cloud&outcome=succeeded")
    assert page.status_code == 200
    assert 'aria-label="Filter audit events"' in page.text
    assert "Created managed account" not in page.text
    assert "Updated managed account" in page.text and "user.update" in page.text
    assert "all times UTC" in page.text and "Show detail" in page.text
    assert f'href="/audit/operations/{succeeded_id}"' in page.text
    assert 'class="inventory-cards stack-2" role="list"' in page.text

    detail = admin_client.get(f"/audit/operations/{succeeded_id}")
    assert detail.status_code == 200
    assert succeeded_id in detail.text
    assert "Target attempts" in detail.text and "cloud" in detail.text
    assert "Audit events" in detail.text and "user.update" in detail.text
    assert "UTC" in detail.text


def test_audit_export_requires_authentication(client):
    denied = client.get("/audit/export.json", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"


def test_audit_exports_are_filtered_bounded_and_redacted(admin_client, monkeypatch):
    from na_sso.db import get_session

    with get_session() as db:
        for index in range(30):
            db.add(AuditEvent(
                actor="alice" if index < 28 else "bob",
                action="user.update",
                subject=f"person-{index:02d}",
                detail="password=hunter2 token:abc123" if index == 0 else "safe detail",
            ))
        db.commit()
    monkeypatch.setattr(
        "na_sso.audit.get_settings",
        lambda: SimpleNamespace(file=SimpleNamespace(
            audit_policy=AuditPolicy(export_page_size=25)
        )),
    )

    result = admin_client.get("/audit/export.json?actor=alice&page=1")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert 'filename="na-sso-audit-1.json"' in result.headers["content-disposition"]
    body = result.json()
    assert body["total"] == 28
    assert body["per_page"] == 25
    assert body["pages"] == 2
    assert len(body["events"]) == 25
    assert all(row["actor"] == "alice" for row in body["events"])
    assert "hunter2" not in result.text and "abc123" not in result.text

    csv_result = admin_client.get("/audit/export.csv?actor=bob")
    rows = list(csv.DictReader(io.StringIO(csv_result.text)))
    assert len(rows) == 2
    assert {row["actor"] for row in rows} == {"bob"}
    with get_session() as db:
        assert db.query(AuditEvent).filter_by(action="audit.exported").count() == 2


def test_audit_retention_removes_only_expired_events_and_can_be_disabled(client):
    from na_sso.db import get_session

    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    with get_session() as db:
        operation = LifecycleOperation(
            id="retained-operation", command="ensure", status="succeeded",
            actor="alice", subject="person-a",
        )
        db.add(operation)
        db.add(OperationTargetAttempt(
            operation_id=operation.id, target="cloud", status="succeeded",
            result_state="success",
        ))
        db.add_all([
            AuditEvent(
                at=now - timedelta(days=31), actor="alice", action="user.update",
                subject="old", operation_id=operation.id,
            ),
            AuditEvent(
                at=now - timedelta(days=2), actor="alice", action="user.update",
                subject="recent", operation_id=operation.id,
            ),
        ])
        db.commit()
        assert enforce_audit_retention(
            db, AuditPolicy(retention_days=None), now=now
        ) == 0
        assert enforce_audit_retention(
            db, AuditPolicy(retention_days=30), now=now
        ) == 1
        assert [event.subject for event in db.query(AuditEvent).all()] == ["recent"]
        assert db.get(LifecycleOperation, operation.id) is not None
        assert db.query(OperationTargetAttempt).filter_by(
            operation_id=operation.id
        ).count() == 1


def test_export_detail_redaction_covers_credentials_and_private_keys():
    detail = (
        "password: secret token=abc authorization Bearer-value\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    redacted = safe_detail(detail)
    assert "secret" not in redacted
    assert "abc" not in redacted
    assert "Bearer-value" not in redacted
    assert "private\n" not in redacted
    assert redacted.count("[redacted]") == 4
