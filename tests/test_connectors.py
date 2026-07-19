import httpx
import pytest
import respx
from httpx import Response

from na_sso.connectors.base import Connector, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import DriftState, ReconciliationField, ReconciliationStatus


class FakeConnector(Connector):
    name = "fake"

    def __init__(self):
        self.calls = []

    async def ensure_user(self, user, password):
        self.calls.append(("ensure", user.username, password))
        return SyncResult(True, "ok")

    async def disable_user(self, user):
        self.calls.append(("disable", user.username))
        return SyncResult(True)

    async def delete_user(self, user):
        self.calls.append(("delete", user.username))
        return SyncResult(True)

    async def probe(self):
        return SyncResult(True, "reachable")


def _user(username="jdoe", status="active"):
    return ManagedUser(username=username, display_name="J", email="j@x", status=status)


@respx.mock
async def test_gitlab_updates_and_unblocks_existing_user():
    from na_sso.config import GitlabTarget
    from na_sso.connectors.gitlab import GitlabConnector

    connector = GitlabConnector(GitlabTarget(
        id="gitlab", type="gitlab", display_name="GitLab",
        base_url="https://gitlab.test", api_token="token",
    ))
    respx.get("https://gitlab.test/api/v4/users").mock(
        return_value=Response(200, json=[{"id": 7, "username": "jdoe", "state": "blocked"}])
    )
    update = respx.put("https://gitlab.test/api/v4/users/7").mock(return_value=Response(200, json={}))
    unblock = respx.post("https://gitlab.test/api/v4/users/7/unblock").mock(return_value=Response(201, json={}))

    result = await connector.ensure_user(_user(), "new-password")

    assert result.ok and update.called and unblock.called
    assert update.calls[0].request.headers["private-token"] == "token"


@respx.mock
async def test_gitea_creates_user_through_admin_api():
    from na_sso.config import GiteaTarget
    from na_sso.connectors.gitea import GiteaConnector

    connector = GiteaConnector(GiteaTarget(
        id="gitea", type="gitea", display_name="Gitea",
        base_url="https://gitea.test", api_token="token",
    ))
    respx.get("https://gitea.test/api/v1/admin/users").mock(return_value=Response(200, json=[]))
    create = respx.post("https://gitea.test/api/v1/admin/users").mock(return_value=Response(201, json={}))

    result = await connector.ensure_user(_user(), "new-password")

    assert result.ok and create.called
    assert create.calls[0].request.headers["authorization"] == "token token"


@respx.mock
async def test_gitea_disables_user_found_beyond_the_first_listing_page():
    from na_sso.config import GiteaTarget
    from na_sso.connectors.gitea import GiteaConnector

    connector = GiteaConnector(GiteaTarget(
        id="gitea", type="gitea", display_name="Gitea",
        base_url="https://gitea.test", api_token="token",
    ))
    respx.get("https://gitea.test/api/v1/admin/users").mock(side_effect=[
        Response(200, json=[{"login": f"user{i}"} for i in range(50)]),
        Response(200, json=[{"login": "jdoe", "prohibit_login": False}]),
    ])
    update = respx.patch("https://gitea.test/api/v1/admin/users/jdoe").mock(return_value=Response(200, json={}))

    result = await connector.disable_user(_user())

    assert result.ok and update.called


@respx.mock
async def test_immich_restores_and_updates_soft_deleted_user():
    from na_sso.config import ImmichTarget
    from na_sso.connectors.immich import ImmichConnector

    connector = ImmichConnector(ImmichTarget(
        id="photos", type="immich", display_name="Immich",
        base_url="https://photos.test", api_token="token",
    ))
    respx.get("https://photos.test/api/admin/users").mock(return_value=Response(200, json=[
        {"id": "user-id", "email": "j@x", "name": "Old", "status": "deleted"},
    ]))
    restore = respx.post("https://photos.test/api/admin/users/user-id/restore").mock(return_value=Response(200, json={}))
    update = respx.put("https://photos.test/api/admin/users/user-id").mock(return_value=Response(200, json={}))

    result = await connector.ensure_user(_user(), "new-password")

    assert result.ok and restore.called and update.called
    assert update.calls[0].request.headers["x-api-key"] == "token"


