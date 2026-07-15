import csv
import io
import re

from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
from na_sso.models import ManagedUser, SyncState


class BulkImportConnector(Connector):
    target_id = "cloud"
    target_type = "nextcloud"
    display_name = "Cloud access"
    capabilities = IdentityCapabilities(email=True, display_name=True, password=False)

    def __init__(self):
        self.ensure_calls = []
        self.disable_calls = []

    async def ensure_user(self, user, password):
        self.ensure_calls.append((user.username, password))
        return SyncResult(True, "saved")

    async def disable_user(self, user):
        self.disable_calls.append(user.username)
        return SyncResult(True, "disabled")

    async def delete_user(self, user):
        return SyncResult(True, "deleted")

    async def probe(self):
        return SyncResult(True, "reachable")


def _install(monkeypatch, connector):
    monkeypatch.setattr("na_sso.bulk.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])


def _existing_user(username="offboard_user"):
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username=username, display_name="Existing user", email="existing@example.test"
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud",
            assigned=True, state="ok",
        ))
        db.commit()
        return user.id


def test_csv_preview_execution_partial_results_and_one_time_credentials(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import (
        AuditEvent, BulkWorkflow, BulkWorkflowRow, LifecycleOperation,
    )

    connector = BulkImportConnector()
    _install(monkeypatch, connector)
    existing_id = _existing_user()
    csv_body = """username,action,display_name,email,target_ids
new_bulk_user,onboard,New Bulk User,new@example.test,cloud
offboard_user,offboard,,,cloud
bad/name,onboard,Bad Name,bad@example.test,missing
"""

    index = admin_client.get("/users/bulk/import")
    assert index.status_code == 200 and "No changes made" not in index.text
    preview = admin_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("accounts.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    assert preview.status_code == 303
    assert connector.ensure_calls == connector.disable_calls == []
    detail = admin_client.get(preview.headers["location"])
    assert detail.status_code == 200
    assert "2 valid row(s); 1 invalid row(s). No changes made." in detail.text
    assert "unknown target(s): missing" in detail.text
    key = re.search(r'name="idempotency_key" value="([^"]+)"', detail.text).group(1)

    with get_session() as db:
        workflow = db.query(BulkWorkflow).one()
        assert workflow.status == "previewed" and workflow.valid_count == 2
        assert db.query(ManagedUser).filter_by(username="new_bulk_user").count() == 0
        workflow_id = workflow.id

    execute = admin_client.post(
        f"/users/bulk/import/{workflow_id}/execute",
        data={"idempotency_key": key}, follow_redirects=False,
    )
    assert execute.status_code == 303
    assert connector.disable_calls == ["offboard_user"]
    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        assert workflow.status == "partially_failed"
        assert (workflow.succeeded_count, workflow.failed_count) == (2, 1)
        parent = db.get(LifecycleOperation, workflow.operation_id)
        children = db.query(LifecycleOperation).filter_by(parent_id=parent.id).all()
        assert parent.status == "partially_failed" and len(children) == 2
        new_user = db.query(ManagedUser).filter_by(username="new_bulk_user").one()
        assert new_user.password_decision_kind == "initial"
        assert {state.target for state in new_user.sync_states if state.assigned} == {"cloud"}
        existing = db.get(ManagedUser, existing_id)
        assert not existing.sync_states[0].assigned
        rows = db.query(BulkWorkflowRow).filter_by(workflow_id=workflow.id).order_by(
            BulkWorkflowRow.row_number
        ).all()
        assert [row.result_status for row in rows] == ["succeeded", "succeeded", "invalid"]
        assert rows[0].encrypted_temporary_password is not None
        assert db.query(AuditEvent).filter_by(action="bulk.import_completed").count() == 1

    results = admin_client.get(f"/users/bulk/import/{workflow_id}/results.csv")
    result_rows = list(csv.DictReader(io.StringIO(results.text)))
    assert results.status_code == 200 and len(result_rows) == 3
    assert "temporary_password" not in results.text
    assert {row["result"] for row in result_rows} == {"succeeded", "invalid"}

    credentials = admin_client.post(f"/users/bulk/import/{workflow_id}/credentials.csv")
    credential_rows = list(csv.DictReader(io.StringIO(credentials.text)))
    assert credentials.status_code == 200
    assert credential_rows[0]["username"] == "new_bulk_user"
    assert len(credential_rows[0]["temporary_password"]) >= 14
    assert credential_rows[0]["temporary_password"][0] not in "=+-@"
    assert admin_client.post(
        f"/users/bulk/import/{workflow_id}/credentials.csv"
    ).status_code == 410
    with get_session() as db:
        assert db.query(BulkWorkflowRow).filter(
            BulkWorkflowRow.encrypted_temporary_password.is_not(None)
        ).count() == 0
        audit_text = " ".join(event.detail for event in db.query(AuditEvent).all())
        assert credential_rows[0]["temporary_password"] not in audit_text
        assert db.query(AuditEvent).filter_by(action="bulk.credentials_downloaded").count() == 1

    replay = admin_client.post(
        f"/users/bulk/import/{workflow_id}/execute",
        data={"idempotency_key": key}, follow_redirects=False,
    )
    assert replay.status_code == 303 and connector.disable_calls == ["offboard_user"]
    with get_session() as db:
        assert db.query(LifecycleOperation).filter_by(command="bulk").count() == 1


def test_json_api_preview_execute_and_idempotent_replay(admin_client, monkeypatch):
    from na_sso.db import get_session
    from na_sso.models import BulkWorkflow, LifecycleOperation

    connector = BulkImportConnector()
    _install(monkeypatch, connector)
    _existing_user("api_user")
    payload = {
        "idempotency_key": "api-import-0001",
        "rows": [{
            "username": "api_user", "action": "onboard",
            "display_name": "API User", "email": "api@example.test",
            "target_ids": ["cloud"],
        }],
    }

    first = admin_client.post("/api/v1/bulk/preview", json=payload)
    second = admin_client.post("/api/v1/bulk/preview", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert first.json()["data"]["rows"][0]["target_ids"] == ["cloud"]
    workflow_id = first.json()["data"]["id"]
    with get_session() as db:
        assert db.query(BulkWorkflow).count() == 1

    executed = admin_client.post(
        f"/api/v1/bulk/{workflow_id}/execute",
        json={"idempotency_key": payload["idempotency_key"]},
    )
    assert executed.status_code == 202
    final = admin_client.get(f"/api/v1/bulk/{workflow_id}")
    assert final.status_code == 200 and final.json()["data"]["status"] == "completed"
    assert final.json()["data"]["rows"][0]["result"] == "succeeded"
    assert connector.ensure_calls == [("api_user", None)]

    replay = admin_client.post(
        f"/api/v1/bulk/{workflow_id}/execute",
        json={"idempotency_key": payload["idempotency_key"]},
    )
    assert replay.status_code == 202 and connector.ensure_calls == [("api_user", None)]
    with get_session() as db:
        assert db.query(LifecycleOperation).filter_by(command="bulk").count() == 1


def test_api_preview_is_bounded_and_validates_assignment_mapping_at_scale(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import BulkWorkflowRow, ManagedUser

    connector = BulkImportConnector()
    _install(monkeypatch, connector)
    rows = [{
        "username": f"scale_user_{index:03d}", "action": "onboard",
        "display_name": f"Scale User {index}",
        "email": f"scale{index}@example.test", "target_ids": ["cloud"],
    } for index in range(250)]
    result = admin_client.post("/api/v1/bulk/preview", json={
        "idempotency_key": "scale-preview-0001", "rows": rows,
    })
    assert result.status_code == 201
    assert result.json()["data"]["row_count"] == result.json()["data"]["valid_count"] == 250
    with get_session() as db:
        assert db.query(BulkWorkflowRow).count() == 250
        assert db.query(ManagedUser).filter(ManagedUser.username.like("scale_user_%")).count() == 0
