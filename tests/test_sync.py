from na_sso.connectors.base import Connector, SyncResult
from na_sso.models import (
    LifecycleOperation,
    ManagedUser,
    OperationTargetAttempt,
    SyncState,
)
from na_sso.security import encrypt_secret


class StubConnector(Connector):
    def __init__(self, name: str, ok: bool = True):
        self.name = name
        self.ok = ok
        self.calls = []

    async def ensure_user(self, user, password):
        self.calls.append(("ensure", user.username, password))
        return SyncResult(self.ok, "saved" if self.ok else "offline")

    async def disable_user(self, user):
        self.calls.append(("disable", user.username))
        return SyncResult(self.ok, "disabled" if self.ok else "offline")

    async def delete_user(self, user):
        self.calls.append(("delete", user.username))
        return SyncResult(self.ok, "deleted" if self.ok else "offline")

    async def probe(self):
        return SyncResult(self.ok)


def _stored_user():
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username="syncme",
            display_name="Sync Me",
            email="sync@example.test",
            pending_secret=encrypt_secret("secret-42"),
        )
        db.add(user)
        db.commit()
        return user.id


async def test_sync_success_clears_pending_secret(client, monkeypatch):
    first = StubConnector("opnsense")
    second = StubConnector("nexus")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [first, second])
    user_id = _stored_user()

    from na_sso.sync import sync_user

    await sync_user(user_id)

    from na_sso.db import get_session

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.pending_secret is None
        assert {state.target: state.state for state in user.sync_states} == {
            "opnsense": "ok",
            "nexus": "ok",
        }
    assert first.calls == [("ensure", "syncme", "secret-42")]


async def test_sync_partial_failure_keeps_secret_and_retry_succeeds(client, monkeypatch):
    good = StubConnector("opnsense")
    flaky = StubConnector("nexus", ok=False)
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [good, flaky])
    monkeypatch.setattr("na_sso.status.get_connectors", lambda: [good, flaky])
    user_id = _stored_user()

    from na_sso.sync import sync_user

    await sync_user(user_id)
    from na_sso.db import get_session

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.pending_secret is not None
        assert {state.target: state.state for state in user.sync_states} == {
            "opnsense": "ok",
            "nexus": "failed",
        }
        operation_id = next(
            state.operation_id for state in user.sync_states if state.target == "nexus"
        )
        assert db.get(LifecycleOperation, operation_id).status == "partially_failed"
    from na_sso.status import sync_snapshot
    progress = next(item for item in sync_snapshot()["users"] if item["id"] == user_id)
    assert progress["operation"]["id"] == operation_id
    assert progress["operation"]["completed_targets"] == 2
    assert progress["operation"]["failed_targets"] == 1
    assert progress["operation"]["blocking_targets"] == ["nexus"]

    flaky.ok = True
    await sync_user(user_id, target="nexus")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.pending_secret is None
        assert all(state.state == "ok" for state in user.sync_states)
        assert all(state.operation_id == operation_id for state in user.sync_states)
        operation = db.get(LifecycleOperation, operation_id)
        assert operation.status == "succeeded"
        attempts = db.query(OperationTargetAttempt).filter_by(
            operation_id=operation_id, target="nexus"
        ).order_by(OperationTargetAttempt.attempt_number).all()
        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    completed = next(item for item in sync_snapshot()["users"] if item["id"] == user_id)
    assert completed["operation"]["status"] == "succeeded"
    assert completed["operation"]["blocking_targets"] == []


async def test_sync_disable_and_delete(client, monkeypatch):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from na_sso.db import get_session

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        user.status = "disabled"
        db.commit()

    from na_sso.sync import sync_user

    await sync_user(user_id)
    assert connector.calls[-1] == ("disable", "syncme")
    await sync_user(user_id, action="delete")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user is not None and user.deleted_at is not None
    assert connector.calls[-1] == ("delete", "syncme")


async def test_delete_overrides_chpw_and_reaches_terminal_state(client, monkeypatch):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from na_sso.db import get_session
    from na_sso.sync import sync_user

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        user.desired_action = "delete"
        user.password_decision_required = True
        user.password_decision_kind = "reset"
        db.add(SyncState(
            user=user,
            target="nextcloud",
            target_type="nextcloud",
            assigned=True,
            state="chpw",
        ))
        db.commit()

    operation_id = await sync_user(user_id, action="delete")

    assert connector.calls == [("delete", "syncme")]
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        state = user.sync_states[0]
        operation = db.get(LifecycleOperation, operation_id)
        assert user.deleted_at is not None
        assert state.state == "ok"
        assert operation.status == "succeeded"
        assert operation.completed_targets == 1 and operation.failed_targets == 0