@respx.mock
async def test_immich_ensure_of_disabled_user_skips_repeated_soft_delete():
    from na_sso.config import ImmichTarget
    from na_sso.connectors.immich import ImmichConnector

    connector = ImmichConnector(ImmichTarget(
        id="photos", type="immich", display_name="Immich",
        base_url="https://photos.test", api_token="token",
    ))
    respx.get("https://photos.test/api/admin/users").mock(return_value=Response(200, json=[
        {"id": "user-id", "email": "j@x", "name": "J", "status": "deleted"},
    ]))

    result = await connector.ensure_user(_user(status="disabled"), None)

    assert result.ok and result.detail == "already disabled"


def _npm_connector():
    from na_sso.config import NpmTarget
    from na_sso.connectors.npm import NpmConnector

    return NpmConnector(NpmTarget(
        id="npm", type="npm", display_name="Nginx Proxy Manager",
        base_url="https://npm.test", admin_user="admin@example.test",
        admin_password="admin-secret",
    ))


def _npm_token():
    return respx.post("https://npm.test/api/tokens").mock(
        return_value=Response(200, json={"token": "jwt-token", "expires": "later"})
    )


@respx.mock
async def test_npm_mints_token_and_creates_email_identified_user():
    connector = _npm_connector()
    token = _npm_token()
    respx.get("https://npm.test/api/users").mock(return_value=Response(200, json=[]))
    create = respx.post("https://npm.test/api/users").mock(
        return_value=Response(201, json={"id": 2})
    )

    result = await connector.ensure_user(_user(), "new-password")

    assert result.ok and result.detail == "created"
    assert token.calls[0].request.content == (
        b'{"identity":"admin@example.test","secret":"admin-secret"}'
    )
    assert create.calls[0].request.headers["authorization"] == "Bearer jwt-token"
    assert create.calls[0].request.content == (
        b'{"name":"J","nickname":"jdoe","email":"j@x","is_disabled":false,'
        b'"roles":[],"auth":{"type":"password","secret":"new-password"}}'
    )


@respx.mock
async def test_npm_updates_profile_status_and_password():
    connector = _npm_connector()
    _npm_token()
    respx.get("https://npm.test/api/users").mock(return_value=Response(200, json=[{
        "id": 7, "email": "J@X", "name": "Old", "nickname": "old",
        "is_disabled": False, "roles": [],
    }]))
    update = respx.put("https://npm.test/api/users/7").mock(
        return_value=Response(200, json={})
    )
    password = respx.put("https://npm.test/api/users/7/auth").mock(
        return_value=Response(200, json=True)
    )

    result = await connector.ensure_user(_user(status="disabled"), "new-password")

    assert result.ok and update.called and password.called
    assert b'"nickname":"jdoe"' in update.calls[0].request.content
    assert b'"is_disabled":true' in update.calls[0].request.content
    assert b'"roles"' not in update.calls[0].request.content


@respx.mock
async def test_npm_inspection_discovery_and_delete_absent_are_read_only_and_idempotent():
    connector = _npm_connector()
    token = respx.post("https://npm.test/api/tokens").mock(
        side_effect=[
            Response(200, json={"token": "one"}),
            Response(200, json={"token": "two"}),
            Response(200, json={"token": "three"}),
        ]
    )
    users = respx.get("https://npm.test/api/users").mock(
        side_effect=[
            Response(200, json=[{
                "id": 7, "email": "j@x", "name": "J", "nickname": "jdoe",
                "is_disabled": False, "roles": [],
            }]),
            Response(200, json=[{
                "id": 7, "email": "j@x", "name": "J", "nickname": "jdoe",
                "is_disabled": True, "roles": [],
            }]),
            Response(200, json=[]),
        ]
    )

    report = await connector.inspect_user(_user())
    discovery = await connector.discover_accounts()
    deleted = await connector.delete_user(_user())

    assert report.status is ReconciliationStatus.IN_SYNC
    assert discovery.supported and discovery.accounts[0].username == "j@x"
    assert discovery.accounts[0].status == "disabled"
    assert deleted.ok and deleted.detail == "already absent"
    assert token.call_count == users.call_count == 3
    assert respx.calls.last.request.method not in {"POST", "PUT", "PATCH", "DELETE"}


