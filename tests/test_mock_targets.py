import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from na_sso.config import GiteaTarget, GitlabTarget, ImmichTarget, JenkinsTarget, NpmTarget, Settings
from na_sso.connectors.gitea import GiteaConnector
from na_sso.connectors.gitlab import GitlabConnector
from na_sso.connectors.immich import ImmichConnector
from na_sso.connectors.jenkins import JenkinsConnector
from na_sso.connectors.nextcloud import NextcloudConnector
from na_sso.connectors.nexus import NexusConnector
from na_sso.connectors.npm import NpmConnector
from na_sso.connectors.opnsense import OPNsenseConnector
from na_sso.mock_targets.app import app
from na_sso.models import ManagedUser
from na_sso.reconciliation import ReconciliationStatus


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


def _npm_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/tokens",
        json={"identity": "admin@example.test", "secret": "demo-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


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


def test_gitlab_mock_user_lifecycle():
    client = _client()
    headers = {"PRIVATE-TOKEN": "demo-token"}
    created = client.post("/api/v4/users", headers=headers, json={
        "username": "alice", "name": "Alice Example", "email": "alice@example.test",
        "password": "first-secret",
    })
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert client.put(f"/api/v4/users/{user_id}", headers=headers, json={
        "name": "Updated Alice", "password": "second-secret",
    }).status_code == 200
    assert client.post(f"/api/v4/users/{user_id}/block", headers=headers).status_code == 201
    user = client.get("/api/v4/users", headers=headers, params={"username": "alice"}).json()[0]
    assert (user["name"], user["state"]) == ("Updated Alice", "blocked")
    assert "password" not in user
    assert client.delete(f"/api/v4/users/{user_id}", headers=headers).status_code == 204


def test_gitea_mock_user_lifecycle():
    client = _client()
    headers = {"Authorization": "token demo-token"}
    assert client.post("/api/v1/admin/users", headers=headers, json={
        "username": "alice", "full_name": "Alice Example", "email": "alice@example.test",
        "password": "first-secret",
    }).status_code == 201
    updated = client.patch("/api/v1/admin/users/alice", headers=headers, json={
        "login_name": "alice", "source_id": 0, "full_name": "Updated Alice",
        "active": False, "prohibit_login": True, "password": "second-secret",
    })
    assert updated.status_code == 200
    user = client.get("/api/v1/admin/users", headers=headers).json()[0]
    assert (user["full_name"], user["prohibit_login"]) == ("Updated Alice", True)
    assert "password" not in user
    assert client.delete("/api/v1/admin/users/alice", headers=headers).status_code == 204


def test_immich_mock_user_lifecycle():
    client = _client()
    headers = {"x-api-key": "demo-token"}
    created = client.post("/api/admin/users", headers=headers, json={
        "email": "alice@example.test", "name": "Alice Example", "password": "first-secret",
    })
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert client.put(f"/api/admin/users/{user_id}", headers=headers, json={
        "name": "Updated Alice", "password": "second-secret",
    }).status_code == 200
    assert client.request("DELETE", f"/api/admin/users/{user_id}", headers=headers, json={"force": False}).status_code == 200
    assert client.get("/api/admin/users", headers=headers).json() == []
    deleted = client.get("/api/admin/users", headers=headers, params={"withDeleted": "true"}).json()[0]
    assert (deleted["name"], deleted["status"]) == ("Updated Alice", "deleted")
    assert client.post(f"/api/admin/users/{user_id}/restore", headers=headers).json()["status"] == "active"
    assert client.request("DELETE", f"/api/admin/users/{user_id}", headers=headers, json={"force": True}).status_code == 200


def test_jenkins_mock_local_realm_lifecycle():
    client = _client()
    auth = ("admin", "demo-token")
    assert client.get("/api/json", auth=auth).status_code == 200
    assert client.post("/securityRealm/createAccountByAdmin", auth=auth, data={
        "username": "alice", "password1": "first-secret", "password2": "first-secret",
        "fullname": "Alice Example", "email": "alice@example.test",
    }, follow_redirects=False).status_code == 303
    user = client.get("/user/alice/api/json", auth=auth).json()
    assert (user["id"], user["fullName"]) == ("alice", "Alice Example")
    assert "password" not in user
    assert client.post("/user/alice/doDelete", auth=auth, follow_redirects=False).status_code == 303
    assert client.get("/user/alice/api/json", auth=auth).status_code == 404


def test_npm_mock_token_minting_and_bearer_enforcement():
    client = _client()
    bad_credentials = client.post(
        "/api/tokens",
        json={"identity": "admin@example.test", "secret": "wrong-password"},
    )
    assert bad_credentials.status_code == 400
    assert bad_credentials.json() == {
        "error": {"code": 400, "message": "Invalid email or password"}
    }

    minted = client.post(
        "/api/tokens",
        json={"identity": " ADMIN@EXAMPLE.TEST ", "secret": "demo-password"},
    )
    assert minted.status_code == 200
    assert set(minted.json()) == {"token", "expires"}

    missing = client.get("/api/users")
    assert missing.status_code == 401
    assert missing.json() == {"error": {"code": 401, "message": "Invalid token"}}
    unknown = client.get(
        "/api/users", headers={"Authorization": "Bearer never-minted"}
    )
    assert unknown.status_code == 401
    assert unknown.json() == {"error": {"code": 401, "message": "Invalid token"}}
    assert client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {minted.json()['token']}"},
    ).status_code == 200


