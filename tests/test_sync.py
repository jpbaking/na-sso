from oneauth.connectors.base import Connector, SyncResult
from oneauth.models import ManagedUser
from oneauth.security import encrypt_secret


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
    from oneauth.db import get_session

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
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [first, second])
    user_id = _stored_user()

    from oneauth.sync import sync_user

    await sync_user(user_id)

    from oneauth.db import get_session

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
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [good, flaky])
    user_id = _stored_user()

    from oneauth.sync import sync_user

    await sync_user(user_id)
    from oneauth.db import get_session

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.pending_secret is not None
        assert {state.target: state.state for state in user.sync_states} == {
            "opnsense": "ok",
            "nexus": "failed",
        }

    flaky.ok = True
    await sync_user(user_id, target="nexus")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.pending_secret is None
        assert all(state.state == "ok" for state in user.sync_states)


async def test_sync_disable_and_delete(client, monkeypatch):
    connector = StubConnector("nextcloud")
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from oneauth.db import get_session

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        user.status = "disabled"
        db.commit()

    from oneauth.sync import sync_user

    await sync_user(user_id)
    assert connector.calls[-1] == ("disable", "syncme")
    await sync_user(user_id, action="delete")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user is not None and user.deleted_at is not None
    assert connector.calls[-1] == ("delete", "syncme")


async def test_failed_delete_schedules_and_retries_delete(client, monkeypatch):
    connector = StubConnector("nextcloud", ok=False)
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [connector])
    user_id = _stored_user()
    from oneauth.db import get_session
    from oneauth.sync import retry_due, sync_user
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


def test_retry_endpoint_runs_only_selected_target(admin_client, monkeypatch):
    first = StubConnector("opnsense")
    second = StubConnector("nexus")
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [first, second])
    user_id = _stored_user()

    response = admin_client.post(
        f"/users/{user_id}/retry/nexus", follow_redirects=False
    )

    assert response.status_code == 303
    assert first.calls == []
    assert second.calls == [("ensure", "syncme", "secret-42")]


def test_target_page_omits_duplicate_user_sync_matrix(admin_client):
    user_id = _stored_user()
    from oneauth.db import get_session
    from oneauth.models import SyncState

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
    monkeypatch.setattr("oneauth.sync.get_connectors", lambda: [connector])

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