@respx.mock
async def test_npm_maps_authentication_unavailable_and_timeout_errors():
    from na_sso.connectors.base import ConnectorErrorKind

    connector = _npm_connector()
    token = respx.post("https://npm.test/api/tokens").mock(
        side_effect=[
            Response(400, json={"error": {"code": 400, "message": "bad"}}),
            httpx.ReadTimeout("late"),
            Response(429, json={"error": {"code": 429, "message": "slow down"}}),
            Response(200, json={"token": "jwt-token"}),
            Response(200, json={"requires_2fa": True, "challenge_token": "secret"}),
        ]
    )
    users = respx.get("https://npm.test/api/users").mock(
        return_value=Response(503, json={"error": {"code": 503, "message": "down"}})
    )

    authentication = await connector.probe()
    timeout = await connector.probe()
    rate_limited = await connector.probe()
    unavailable = await connector.probe()
    two_factor = await connector.probe()

    assert authentication.error_kind is ConnectorErrorKind.AUTHENTICATION
    assert timeout.error_kind is ConnectorErrorKind.TIMEOUT and timeout.retryable
    assert rate_limited.error_kind is ConnectorErrorKind.RATE_LIMITED
    assert rate_limited.retryable
    assert unavailable.error_kind is ConnectorErrorKind.UNAVAILABLE and unavailable.retryable
    assert two_factor.error_kind is ConnectorErrorKind.AUTHENTICATION
    assert "secret" not in two_factor.detail
    assert token.call_count == 5 and users.call_count == 1


async def test_npm_rejects_out_of_range_password_before_http():
    from na_sso.connectors.base import ConnectorErrorKind

    connector = _npm_connector()
    invalid = await connector.ensure_user(_user(), "short")

    assert not invalid.ok and invalid.error_kind is ConnectorErrorKind.VALIDATION


@respx.mock
async def test_jenkins_creates_local_realm_user_and_fails_disable_safely():
    from na_sso.config import JenkinsTarget
    from na_sso.connectors.jenkins import JenkinsConnector

    connector = JenkinsConnector(JenkinsTarget(
        id="ci", type="jenkins", display_name="Jenkins", base_url="https://ci.test",
        admin_user="admin", api_token="token",
    ))
    respx.get("https://ci.test/user/jdoe/api/json").mock(side_effect=[
        Response(404), Response(200, json={"id": "jdoe", "fullName": "J"}),
    ])
    respx.get("https://ci.test/crumbIssuer/api/json").mock(return_value=Response(200, json={
        "crumbRequestField": "Jenkins-Crumb", "crumb": "crumb-value",
    }))
    create = respx.post("https://ci.test/securityRealm/createAccountByAdmin").mock(return_value=Response(302))

    created = await connector.ensure_user(_user(), "new-password")
    disabled = await connector.disable_user(_user(status="disabled"))

    assert created.ok and create.called
    assert create.calls[0].request.headers["jenkins-crumb"] == "crumb-value"
    assert not disabled.ok and "cannot safely disable" in disabled.detail