def test_npm_mock_rejects_disabled_user_token():
    client = _client()
    headers = _npm_headers(client)
    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Disabled User",
            "email": "disabled@example.test",
            "nickname": "disabled",
            "is_disabled": True,
            "auth": {"type": "password", "secret": "disabled-secret"},
        },
    )
    assert created.status_code == 201

    rejected = client.post(
        "/api/tokens",
        json={"identity": "DISABLED@EXAMPLE.TEST", "secret": "disabled-secret"},
    )
    assert rejected.status_code == 400
    assert rejected.json() == {
        "error": {"code": 400, "message": "Invalid email or password"}
    }


def test_npm_mock_user_lifecycle_and_soft_delete_recreate():
    client = _client()
    headers = _npm_headers(client)
    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Alice Example",
            "email": "alice@example.test",
            "nickname": "alice",
            "roles": [],
            "auth": {"type": "password", "secret": "first-secret"},
        },
    )
    assert created.status_code == 201
    user = created.json()
    user_id = user["id"]
    assert set(user) == {
        "id", "created_on", "modified_on", "is_disabled", "email", "name",
        "nickname", "avatar", "roles",
    }
    assert (user["is_disabled"], user["roles"]) == (False, [])

    listed = client.get("/api/users", headers=headers).json()
    assert user_id in {item["id"] for item in listed}
    assert client.get(f"/api/users/{user_id}", headers=headers).json() == user

    updated = client.put(
        f"/api/users/{user_id}",
        headers=headers,
        json={
            "name": "Updated Alice",
            "email": "updated-alice@example.test",
            "nickname": "alice-updated",
            "is_disabled": True,
        },
    )
    assert updated.status_code == 200
    assert (
        updated.json()["name"],
        updated.json()["email"],
        updated.json()["nickname"],
        updated.json()["is_disabled"],
    ) == ("Updated Alice", "updated-alice@example.test", "alice-updated", True)
    assert client.put(
        f"/api/users/{user_id}/auth",
        headers=headers,
        json={"type": "password", "secret": "second-secret"},
    ).json() is True

    deleted = client.delete(f"/api/users/{user_id}", headers=headers)
    assert deleted.status_code == 200
    assert user_id not in {
        item["id"] for item in client.get("/api/users", headers=headers).json()
    }
    missing = client.delete(f"/api/users/{user_id}", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": 404, "message": "Not Found"}}

    recreated = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Recreated Alice",
            "email": "updated-alice@example.test",
            "nickname": "alice-recreated",
        },
    )
    assert recreated.status_code == 201
    assert recreated.json()["id"] != user_id


def test_npm_mock_allows_duplicate_create_but_rejects_duplicate_update():
    client = _client()
    headers = _npm_headers(client)
    first = client.post(
        "/api/users",
        headers=headers,
        json={"name": "First", "email": "first@example.test", "nickname": "first"},
    )
    duplicate_create = client.post(
        "/api/users",
        headers=headers,
        json={"name": "Duplicate", "email": "first@example.test", "nickname": "duplicate"},
    )
    second = client.post(
        "/api/users",
        headers=headers,
        json={"name": "Second", "email": "second@example.test", "nickname": "second"},
    )
    assert first.status_code == duplicate_create.status_code == second.status_code == 201

    rejected = client.put(
        f"/api/users/{second.json()['id']}",
        headers=headers,
        json={"email": "first@example.test"},
    )
    assert rejected.status_code == 400
    assert rejected.json() == {
        "error": {
            "code": 400,
            "message": "Email address already in use - first@example.test",
        }
    }


