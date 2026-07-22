from datetime import datetime, timedelta, timezone
import re

from na_sso.connectors import Connector, SyncResult
from na_sso.models import ManagedUser, SyncState
from na_sso.reconciliation import (
    InspectionCapabilities,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)


class ReconciliationConnector(Connector):
    target_id = "cloud"
    target_type = "nextcloud"
    display_name = "Cloud access"
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True,
    )

    def __init__(self):
        self.remote = {
            "present": True,
            "username": "reconcile_user",
            "display_name": "Old name",
            "email": "person@example.test",
            "status": "active",
            "memberships": frozenset(),
        }
        self.read_fails = False
        self.ensure_calls = 0
        self.delete_calls = 0

    async def inspect_user(self, user):
        if self.read_fails:
            return unavailable_report(
                target_id=self.target_id, target_name=self.display_name,
                user=user, capabilities=self.inspection_capabilities,
                detail="Cloud identity read failed.",
            )
        snapshot = RemoteIdentitySnapshot(**self.remote)
        return compare_snapshot(
            target_id=self.target_id, target_name=self.display_name,
            user=user, capabilities=self.inspection_capabilities,
            snapshot=snapshot,
        )

    async def ensure_user(self, user, password):
        self.ensure_calls += 1
        self.remote.update(
            present=True, username=user.username,
            display_name=user.display_name or user.username,
            email=user.email,
            status="disabled" if user.status == "disabled" else "active",
        )
        return SyncResult(True, "saved")

    async def disable_user(self, user):
        return await self.ensure_user(user, None)

    async def delete_user(self, user):
        self.delete_calls += 1
        self.remote.update(present=False, username=None, display_name=None, email=None, status=None)
        return SyncResult(True, "deleted")

    async def probe(self):
        return SyncResult(True, "reachable")


def _install_connector(monkeypatch, connector):
    monkeypatch.setattr("na_sso.reconcile.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])


def _assigned_user(username="reconcile_user", *, desired_action="ensure"):
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username=username,
            display_name="Current name",
            email="person@example.test",
            desired_action=desired_action,
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud",
            assigned=True, state="ok",
        ))
        db.commit()
        return user.id