def test_restore_is_rejected_until_delete_is_terminal(admin_client):
    from na_sso.db import get_session
    from na_sso.security import verify_password

    admin_client.post("/users/new", data={
        "username": "restore-race",
        "display_name": "Restore Race",
        "email": "mp@example.invalid",
        "password": "V4lid!Copper-Zebra-2026",
    })
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="restore-race").one()
        user.desired_action = "delete"
        user.deletion_requested_at = user.created_at
        user.deleted_at = None
        original_hash = user.password_hash
        db.commit()
        user_id = user.id

    page = admin_client.get("/users")
    assert f'action="/users/{user_id}/restore"' not in page.text
    assert "Deletion must finish before recovery" in page.text

    response = admin_client.post(
        f"/users/{user_id}/restore",
        data={"password": "N3w!Marble-Quartz-2027"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.desired_action == "delete" and user.deleted_at is None
        assert user.password_hash == original_hash
        assert not verify_password("N3w!Marble-Quartz-2027", user.password_hash)

    feedback = admin_client.get("/users")
    assert "Restore unavailable" in feedback.text
    assert "only after remote deletion completes" in feedback.text


def test_completed_delete_restores_to_chpw_without_recreating_remote_account(
    admin_client, monkeypatch
):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    from na_sso.db import get_session

    response = admin_client.post("/users/new", data={
        "username": "restore-terminal",
        "display_name": "Terminal Restore",
        "email": "mp@example.invalid",
        "password": "V4lid!Copper-Zebra-2026",
        "target_ids": "nextcloud",
    })
    assert response.status_code == 200
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="restore-terminal").one()
        user_id = user.id
        assert user.sync_states[0].state == "chpw"
    assert connector.calls == []

    admin_client.post(f"/users/{user_id}/delete")
    assert connector.calls == [("delete", "restore-terminal")]

    admin_client.post(
        f"/users/{user_id}/restore",
        data={"password": "N3w!Marble-Quartz-2027"},
    )

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        operation = (
            db.query(LifecycleOperation)
            .filter_by(user_id=user_id, command="restore")
            .order_by(LifecycleOperation.created_at.desc())
            .first()
        )
        assert user.desired_action == "ensure" and user.deleted_at is None
        assert user.sync_states[0].state == "chpw"
        assert operation is not None and operation.status == "succeeded"
        assert user.active_operation_id is None
    assert connector.calls == [("delete", "restore-terminal")]


def test_update_is_rejected_while_operation_is_running(admin_client):
    from na_sso.db import get_session
    from na_sso.lifecycle import LifecycleCommand
    from na_sso.operations import create_operation, start_operation

    admin_client.post("/users/new", data={
        "username": "busy-user",
        "display_name": "Before",
        "email": "mp@example.invalid",
        "password": "V4lid!Copper-Zebra-2026",
    })
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="busy-user").one()
        operation = create_operation(db, user, LifecycleCommand.UPDATE, "system")
        start_operation(operation, 1)
        db.commit()
        user_id = user.id

    response = admin_client.post(
        f"/users/{user_id}",
        data={"display_name": "After", "email": "mp@example.invalid", "password": "", "status": "active"},
    )

    assert response.status_code == 409
    assert "another lifecycle operation is running" in response.text
    with get_session() as db:
        assert db.get(ManagedUser, user_id).display_name == "Before"


def test_users_html_and_sse_share_unassigned_state_presentation(admin_client, monkeypatch):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.status.get_connectors", lambda: [connector])
    from na_sso.db import get_session
    from na_sso.status import sync_snapshot

    with get_session() as db:
        user = ManagedUser(username="unassigned-view", display_name="Unassigned")
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user,
            target="nextcloud",
            target_type="nextcloud",
            assigned=False,
            state="unassigned",
            detail="saved",
        ))
        not_assigned = ManagedUser(username="not-assigned-view", display_name="Never Assigned")
        db.add(not_assigned)
        db.commit()
        user_id = user.id
        not_assigned_id = not_assigned.id

    assigned_page = admin_client.get(f"/users/{user_id}")
    unassigned_page = admin_client.get(f"/users/{not_assigned_id}")
    snapshot = next(item for item in sync_snapshot()["users"] if item["id"] == user_id)
    not_assigned_snapshot = next(
        item for item in sync_snapshot()["users"] if item["id"] == not_assigned_id
    )

    assert "Unassigned; disabled" in assigned_page.text
    assert "Not assigned" in unassigned_page.text
    assert snapshot["states"]["nextcloud"]["state"] == "unassigned"
    assert snapshot["states"]["nextcloud"]["presentation"]["label"] == "Unassigned; disabled"
    assert not_assigned_snapshot["states"]["nextcloud"]["state"] == "not_assigned"
    assert not_assigned_snapshot["states"]["nextcloud"]["presentation"]["label"] == "Not assigned"