@respx.mock
async def test_jenkins_reports_creation_rejected_by_the_200_signup_error_page():
    from na_sso.config import JenkinsTarget
    from na_sso.connectors.jenkins import JenkinsConnector

    connector = JenkinsConnector(JenkinsTarget(
        id="ci", type="jenkins", display_name="Jenkins", base_url="https://ci.test",
        admin_user="admin", api_token="token",
    ))
    respx.get("https://ci.test/user/jdoe/api/json").mock(return_value=Response(404))
    respx.get("https://ci.test/crumbIssuer/api/json").mock(return_value=Response(404))
    respx.post("https://ci.test/securityRealm/createAccountByAdmin").mock(
        return_value=Response(200, text="<html>signup error</html>")
    )

    result = await connector.ensure_user(_user(), "new-password")

    assert not result.ok and "rejected the account creation" in result.detail


def test_yaml_registry_preserves_order_repeated_types_and_capabilities(tmp_path, monkeypatch):
    path = tmp_path / "targets.yaml"
    path.write_text("""
targets:
  - {id: cloud_a, type: nextcloud, display_name: Cloud A, base_url: https://a,
     admin_user: admin, admin_password: secret}
  - {id: cloud_b, type: nextcloud, display_name: Cloud B, base_url: https://b,
     admin_user: admin, admin_password: other}
  - {id: shell_disabled, type: ssh, display_name: Shell, enabled: false, host: shell,
     management_user: mgr, management_private_key: key,
     host_key_sha256: "SHA256:AAAAAAAAAAAAAAAAAAAA", platform: debian}
""")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(path))
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(tmp_path / "registry.db"))
    from na_sso.config import get_settings
    import na_sso.db as database
    get_settings.cache_clear()
    database._engine = database._session_factory = None
    database.init_db()
    from na_sso.connectors import get_connectors
    assert get_connectors() == []
    database._engine = database._session_factory = None
    get_settings.cache_clear()


def test_cross_target_identity_validation_is_preflight():
    from na_sso.config import NexusTarget
    from na_sso.connectors import validate_for_targets
    from na_sso.connectors.nexus import NexusConnector
    connector = NexusConnector(NexusTarget(id="nexus_a", type="nexus", display_name="Nexus",
        base_url="https://nexus", admin_user="admin", admin_password="secret"))
    user = ManagedUser(username="jdoe", display_name="", email="")
    result = validate_for_targets(user, [connector])
    assert not result.ok and "requires email" in result.detail


def test_ssh_rejects_unrepresentable_and_nonportable_names_without_connecting():
    from na_sso.config import SshTarget
    from na_sso.connectors.ssh import SSHConnector
    target = SshTarget(id="shell", type="ssh", display_name="Shell", host="shell",
        management_user="mgr", management_private_key="key",
        host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="ubuntu")
    connector = SSHConnector(target)
    assert not connector.validate_identity(ManagedUser(username="bad/name")).ok
    assert not connector.validate_identity(ManagedUser(username="name with space")).ok
    assert not connector.validate_identity(ManagedUser(username="john.doe")).ok
    assert connector.validate_identity(ManagedUser(username="john-doe")).ok

    relaxed = target.model_copy(update={"allow_relaxed_usernames": True})
    assert SSHConnector(relaxed).validate_identity(ManagedUser(username="john.doe")).ok


async def test_ssh_combined_management_auth_passes_password_and_key(monkeypatch):
    from na_sso.config import SshTarget
    from na_sso.connectors.ssh import SSHConnector
    import na_sso.connectors.ssh as ssh_module

    captured = {}

    async def connect(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ssh_module.asyncssh, "import_private_key", lambda material: f"parsed:{material}")
    monkeypatch.setattr(ssh_module.asyncssh, "connect", connect)
    connector = SSHConnector(SshTarget(
        id="shell", type="ssh", display_name="Shell", host="shell",
        management_user="mgr", management_password="secret",
        management_private_key="private-material",
        host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="ubuntu",
    ))

    await connector._connect()

    assert captured["password"] == "secret"
    assert captured["client_keys"] == ["parsed:private-material"]

async def test_fake_connector_interface():
    fake = FakeConnector()
    res = await fake.ensure_user(_user(), "pw")
    assert res.ok and fake.calls == [("ensure", "jdoe", "pw")]