@pytest.mark.parametrize("secret", ["1234567", "x" * 65])
def test_npm_mock_validates_password_length(secret):
    client = _client()
    headers = _npm_headers(client)
    created = client.post(
        "/api/users",
        headers=headers,
        json={"name": "Alice", "email": "alice@example.test", "nickname": "alice"},
    )
    response = client.put(
        f"/api/users/{created.json()['id']}/auth",
        headers=headers,
        json={"type": "password", "secret": secret},
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": 400,
            "message": "Password must be between 8 and 64 characters",
        }
    }


def test_npm_mock_fail_next_uses_npm_error_envelope():
    client = _client()
    headers = _npm_headers(client)
    assert client.post("/__mock__/fail/npm").json() == {
        "status": "armed", "target": "npm",
    }
    failed = client.get("/api/users", headers=headers)
    assert failed.status_code == 503
    assert failed.json() == {
        "error": {"code": 503, "message": "injected npm failure"}
    }
    assert client.get("/api/users", headers=headers).status_code == 200


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
    assert client.post("/__mock__/fail/gitlab").json() == {
        "status": "armed", "target": "gitlab",
    }
    assert client.get("/api/v4/user", headers={"PRIVATE-TOKEN": "demo-token"}).status_code == 503
    assert client.get("/api/v4/user", headers={"PRIVATE-TOKEN": "demo-token"}).status_code == 200


def test_target_wide_availability_controls():
    client = _client()
    page = client.get("/")
    assert page.status_code == 200 and "Mock target controls" in page.text
    assert all(label in page.text for label in (
        "OPNsense", "Nexus Repository", "Nextcloud", "Jenkins", "GitLab", "Gitea", "Immich",
        "Nginx Proxy Manager",
    ))
    assert page.text.count('class="card stack-2"') == 8
    assert all(asset in page.text for asset in (
        "/design/styles.css", "/design/components.css", "/static/favicon.svg",
        "/static/favicon.ico", "/static/apple-touch-icon.png",
    ))
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
    assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
    user.display_name = "Updated User"
    assert (await connector.ensure_user(user, "second-secret")).ok
    user.status = "disabled"
    assert (await connector.disable_user(user)).ok
    assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
    assert (await connector.delete_user(user)).ok
    user.desired_action = "delete"
    assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
    assert (await connector.delete_user(user)).ok


@pytest.mark.parametrize("target_type", ["gitlab", "gitea", "immich", "jenkins", "npm"])
async def test_new_connector_lifecycle_over_real_http(live_mock_url, target_type):
    async with httpx.AsyncClient() as client:
        assert (await client.post(f"{live_mock_url}/__mock__/reset")).status_code == 200
    connector = {
        "gitlab": lambda: GitlabConnector(GitlabTarget(
            id="gitlab", type="gitlab", display_name="GitLab", base_url=live_mock_url,
            api_token="demo-token", verify_tls=False,
        )),
        "gitea": lambda: GiteaConnector(GiteaTarget(
            id="gitea", type="gitea", display_name="Gitea", base_url=live_mock_url,
            api_token="demo-token", verify_tls=False,
        )),
        "immich": lambda: ImmichConnector(ImmichTarget(
            id="immich", type="immich", display_name="Immich", base_url=live_mock_url,
            api_token="demo-token", verify_tls=False,
        )),
        "jenkins": lambda: JenkinsConnector(JenkinsTarget(
            id="jenkins", type="jenkins", display_name="Jenkins", base_url=live_mock_url,
            admin_user="admin", api_token="demo-token", verify_tls=False,
        )),
        "npm": lambda: NpmConnector(NpmTarget(
            id="npm", type="npm", display_name="Nginx Proxy Manager",
            base_url=live_mock_url, admin_user="admin@example.test",
            admin_password="demo-password", verify_tls=False,
        )),
    }[target_type]()
    user = ManagedUser(
        username="integration_user", display_name="Integration User",
        email="integration@example.test", status="active",
    )

    assert (await connector.probe()).ok
    assert (await connector.ensure_user(user, "first-secret")).ok
    assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
    if target_type == "jenkins":
        disabled = await connector.disable_user(user)
        assert not disabled.ok and "cannot safely disable" in disabled.detail
    else:
        user.status = "disabled"
        assert (await connector.disable_user(user)).ok
        assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
    assert (await connector.delete_user(user)).ok
    user.desired_action = "delete"
    assert (await connector.inspect_user(user)).status == ReconciliationStatus.IN_SYNC
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
        user_detail = client.get(f"/users/{user_id}")
        assert "demo_user" not in status_page.text
        assert "User sync matrix" not in status_page.text
        assert "demo_user" in users_page.text and "needs attention" in users_page.text
        assert "Retrying" in user_detail.text

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