async def test_failed_delete_schedules_and_retries_delete(client, monkeypatch):
    connector = StubConnector("nextcloud", ok=False)
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from na_sso.db import get_session
    from na_sso.sync import retry_due, sync_user
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        user.desired_action = "delete"
        db.commit()
    await sync_user(user_id)
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert state.state == "failed" and state.attempt_count == 1 and state.next_retry_at
        state.next_retry_at = state.next_retry_at.replace(year=2000)
        db.commit()
    connector.ok = True
    assert await retry_due() == 1
    assert connector.calls[-1] == ("delete", "syncme")
    with get_session() as db:
        assert db.get(ManagedUser, user_id).deleted_at is not None


async def test_validation_failure_is_terminal_unsupported_without_retry(client, monkeypatch):
    class UnsupportedDisableConnector(StubConnector):
        async def disable_user(self, user):
            self.calls.append(("disable", user.username))
            return SyncResult(False, "Jenkins core cannot safely disable a local account")

    connector = UnsupportedDisableConnector("jenkins")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from na_sso.db import get_session
    from na_sso.sync import retry_due, sync_user
    with get_session() as db:
        db.get(ManagedUser, user_id).status = "disabled"
        db.commit()

    await sync_user(user_id)

    from na_sso.models import LifecycleOperation
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert state.state == "unsupported"
        assert state.next_retry_at is None and state.attempt_count == 1
        operation = db.get(LifecycleOperation, state.operation_id)
        assert operation.status == "failed" and operation.failed_targets == 1
    assert await retry_due() == 0
    assert connector.calls.count(("disable", "syncme")) == 1


async def test_unassigned_disable_failure_retries_until_disabled(client, monkeypatch):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from na_sso.db import get_session
    from na_sso.sync import retry_due, sync_user

    await sync_user(user_id)
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        state.assigned = False
        state.state = "pending_disable"
        db.commit()
    connector.ok = False
    await sync_user(user_id)
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert state.state == "failed" and not state.assigned and state.next_retry_at
        state.next_retry_at = state.next_retry_at.replace(year=2000)
        db.commit()

    connector.ok = True
    assert await retry_due() == 1

    assert connector.calls[-1] == ("disable", "syncme")
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert state.state == "unassigned" and state.next_retry_at is None


def test_retry_endpoint_runs_only_selected_target(admin_client, monkeypatch):
    first = StubConnector("opnsense")
    second = StubConnector("nexus")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [first, second])
    user_id = _stored_user()

    response = admin_client.post(
        f"/users/{user_id}/retry/nexus", follow_redirects=False
    )

    assert response.status_code == 303
    assert first.calls == []
    assert second.calls == [("ensure", "syncme", "secret-42")]


def test_target_page_omits_duplicate_user_sync_matrix(admin_client):
    user_id = _stored_user()
    from na_sso.db import get_session
    from na_sso.models import SyncState

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        db.add(SyncState(user=user, target="nexus", state="failed", detail="offline"))
        db.commit()

    target_page = admin_client.get("/status")
    users_page = admin_client.get("/users")

    assert target_page.status_code == 200
    assert "User sync matrix" not in target_page.text
    assert "syncme" not in target_page.text
    assert '<td data-sync-cell' not in target_page.text
    assert "syncme" in users_page.text


def test_sync_sse_requires_auth_and_returns_snapshot(client):
    assert client.get("/events/sync?once=true").status_code == 401
    user_id = _stored_user()
    assert client.post("/login", data={"username": "admin", "password": "admin-pass"}, follow_redirects=False).status_code == 303
    response = client.get("/events/sync?once=true")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: sync" in response.text
    assert f'\"id\":{user_id}' in response.text
    assert '\"states\"' in response.text


def test_audit_page_lists_admin_and_sync_events(admin_client, monkeypatch):
    connector = StubConnector("opnsense")
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])

    response = admin_client.post(
        "/users/new",
        data={
            "username": "audited",
            "display_name": "Audit User",
            "email": "audit@example.test",
            "password": "V4lid!Sync-Secret-2026",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = admin_client.get("/audit")
    assert response.status_code == 200
    assert "user.create" in response.text
    assert "sync.ensure" in response.text
    assert "audited" in response.text
