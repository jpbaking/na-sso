import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from na_sso.config import Settings
from na_sso.connectors.nextcloud import NextcloudConnector
from na_sso.connectors.nexus import NexusConnector
from na_sso.connectors.opnsense import OPNsenseConnector
from na_sso.mock_targets.app import app
from na_sso.models import ManagedUser


@pytest.fixture(scope="module")
def live_mock_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        pytest.fail("mock target HTTP server did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _client() -> TestClient:
    client = TestClient(app)
    response = client.post("/__mock__/reset")
    assert response.status_code == 200
    return client


def test_opnsense_mock_user_lifecycle():
    client = _client()
    auth = ("demo-key", "demo-secret")
    payload = {
        "user": {
            "name": "alice",
            "descr": "Alice Example",
            "email": "alice@example.test",
            "disabled": "0",
            "password": "first-secret",
        }
    }

    assert client.post("/api/auth/user/add", auth=auth, json=payload).json()["result"] == "saved"
    rows = client.post(
        "/api/auth/user/search", auth=auth, json={"searchPhrase": "alice"}
    ).json()["rows"]
    assert rows[0]["password"] == "first-secret"

    user_uuid = rows[0]["uuid"]
    payload["user"].update({"disabled": "1", "password": "second-secret"})
    assert client.post(f"/api/auth/user/set/{user_uuid}", auth=auth, json=payload).json()["result"] == "saved"
    row = client.post(
        "/api/auth/user/search", auth=auth, json={"searchPhrase": "alice"}
    ).json()["rows"][0]
    assert (row["disabled"], row["password"]) == ("1", "second-secret")

    assert client.post(f"/api/auth/user/del/{user_uuid}", auth=auth, json={}).json()["result"] == "deleted"
    assert client.post(
        "/api/auth/user/search", auth=auth, json={"searchPhrase": "alice"}
    ).json()["rows"] == []


def test_nexus_mock_user_lifecycle():
    client = _client()
    auth = ("admin", "demo-password")
    payload = {
        "userId": "alice",
        "firstName": "Alice",
        "lastName": "Example",
        "emailAddress": "alice@example.test",
        "status": "active",
        "roles": ["nx-anonymous"],
        "password": "first-secret",
    }

    assert client.post("/service/rest/v1/security/users", auth=auth, json=payload).status_code == 204
    users = client.get(
        "/service/rest/v1/security/users",
        auth=auth,
        params={"userId": "alice", "source": "default"},
    ).json()
    assert users[0]["password"] == "first-secret"

    update = dict(users[0], status="disabled")
    assert client.put("/service/rest/v1/security/users/alice", auth=auth, json=update).status_code == 204
    assert client.put(
        "/service/rest/v1/security/users/alice/change-password",
        auth=auth,
        content="second-secret",
        headers={"Content-Type": "text/plain"},
    ).status_code == 204
    users = client.get(
        "/service/rest/v1/security/users", auth=auth, params={"userId": "alice"}
    ).json()
    assert (users[0]["status"], users[0]["password"]) == ("disabled", "second-secret")

    assert client.delete("/service/rest/v1/security/users/alice", auth=auth).status_code == 204
    assert client.delete("/service/rest/v1/security/users/alice", auth=auth).status_code == 404


def test_nextcloud_mock_user_lifecycle():
    client = _client()
    auth = ("admin", "demo-password")
    headers = {"OCS-APIRequest": "true"}

    response = client.post(
        "/ocs/v1.php/cloud/users",
        auth=auth,
        headers=headers,
        data={
            "userid": "alice",
            "password": "first-secret",
            "displayName": "Alice Example",
            "email": "alice@example.test",
        },
    )
    assert response.json()["ocs"]["meta"]["statuscode"] == 100
    assert client.put(
        "/ocs/v1.php/cloud/users/alice",
        auth=auth,
        headers=headers,
        data={"key": "password", "value": "second-secret"},
    ).json()["ocs"]["meta"]["statuscode"] == 100
    assert client.put(
        "/ocs/v1.php/cloud/users/alice/disable", auth=auth, headers=headers
    ).json()["ocs"]["meta"]["statuscode"] == 100
    user = client.get(
        "/ocs/v1.php/cloud/users/alice", auth=auth, headers=headers
    ).json()["ocs"]["data"]
    assert (user["enabled"], user["password"]) == (False, "second-secret")

    assert client.delete(
        "/ocs/v1.php/cloud/users/alice", auth=auth, headers=headers
    ).json()["ocs"]["meta"]["statuscode"] == 100
    assert client.delete(
        "/ocs/v1.php/cloud/users/alice", auth=auth, headers=headers
    ).json()["ocs"]["meta"]["statuscode"] == 404


def test_mock_health_reset_auth_and_failure_injection():
    client = _client()
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/api/auth/user/search", json={}).status_code == 401

    assert client.post("/__mock__/fail/opnsense").json() == {
        "status": "armed",
        "target": "opnsense",
    }
    assert client.post(
        "/api/auth/user/search", auth=("demo-key", "demo-secret"), json={}
    ).status_code == 503
    assert client.post(
        "/api/auth/user/search", auth=("demo-key", "demo-secret"), json={}
    ).status_code == 200


def test_target_wide_availability_controls():
    client = _client()
    page = client.get("/")
    assert page.status_code == 200 and "Mock target controls" in page.text
    assert client.post(
        "/api/auth/user/search", auth=("demo-key", "demo-secret"), json={}
    ).status_code == 200
    response = client.post(
        "/__mock__/availability/opnsense",
        data={"available": "false"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.post(
        "/api/auth/user/search", auth=("demo-key", "demo-secret"), json={}
    ).status_code == 503
    assert client.get(
        "/service/rest/v1/security/users", auth=("admin", "demo-password")
    ).status_code == 200
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "connector_type",
    [OPNsenseConnector, NexusConnector, NextcloudConnector],
)
async def test_connector_lifecycle_over_real_http(live_mock_url, connector_type):
    async with httpx.AsyncClient() as client:
        assert (await client.post(f"{live_mock_url}/__mock__/reset")).status_code == 200
    settings = Settings(
        opnsense_enabled=True,
        opnsense_base_url=live_mock_url,
        opnsense_api_key="demo-key",
        opnsense_api_secret="demo-secret",
        opnsense_verify_tls=False,
        nexus_enabled=True,
        nexus_base_url=live_mock_url,
        nexus_admin_user="admin",
        nexus_admin_password="demo-password",
        nextcloud_enabled=True,
        nextcloud_base_url=live_mock_url,
        nextcloud_admin_user="admin",
        nextcloud_admin_password="demo-password",
    )
    connector = connector_type(settings)
    user = ManagedUser(
        username="integration_user",
        display_name="Integration User",
        email="integration@example.test",
        status="active",
    )

    assert (await connector.probe()).ok
    assert (await connector.ensure_user(user, "first-secret")).ok
    user.display_name = "Updated User"
    assert (await connector.ensure_user(user, "second-secret")).ok
    user.status = "disabled"
    assert (await connector.disable_user(user)).ok
    assert (await connector.delete_user(user)).ok
    assert (await connector.delete_user(user)).ok


def test_application_demo_workflow_with_failure_and_retry(
    live_mock_url, tmp_path, monkeypatch
):
    target_settings = {
        "NA_SSO_DATABASE_PATH": str(tmp_path / "demo-test.db"),
        "NA_SSO_SECRET_KEY": "demo-test-secret",
        "NA_SSO_ADMIN_USERNAME": "admin",
        "NA_SSO_ADMIN_BOOTSTRAP_PASSWORD": "demo-password",
        "NA_SSO_OPNSENSE_ENABLED": "true",
        "NA_SSO_OPNSENSE_BASE_URL": live_mock_url,
        "NA_SSO_OPNSENSE_API_KEY": "demo-key",
        "NA_SSO_OPNSENSE_API_SECRET": "demo-secret",
        "NA_SSO_OPNSENSE_VERIFY_TLS": "false",
        "NA_SSO_NEXUS_ENABLED": "true",
        "NA_SSO_NEXUS_BASE_URL": live_mock_url,
        "NA_SSO_NEXUS_ADMIN_USER": "admin",
        "NA_SSO_NEXUS_ADMIN_PASSWORD": "demo-password",
        "NA_SSO_NEXTCLOUD_ENABLED": "true",
        "NA_SSO_NEXTCLOUD_BASE_URL": live_mock_url,
        "NA_SSO_NEXTCLOUD_ADMIN_USER": "admin",
        "NA_SSO_NEXTCLOUD_ADMIN_PASSWORD": "demo-password",
    }
    for key, value in target_settings.items():
        monkeypatch.setenv(key, value)

    import na_sso.config as config
    import na_sso.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    httpx.post(f"{live_mock_url}/__mock__/reset").raise_for_status()

    from fastapi.testclient import TestClient

    from na_sso.main import app as na_sso_app

    with TestClient(na_sso_app) as client:
        assert client.post(
            "/login",
            data={"username": "admin", "password": "demo-password"},
            follow_redirects=False,
        ).status_code == 303
        assert client.post(
            "/users/new",
            data={
                "username": "demo_user",
                "display_name": "Demo User",
                "email": "demo@example.test",
                "password": "V4lid!First-Secret-2026",
            },
            follow_redirects=False,
        ).status_code == 303

        from na_sso.models import ManagedUser

        with db.get_session() as session:
            user = session.query(ManagedUser).filter_by(username="demo_user").one()
            user_id = user.id
            assert user.pending_secret is None
            assert {item.target: item.state for item in user.sync_states} == {
                "opnsense": "chpw",
                "nexus": "chpw",
                "nextcloud": "chpw",
            }

        client.post("/logout")
        assert client.post("/login", data={"username": "demo_user", "password": "V4lid!First-Secret-2026"}, follow_redirects=False).headers["location"] == "/account/password-decision"
        first_replacement = "V4lid!Orbit-Replacement-2026"
        assert client.post("/account/password-decision", data={
            "choice": "change", "current_password": "V4lid!First-Secret-2026",
            "new_password": first_replacement, "confirm_password": first_replacement,
        }, follow_redirects=False).headers["location"] == "/login"
        assert client.post("/login", data={"username": "demo_user", "password": first_replacement}, follow_redirects=False).headers["location"] == "/account"
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.pending_secret is None
            assert all(item.state == "ok" for item in user.sync_states)

        client.post("/logout")
        client.post("/login", data={"username": "admin", "password": "demo-password"})
        assert client.post(
            f"/users/{user_id}",
            data={
                "display_name": "Updated Demo User",
                "email": "updated@example.test",
                "password": "V4lid!Second-Secret-2026",
                "status": "active",
            },
            follow_redirects=False,
        ).status_code == 303
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.pending_secret is None
            assert all(item.state == "chpw" for item in user.sync_states)

        client.post("/logout")
        assert client.post("/login", data={"username": "demo_user", "password": "V4lid!Second-Secret-2026"}, follow_redirects=False).headers["location"] == "/account/password-decision"
        httpx.post(f"{live_mock_url}/__mock__/fail/nexus").raise_for_status()
        second_replacement = "V4lid!Comet-Replacement-2026"
        assert client.post("/account/password-decision", data={
            "choice": "change", "current_password": "V4lid!Second-Secret-2026",
            "new_password": second_replacement, "confirm_password": second_replacement,
        }, follow_redirects=False).headers["location"] == "/login"
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.pending_secret is not None
            assert {item.target: item.state for item in user.sync_states}["nexus"] == "failed"

        client.post("/login", data={"username": "admin", "password": "demo-password"})
        status_page = client.get("/status")
        users_page = client.get("/users")
        assert "demo_user" not in status_page.text
        assert "User sync matrix" not in status_page.text
        assert "demo_user" in users_page.text and "failed" in users_page.text

        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            nexus_state = next(item for item in user.sync_states if item.target == "nexus")
            nexus_state.next_retry_at = nexus_state.next_retry_at.replace(year=2000)
            session.commit()
        import asyncio
        from na_sso.sync import retry_due
        assert asyncio.run(retry_due()) == 1
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.pending_secret is None
            assert all(item.state == "ok" for item in user.sync_states)

        assert client.post(
            f"/users/{user_id}",
            data={
                "display_name": "Updated Demo User",
                "email": "updated@example.test",
                "password": "",
                "status": "disabled",
            },
            follow_redirects=False,
        ).status_code == 303
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.status == "disabled"
            assert all(item.state == "ok" for item in user.sync_states)

        assert client.post(
            f"/users/{user_id}/delete", follow_redirects=False
        ).status_code == 303
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user is not None and user.deleted_at is not None
        audit_page = client.get("/audit")
        assert all(
            event in audit_page.text
            for event in ("user.create", "user.update", "auto-retry", "user.delete")
        )

    config.get_settings.cache_clear()