def test_npm_application_lifecycle_against_live_mock(
    live_mock_url, tmp_path, monkeypatch
):
    config_path = tmp_path / "npm-target.yaml"
    config_path.write_text(f"""version: 1
targets:
  - id: npm
    type: npm
    display_name: Nginx Proxy Manager
    base_url: {live_mock_url}
    verify_tls: false
""")
    target_settings = {
        "NA_SSO_CONFIG_FILE": str(config_path),
        "NA_SSO_DATABASE_PATH": str(tmp_path / "npm-app.db"),
        "NA_SSO_SECRET_KEY": "npm-app-test-secret",
        "NA_SSO_ADMIN_USERNAME": "admin",
        "NA_SSO_ADMIN_BOOTSTRAP_PASSWORD": "demo-password",
    }
    for key, value in target_settings.items():
        monkeypatch.setenv(key, value)

    import na_sso.config as config
    import na_sso.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    httpx.post(f"{live_mock_url}/__mock__/reset").raise_for_status()

    from na_sso.main import app as na_sso_app

    with TestClient(na_sso_app) as client:
        assert client.post(
            "/login",
            data={"username": "admin", "password": "demo-password"},
            follow_redirects=False,
        ).status_code == 303
        assert client.post(
            "/targets/npm/credentials",
            data={
                "auth_mode": "password",
                "admin_user": "admin@example.test",
                "password": "demo-password",
            },
            follow_redirects=False,
        ).status_code == 303
        status_page = client.get("/status")
        assert "Nginx Proxy Manager" in status_page.text
        assert "fully configured" in status_page.text

        initial_password = "V4lid!Orbit-Initial-Secret-2026"
        assert client.post(
            "/users/new",
            data={
                "username": "npm_user",
                "display_name": "NPM User",
                "email": "npm-user@example.test",
                "password": initial_password,
                "target_ids": "npm",
            },
            follow_redirects=False,
        ).status_code == 303
        with db.get_session() as session:
            user = session.query(ManagedUser).filter_by(username="npm_user").one()
            user_id = user.id
            assert {item.target: item.state for item in user.sync_states} == {
                "npm": "chpw"
            }

        client.post("/logout")
        assert client.post(
            "/login",
            data={"username": "npm_user", "password": initial_password},
            follow_redirects=False,
        ).headers["location"] == "/account/password-decision"
        replacement = "V4lid!Comet-Replacement-Secret-2026"
        assert client.post(
            "/account/password-decision",
            data={
                "choice": "change",
                "current_password": initial_password,
                "new_password": replacement,
                "confirm_password": replacement,
            },
            follow_redirects=False,
        ).headers["location"] == "/login"
        with db.get_session() as session:
            user = session.get(ManagedUser, user_id)
            assert user.pending_secret is None
            assert {item.target: item.state for item in user.sync_states} == {
                "npm": "ok"
            }

        token = httpx.post(
            f"{live_mock_url}/api/tokens",
            json={"identity": "admin@example.test", "secret": "demo-password"},
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        remote_users = httpx.get(
            f"{live_mock_url}/api/users", headers=headers
        ).json()
        assert any(
            item["email"] == "npm-user@example.test"
            and item["nickname"] == "npm_user"
            and not item["is_disabled"]
            for item in remote_users
        )

        client.post("/login", data={"username": "admin", "password": "demo-password"})
        assert client.post(
            f"/users/{user_id}",
            data={
                "display_name": "NPM User",
                "email": "npm-user@example.test",
                "password": "",
                "status": "disabled",
            },
            follow_redirects=False,
        ).status_code == 303
        remote_users = httpx.get(
            f"{live_mock_url}/api/users", headers=headers
        ).json()
        assert next(
            item for item in remote_users if item["email"] == "npm-user@example.test"
        )["is_disabled"] is True

        import asyncio

        from na_sso.connectors import get_connectors
        discovery = asyncio.run(get_connectors()[0].discover_accounts())
        assert discovery.supported
        assert any(account.username == "npm-user@example.test" for account in discovery.accounts)

        assert client.post(
            f"/users/{user_id}/delete", follow_redirects=False
        ).status_code == 303
        remote_users = httpx.get(
            f"{live_mock_url}/api/users", headers=headers
        ).json()
        assert all(item["email"] != "npm-user@example.test" for item in remote_users)
        audit_page = client.get("/audit")
        assert all(
            event in audit_page.text
            for event in ("user.create", "user.update", "user.delete")
        )

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