def test_preview_is_read_only_and_approved_repair_is_correlated_idempotent(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import AuditEvent, LifecycleOperation, ReconciliationFinding, ReconciliationRun

    connector = ReconciliationConnector()
    _install_connector(monkeypatch, connector)
    user_id = _assigned_user()

    index = admin_client.get("/reconciliation")
    assert index.status_code == 200
    assert "Create a preview" in index.text
    assert "never approve or repair drift automatically" in index.text

    response = admin_client.post(
        "/reconciliation/preview",
        data={"user_id": user_id, "target_id": "cloud"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert connector.ensure_calls == connector.delete_calls == 0
    detail = admin_client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Old name" in detail.text and "Current name" in detail.text
    assert "Approve correlated repair" in detail.text
    token = re.search(r'name="approval_token" value="([^"]+)"', detail.text).group(1)

    with get_session() as db:
        run = db.query(ReconciliationRun).one()
        assert run.status == "previewed" and run.drifted_targets == 1
        assert db.query(ReconciliationFinding).filter_by(
            run_id=run.id, field="display_name", state="drift",
        ).count() == 1
        run_id = run.id

    approved = admin_client.post(
        f"/reconciliation/{run_id}/approve",
        data={"approval_token": token},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert connector.ensure_calls == 1 and connector.delete_calls == 0
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        assert run.status == "completed" and run.operation_id
        parent = db.get(LifecycleOperation, run.operation_id)
        children = db.query(LifecycleOperation).filter_by(parent_id=parent.id).all()
        assert parent.status == "succeeded" and len(children) == 1
        assert children[0].status == "succeeded" and children[0].requested_target == "cloud"
        assert db.query(ReconciliationFinding).filter_by(
            run_id=run.id, field="display_name", repair_status="repaired",
        ).count() == 1
        assert db.query(AuditEvent).filter_by(action="reconcile.approved").count() == 1
        assert db.query(AuditEvent).filter_by(action="reconcile.completed").count() == 1
        parent_id, child_id = parent.id, children[0].id

    operation_page = admin_client.get(f"/audit/operations/{parent_id}")
    assert operation_page.status_code == 200
    assert child_id[:8] in operation_page.text and "Correlated repairs" in operation_page.text

    replay = admin_client.post(
        f"/reconciliation/{run_id}/approve",
        data={"approval_token": token}, follow_redirects=False,
    )
    assert replay.status_code == 303 and connector.ensure_calls == 1
    with get_session() as db:
        assert db.query(LifecycleOperation).filter_by(command="reconcile").count() == 2


def test_destructive_repair_requires_separate_confirmation(admin_client, monkeypatch):
    from na_sso.db import get_session
    from na_sso.models import ReconciliationRun

    connector = ReconciliationConnector()
    connector.remote["username"] = "departing_user"
    _install_connector(monkeypatch, connector)
    user_id = _assigned_user("departing_user", desired_action="delete")

    response = admin_client.post(
        "/reconciliation/preview", data={"user_id": user_id, "target_id": "cloud"},
        follow_redirects=False,
    )
    detail = admin_client.get(response.headers["location"])
    assert "Remote account deletion" in detail.text
    token = re.search(r'name="approval_token" value="([^"]+)"', detail.text).group(1)
    with get_session() as db:
        run = db.query(ReconciliationRun).one()
        assert run.destructive_targets == 1
        run_id = run.id

    rejected = admin_client.post(
        f"/reconciliation/{run_id}/approve",
        data={"approval_token": token}, follow_redirects=False,
    )
    assert rejected.status_code == 303 and connector.delete_calls == 0
    with get_session() as db:
        assert db.get(ReconciliationRun, run_id).status == "previewed"

    approved = admin_client.post(
        f"/reconciliation/{run_id}/approve",
        data={"approval_token": token, "confirm_destructive": "yes"},
        follow_redirects=False,
    )
    assert approved.status_code == 303 and connector.delete_calls == 1
    with get_session() as db:
        assert db.get(ReconciliationRun, run_id).status == "completed"
        assert db.get(ManagedUser, user_id).deleted_at is not None


def test_preview_marks_declared_unsupported_disable_without_proposing_repair(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import ReconciliationFinding, ReconciliationRun

    connector = ReconciliationConnector()
    connector.disable_supported = False
    connector.remote["display_name"] = "Current name"
    _install_connector(monkeypatch, connector)
    user_id = _assigned_user()
    with get_session() as db:
        db.get(ManagedUser, user_id).status = "disabled"
        db.commit()

    response = admin_client.post(
        "/reconciliation/preview",
        data={"user_id": user_id, "target_id": "cloud"},
        follow_redirects=False,
    )
    detail = admin_client.get(response.headers["location"])

    assert response.status_code == 303 and detail.status_code == 200
    assert ">unsupported<" in detail.text
    assert "Approve correlated repair" not in detail.text
    assert connector.ensure_calls == connector.delete_calls == 0
    with get_session() as db:
        run = db.query(ReconciliationRun).one()
        finding = db.query(ReconciliationFinding).filter_by(
            run_id=run.id,
            field="status",
        ).one()
        assert run.status == "previewed" and run.drifted_targets == 0
        assert finding.state == "unsupported"
        assert finding.desired == "disabled" and finding.actual == "active"
        assert finding.detail == (
            "Repair unsupported: Cloud access declares disable unsupported."
        )


async def test_scheduled_discovery_retries_unknown_reads_without_repair(
    client, tmp_path, monkeypatch,
):
    from na_sso.config import get_settings
    from na_sso.db import get_session
    from na_sso.models import ReconciliationRun
    from na_sso.reconcile import run_scheduled_reconciliation

    config_path = tmp_path / "reconcile.yaml"
    config_path.write_text("""
reconciliation_policy:
  enabled: true
  interval_seconds: 60
  scan_seconds: 5
  retry_base_seconds: 5
  retry_max_seconds: 20
  max_attempts: 3
""")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    get_settings.cache_clear()
    connector = ReconciliationConnector()
    connector.read_fails = True
    _install_connector(monkeypatch, connector)
    _assigned_user()

    assert await run_scheduled_reconciliation() == 1
    with get_session() as db:
        run = db.query(ReconciliationRun).one()
        assert run.source == "scheduled" and run.status == "retrying"
        assert run.attempt_count == 1 and run.next_attempt_at is not None
        run.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        run_id = run.id
        db.commit()

    connector.read_fails = False
    assert await run_scheduled_reconciliation() == 1
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        assert run.status == "previewed" and run.attempt_count == 2
        assert run.next_attempt_at is None
        assert db.query(ReconciliationRun).count() == 1
    assert connector.ensure_calls == connector.delete_calls == 0
    get_settings.cache_clear()