@respx.mock
async def test_opnsense_inspection_reports_drift_using_search_only(opnsense):
    search = respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": [{
            "name": "jdoe", "uuid": "u-1", "descr": "J", "email": "j@x",
            "disabled": "0", "group_memberships": "vpn-users",
        }]})
    )

    report = await opnsense.inspect_user(_user(status="disabled"))

    assert report.status == ReconciliationStatus.DRIFTED
    assert report.field(ReconciliationField.STATUS).state == DriftState.DRIFT
    assert report.field(ReconciliationField.PUBLIC_KEY).state == DriftState.UNSUPPORTED
    assert search.call_count == 1
    assert [call.request.url.path for call in respx.calls] == ["/api/auth/user/search"]


@respx.mock
async def test_opnsense_inspection_failure_is_unknown_and_sanitised(opnsense):
    respx.post("https://fw.test/api/auth/user/search").mock(return_value=Response(503))

    report = await opnsense.inspect_user(_user())

    assert report.status == ReconciliationStatus.UNKNOWN
    assert report.field("identity").state == DriftState.UNKNOWN
    assert report.detail == "OPNsense identity read failed."
    assert "secret" not in report.detail.lower()


@pytest.fixture()
def opnsense(monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_OPNSENSE_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_OPNSENSE_BASE_URL", "https://fw.test")
    monkeypatch.setenv("NA_SSO_OPNSENSE_API_KEY", "k")
    monkeypatch.setenv("NA_SSO_OPNSENSE_API_SECRET", "s")
    config.get_settings.cache_clear()
    from na_sso.connectors.opnsense import OPNsenseConnector

    yield OPNsenseConnector(config.get_settings())
    config.get_settings.cache_clear()


@respx.mock
async def test_opnsense_create_user(opnsense):
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    add = respx.post("https://fw.test/api/auth/user/add").mock(
        return_value=Response(200, json={"result": "saved"})
    )
    res = await opnsense.ensure_user(_user(), "pw-123")
    assert res.ok and add.called
    import json

    body = json.loads(add.calls[0].request.content)
    assert body["user"]["name"] == "jdoe" and body["user"]["password"] == "pw-123"


@respx.mock
async def test_opnsense_applies_default_groups():
    from na_sso.config import OpnsenseTarget
    from na_sso.connectors.opnsense import OPNsenseConnector
    connector = OPNsenseConnector(OpnsenseTarget(id="fw", type="opnsense", display_name="FW",
        base_url="https://groups.test", api_key="key", api_secret="secret",
        default_groups=["vpn-users", "auditors"]))
    respx.post("https://groups.test/api/auth/user/search").mock(return_value=Response(200, json={"rows": []}))
    add = respx.post("https://groups.test/api/auth/user/add").mock(return_value=Response(200, json={"result": "saved"}))
    assert (await connector.ensure_user(_user(), "pw")).ok
    import json
    assert set(json.loads(add.calls[0].request.content)["user"]["group_memberships"].split(",")) == {"vpn-users", "auditors"}


@respx.mock
async def test_opnsense_update_and_disable(opnsense):
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": [{"name": "jdoe", "uuid": "u-1"}]})
    )
    set_route = respx.post("https://fw.test/api/auth/user/set/u-1").mock(
        return_value=Response(200, json={"result": "saved"})
    )
    res = await opnsense.disable_user(_user(status="disabled"))
    assert res.ok and set_route.called
    import json

    assert json.loads(set_route.calls[0].request.content)["user"]["disabled"] == "1"


@respx.mock
async def test_opnsense_delete_absent_is_ok(opnsense):
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    res = await opnsense.delete_user(_user())
    assert res.ok and "absent" in res.detail


@respx.mock
async def test_opnsense_probe_failure(opnsense):
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(401)
    )
    res = await opnsense.probe()
    assert not res.ok


