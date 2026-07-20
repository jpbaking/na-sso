import csv
import io
import re

import pytest

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


def test_csv_template_download_uses_configured_target_ids(admin_client, monkeypatch):
    _install(monkeypatch, BulkImportConnector())

    response = admin_client.get("/users/bulk/import/template.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "na-sso-bulk-import-template.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["action"] for row in rows] == ["onboard", "onboard", "offboard"]
    assert {"username", "action", "display_name", "email", "target_ids"} == set(rows[0])
    onboard_targets = {
        target for row in rows if row["action"] == "onboard"
        for target in row["target_ids"].split("|")
    }
    assert onboard_targets == {"cloud"}


def test_csv_template_rows_pass_preview_validation(admin_client, monkeypatch):
    _install(monkeypatch, BulkImportConnector())
    template = admin_client.get("/users/bulk/import/template.csv").text

    preview = admin_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("template.csv", template.encode(), "text/csv")},
        follow_redirects=True,
    )

    assert preview.status_code == 200
    assert "unknown target" not in preview.text


def test_csv_template_requires_an_authenticated_admin(client):
    response = client.get("/users/bulk/import/template.csv", follow_redirects=False)

    assert response.status_code in {302, 303, 307, 403}


def test_bulk_import_page_lists_target_ids_in_a_modal(admin_client, monkeypatch):
    _install(monkeypatch, BulkImportConnector())

    page = admin_client.get("/users/bulk/import")

    assert page.status_code == 200
    assert 'data-modal-open="#targets-modal"' in page.text
    assert 'id="targets-modal"' in page.text
    assert "<code>cloud</code>" in page.text
    assert "Cloud access" in page.text
    assert "/users/bulk/import/template.csv" in page.text


def test_csv_upload_requires_every_column(admin_client, monkeypatch):
    _install(monkeypatch, BulkImportConnector())
    csv_body = "username,action\nnew_bulk_user,onboard\n"

    rejected = admin_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("accounts.csv", csv_body, "text/csv")},
        follow_redirects=True,
    )

    assert rejected.status_code == 200
    assert "missing required column" in rejected.text
    for column in ("display_name", "email", "target_ids"):
        assert column in rejected.text


def test_onboard_rows_require_display_name_and_email(admin_client, monkeypatch):
    from na_sso.db import get_session
    from na_sso.models import BulkWorkflowRow

    _install(monkeypatch, BulkImportConnector())
    _existing_user()
    csv_body = (
        "username,action,display_name,email,target_ids\n"
        "no_profile_user,onboard,,,cloud\n"
        "named_user,onboard,Named User,named@example.test,cloud\n"
        "offboard_user,offboard,,,\n"
    )

    preview = admin_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("accounts.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )

    assert preview.status_code == 303
    with get_session() as db:
        rows = {
            row.username: row
            for row in db.query(BulkWorkflowRow).order_by(BulkWorkflowRow.row_number).all()
        }
    assert rows["no_profile_user"].validation_status == "invalid"
    assert "display name" in rows["no_profile_user"].detail
    assert "email" in rows["no_profile_user"].detail
    assert rows["named_user"].validation_status == "valid"
    assert rows["offboard_user"].validation_status == "valid"


@pytest.fixture()
def capped_client(tmp_path, monkeypatch):
    """App client whose configuration caps bulk jobs at five rows."""
    config_path = tmp_path / "na-sso.yaml"
    config_path.write_text(
        "version: 1\n"
        "bulk_import_policy:\n"
        "  max_rows: 5\n"
        "  row_byte_allowance: 512\n"
    )
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(tmp_path / "capped.db"))
    monkeypatch.setenv("NA_SSO_SECRET_KEY", "capped-secret")
    monkeypatch.setenv("NA_SSO_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("NA_SSO_ADMIN_BOOTSTRAP_PASSWORD", "admin-pass")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    import na_sso.config as config
    import na_sso.db as db

    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    from fastapi.testclient import TestClient

    from na_sso.main import app, bootstrap_admin

    db.init_db()
    bootstrap_admin()
    with TestClient(app) as client:
        client.post(
            "/login", data={"username": "admin", "password": "admin-pass"},
            follow_redirects=True,
        )
        yield client
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()


def _rows_csv(count):
    body = "username,action,display_name,email,target_ids\n"
    for index in range(count):
        body += f"user_{index:04d},onboard,User {index},u{index}@example.test,cloud\n"
    return body


def test_configured_row_cap_drives_the_derived_upload_cap(capped_client):
    from na_sso.bulk import max_bulk_rows, max_csv_bytes, upload_size_label

    assert max_bulk_rows() == 5
    assert max_csv_bytes() == 5 * 512
    assert upload_size_label() == "2 KiB"


def test_csv_path_accepts_the_configured_cap_and_rejects_one_more(capped_client):
    at_cap = capped_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("at.csv", _rows_csv(5).encode(), "text/csv")},
        follow_redirects=False,
    )
    assert at_cap.status_code == 303

    over_cap = capped_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("over.csv", _rows_csv(6).encode(), "text/csv")},
        follow_redirects=True,
    )
    assert over_cap.status_code == 200
    assert "1–5 data rows" in over_cap.text


def test_api_path_rejects_above_the_configured_cap(capped_client):
    rows = [{
        "username": f"api_user_{index}", "action": "onboard",
        "display_name": f"API User {index}", "email": f"api{index}@example.test",
        "target_ids": ["cloud"],
    } for index in range(6)]

    response = capped_client.post("/api/v1/bulk/preview", json={
        "idempotency_key": "capped-preview-01", "rows": rows,
    })

    assert response.status_code == 422
    assert "1–5 rows" in response.text


def test_upload_larger_than_the_derived_cap_is_rejected(capped_client):
    oversized = "username,action,display_name,email,target_ids\n" + (
        "u,onboard,Padded Name,padded@example.test,cloud\n" * 100
    )
    assert len(oversized) > 5 * 512

    rejected = capped_client.post(
        "/users/bulk/import/preview",
        files={"csv_file": ("big.csv", oversized.encode(), "text/csv")},
        follow_redirects=True,
    )

    assert rejected.status_code == 200
    assert "exceeds 2 KiB" in rejected.text