def test_status_page_lists_targets(admin_client, monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_OPNSENSE_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_OPNSENSE_BASE_URL", "https://fw.invalid")
    config.get_settings.cache_clear()
    try:
        r = admin_client.get("/status")
        assert r.status_code == 200
        assert "opnsense" in r.text and "unreachable" in r.text
    finally:
        config.get_settings.cache_clear()


@pytest.fixture()
def nexus(monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_NEXUS_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_NEXUS_BASE_URL", "https://nexus.test")
    monkeypatch.setenv("NA_SSO_NEXUS_ADMIN_USER", "admin")
    monkeypatch.setenv("NA_SSO_NEXUS_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("NA_SSO_NEXUS_DEFAULT_ROLES", "nx-reader,nx-anonymous")
    config.get_settings.cache_clear()
    from na_sso.connectors.nexus import NexusConnector

    yield NexusConnector(config.get_settings())
    config.get_settings.cache_clear()


@respx.mock
async def test_nexus_create_user(nexus):
    respx.get("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(200, json=[])
    )
    create = respx.post("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(200, json={})
    )
    result = await nexus.ensure_user(_user(), "pw-123")
    assert result.ok and create.called
    import json

    body = json.loads(create.calls[0].request.content)
    assert body["userId"] == "jdoe" and body["password"] == "pw-123"
    assert set(body["roles"]) == {"nx-reader", "nx-anonymous"}


@respx.mock
async def test_nexus_inspection_is_in_sync_and_get_only(nexus):
    lookup = respx.get("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(200, json=[{
            "userId": "jdoe", "firstName": "J", "lastName": "",
            "emailAddress": "j@x", "status": "active",
            "roles": ["nx-reader", "nx-anonymous"],
        }])
    )

    report = await nexus.inspect_user(_user())

    assert report.status == ReconciliationStatus.IN_SYNC
    assert report.field("memberships").state == DriftState.MATCH
    assert lookup.call_count == 1
    assert all(call.request.method == "GET" for call in respx.calls)


@respx.mock
async def test_nexus_update_password_and_disable(nexus):
    existing = {
        "userId": "jdoe",
        "firstName": "Old",
        "lastName": "Name",
        "emailAddress": "old@example.test",
        "source": "default",
        "status": "active",
        "readOnly": False,
        "roles": ["nx-reader"],
        "externalRoles": [],
    }
    respx.get("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(200, json=[existing])
    )
    update = respx.put(
        "https://nexus.test/service/rest/v1/security/users/jdoe"
    ).mock(return_value=Response(204))
    password = respx.put(
        "https://nexus.test/service/rest/v1/security/users/jdoe/change-password"
    ).mock(return_value=Response(204))

    result = await nexus.ensure_user(_user(status="disabled"), "new-password")
    assert result.ok and update.called and password.called
    import json

    assert json.loads(update.calls[0].request.content)["status"] == "disabled"
    assert password.calls[0].request.content == b"new-password"


@respx.mock
async def test_nexus_delete_absent_is_ok(nexus):
    respx.delete("https://nexus.test/service/rest/v1/security/users/jdoe").mock(
        return_value=Response(404)
    )
    result = await nexus.delete_user(_user())
    assert result.ok and "absent" in result.detail


@respx.mock
async def test_nexus_probe_failure(nexus):
    respx.get("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(401)
    )
    result = await nexus.probe()
    assert not result.ok


@respx.mock
def test_status_page_lists_nexus(admin_client, monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_NEXUS_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_NEXUS_BASE_URL", "https://nexus.test")
    monkeypatch.setenv("NA_SSO_NEXUS_ADMIN_USER", "admin")
    monkeypatch.setenv("NA_SSO_NEXUS_ADMIN_PASSWORD", "secret")
    config.get_settings.cache_clear()
    respx.get("https://nexus.test/service/rest/v1/security/users").mock(
        return_value=Response(200, json=[])
    )
    try:
        response = admin_client.get("/status")
        assert response.status_code == 200
        assert "nexus" in response.text and "reachable" in response.text
    finally:
        config.get_settings.cache_clear()


def _ocs(code=100, message="OK"):
    return {"ocs": {"meta": {"statuscode": code, "message": message}, "data": {}}}


@pytest.fixture()
def nextcloud(monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ADMIN_USER", "admin")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ADMIN_PASSWORD", "app-password")
    config.get_settings.cache_clear()
    from na_sso.connectors.nextcloud import NextcloudConnector

    yield NextcloudConnector(config.get_settings())
    config.get_settings.cache_clear()


@respx.mock
async def test_nextcloud_create_user(nextcloud):
    respx.get("https://cloud.test/ocs/v1.php/cloud/users/jdoe").mock(
        return_value=Response(200, json=_ocs(998, "not found"))
    )
    create = respx.post("https://cloud.test/ocs/v1.php/cloud/users").mock(
        return_value=Response(200, json=_ocs())
    )
    enable = respx.put("https://cloud.test/ocs/v1.php/cloud/users/jdoe/enable").mock(
        return_value=Response(200, json=_ocs())
    )
    result = await nextcloud.ensure_user(_user(), "pw-123")
    assert result.ok and create.called and enable.called
    assert b"userid=jdoe" in create.calls[0].request.content
    assert b"password=pw-123" in create.calls[0].request.content


@respx.mock
async def test_nextcloud_inspection_reads_profile_and_groups_without_mutation():
    from na_sso.config import NextcloudTarget
    from na_sso.connectors.nextcloud import NextcloudConnector

    connector = NextcloudConnector(NextcloudTarget(
        id="cloud", type="nextcloud", display_name="Cloud",
        base_url="https://inspect.test", admin_user="admin", admin_password="secret",
        default_groups=["employees", "engineering"],
    ))
    respx.get("https://inspect.test/ocs/v1.php/cloud/users/jdoe").mock(
        return_value=Response(200, json={"ocs": {
            "meta": {"statuscode": 100, "message": "OK"},
            "data": {"id": "jdoe", "displayname": "J", "email": "j@x", "enabled": True},
        }})
    )
    respx.get("https://inspect.test/ocs/v1.php/cloud/users/jdoe/groups").mock(
        return_value=Response(200, json={"ocs": {
            "meta": {"statuscode": 100, "message": "OK"},
            "data": {"groups": ["employees", "optional"]},
        }})
    )

    report = await connector.inspect_user(_user())

    assert report.status == ReconciliationStatus.DRIFTED
    assert report.field("memberships").state == DriftState.DRIFT
    assert all(call.request.method == "GET" for call in respx.calls)


async def test_ssh_inspection_uses_read_only_commands_and_fingerprints_key(monkeypatch):
    from na_sso.config import SshTarget
    from na_sso.connectors.ssh import SSHConnector

    public_key = "ssh-ed25519 YWJj managed@example.test"

    class Result:
        def __init__(self, status=0, stdout="", stderr=""):
            self.exit_status, self.stdout, self.stderr = status, stdout, stderr

    class Connection:
        def __init__(self):
            self.commands = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def run(self, command, **kwargs):
            self.commands.append(command)
            if command.startswith("getent passwd"):
                return Result(stdout="jdoe:x:1000:1000:J:/home/jdoe:/bin/bash\n")
            if command.startswith("id -nG"):
                return Result(stdout="jdoe operators extra\n")
            if command.startswith("sudo -n passwd -S"):
                return Result(stdout="jdoe P 2026-07-15 0 99999 7 -1\n")
            if command.startswith("sudo -n test -f"):
                return Result()
            if command.startswith("sudo -n cat"):
                return Result(stdout=public_key + "\n")
            raise AssertionError(f"unexpected command: {command}")

    connection = Connection()
    connector = SSHConnector(SshTarget(
        id="shell", type="ssh", display_name="Shell", host="shell",
        management_user="mgr", management_private_key="key",
        host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="ubuntu",
        mode="key", default_groups=["operators"],
    ))

    async def connect():
        return connection

    monkeypatch.setattr(connector, "_connect", connect)
    user = _user()
    user.ssh_public_key = public_key

    report = await connector.inspect_user(user)

    assert report.status == ReconciliationStatus.IN_SYNC
    assert report.field("public_key").state == DriftState.MATCH
    assert report.field("email").state == DriftState.UNSUPPORTED
    assert report.field("memberships").state == DriftState.MATCH
    mutation_words = {"adduser", "useradd", "usermod", "userdel", "chpasswd", "tee", "install"}
    assert not mutation_words.intersection(" ".join(connection.commands).split())


@respx.mock
async def test_nextcloud_applies_default_groups():
    from na_sso.config import NextcloudTarget
    from na_sso.connectors.nextcloud import NextcloudConnector
    connector = NextcloudConnector(NextcloudTarget(id="cloud", type="nextcloud", display_name="Cloud",
        base_url="https://groups.test", admin_user="admin", admin_password="secret",
        default_groups=["employees", "engineering"]))
    respx.get("https://groups.test/ocs/v1.php/cloud/users/jdoe").mock(return_value=Response(200, json=_ocs(998)))
    create = respx.post("https://groups.test/ocs/v1.php/cloud/users").mock(return_value=Response(200, json=_ocs()))
    respx.get("https://groups.test/ocs/v1.php/cloud/users/jdoe/groups").mock(return_value=Response(200, json={"ocs": {"meta": {"statuscode": 100}, "data": {"groups": ["employees"]}}}))
    add_group = respx.post("https://groups.test/ocs/v1.php/cloud/users/jdoe/groups").mock(return_value=Response(200, json=_ocs()))
    respx.put("https://groups.test/ocs/v1.php/cloud/users/jdoe/enable").mock(return_value=Response(200, json=_ocs()))
    assert (await connector.ensure_user(_user(), "pw")).ok
    assert b"groups%5B%5D=employees" in create.calls[0].request.content
    assert add_group.call_count == 1 and b"engineering" in add_group.calls[0].request.content


@respx.mock
async def test_nextcloud_update_password_and_disable(nextcloud):
    respx.get("https://cloud.test/ocs/v1.php/cloud/users/jdoe").mock(
        return_value=Response(200, json=_ocs())
    )
    edit = respx.put("https://cloud.test/ocs/v1.php/cloud/users/jdoe").mock(
        return_value=Response(200, json=_ocs())
    )
    disable = respx.put(
        "https://cloud.test/ocs/v1.php/cloud/users/jdoe/disable"
    ).mock(return_value=Response(200, json=_ocs()))
    result = await nextcloud.ensure_user(_user(status="disabled"), "new-password")
    assert result.ok and edit.call_count == 3 and disable.called
    assert b"key=password" in edit.calls[-1].request.content


@respx.mock
async def test_nextcloud_delete_absent_is_ok(nextcloud):
    respx.get("https://cloud.test/ocs/v1.php/cloud/users/jdoe").mock(
        return_value=Response(200, json=_ocs(998, "not found"))
    )
    result = await nextcloud.delete_user(_user())
    assert result.ok and "absent" in result.detail


@respx.mock
async def test_nextcloud_probe_failure(nextcloud):
    respx.get("https://cloud.test/ocs/v1.php/cloud/users").mock(
        return_value=Response(401)
    )
    result = await nextcloud.probe()
    assert not result.ok


@respx.mock
def test_status_page_lists_nextcloud(admin_client, monkeypatch):
    import na_sso.config as config

    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ENABLED", "true")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ADMIN_USER", "admin")
    monkeypatch.setenv("NA_SSO_NEXTCLOUD_ADMIN_PASSWORD", "app-password")
    config.get_settings.cache_clear()
    respx.get("https://cloud.test/ocs/v1.php/cloud/users").mock(
        return_value=Response(200, json=_ocs())
    )
    try:
        response = admin_client.get("/status")
        assert response.status_code == 200
        assert "nextcloud" in response.text and "reachable" in response.text
    finally:
        config.get_settings.cache_clear()
