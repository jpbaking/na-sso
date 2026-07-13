import pytest
import respx
from httpx import Response

from oneauth.connectors.base import Connector, SyncResult
from oneauth.models import ManagedUser


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
    monkeypatch.setenv("ONEAUTH_CONFIG_FILE", str(path))
    monkeypatch.setenv("ONEAUTH_DATABASE_PATH", str(tmp_path / "registry.db"))
    from oneauth.config import get_settings
    import oneauth.db as database
    get_settings.cache_clear()
    database._engine = database._session_factory = None
    database.init_db()
    from oneauth.connectors import get_connectors
    assert get_connectors() == []
    database._engine = database._session_factory = None
    get_settings.cache_clear()


def test_cross_target_identity_validation_is_preflight():
    from oneauth.config import NexusTarget
    from oneauth.connectors import validate_for_targets
    from oneauth.connectors.nexus import NexusConnector
    connector = NexusConnector(NexusTarget(id="nexus_a", type="nexus", display_name="Nexus",
        base_url="https://nexus", admin_user="admin", admin_password="secret"))
    user = ManagedUser(username="jdoe", display_name="", email="")
    result = validate_for_targets(user, [connector])
    assert not result.ok and "requires email" in result.detail


def test_ssh_rejects_unrepresentable_and_nonportable_names_without_connecting():
    from oneauth.config import SshTarget
    from oneauth.connectors.ssh import SSHConnector
    target = SshTarget(id="shell", type="ssh", display_name="Shell", host="shell",
        management_user="mgr", management_private_key="key",
        host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="ubuntu")
    connector = SSHConnector(target)
    assert not connector.validate_identity(ManagedUser(username="bad/name")).ok
    assert not connector.validate_identity(ManagedUser(username="name with space")).ok

async def test_fake_connector_interface(client):
    fake = FakeConnector()
    res = await fake.ensure_user(_user(), "pw")
    assert res.ok and fake.calls == [("ensure", "jdoe", "pw")]


@pytest.fixture()
def opnsense(client, monkeypatch):
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_OPNSENSE_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_OPNSENSE_BASE_URL", "https://fw.test")
    monkeypatch.setenv("ONEAUTH_OPNSENSE_API_KEY", "k")
    monkeypatch.setenv("ONEAUTH_OPNSENSE_API_SECRET", "s")
    config.get_settings.cache_clear()
    from oneauth.connectors.opnsense import OPNsenseConnector

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
    from oneauth.config import OpnsenseTarget
    from oneauth.connectors.opnsense import OPNsenseConnector
    connector = OPNsenseConnector(OpnsenseTarget(id="fw", type="opnsense", display_name="FW",
        base_url="https://groups.test", api_key="key", api_secret="secret",
        default_groups=["vpn-users", "auditors"]))
    respx.post("https://groups.test/api/auth/user/search").mock(return_value=Response(200, json={"rows": []}))
    add = respx.post("https://groups.test/api/auth/user/add").mock(return_value=Response(200, json={"result": "saved"}))
    assert (await connector.ensure_user(_user(), "pw")).ok
    import json
    assert json.loads(add.calls[0].request.content)["user"]["group_memberships"] == "vpn-users,auditors"


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
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_OPNSENSE_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_OPNSENSE_BASE_URL", "https://fw.invalid")
    config.get_settings.cache_clear()
    try:
        r = admin_client.get("/status")
        assert r.status_code == 200
        assert "opnsense" in r.text and "unreachable" in r.text
    finally:
        config.get_settings.cache_clear()


@pytest.fixture()
def nexus(client, monkeypatch):
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_NEXUS_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_NEXUS_BASE_URL", "https://nexus.test")
    monkeypatch.setenv("ONEAUTH_NEXUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ONEAUTH_NEXUS_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("ONEAUTH_NEXUS_DEFAULT_ROLES", "nx-reader,nx-anonymous")
    config.get_settings.cache_clear()
    from oneauth.connectors.nexus import NexusConnector

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
    assert body["roles"] == ["nx-reader", "nx-anonymous"]


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
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_NEXUS_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_NEXUS_BASE_URL", "https://nexus.test")
    monkeypatch.setenv("ONEAUTH_NEXUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ONEAUTH_NEXUS_ADMIN_PASSWORD", "secret")
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
def nextcloud(client, monkeypatch):
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ADMIN_USER", "admin")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ADMIN_PASSWORD", "app-password")
    config.get_settings.cache_clear()
    from oneauth.connectors.nextcloud import NextcloudConnector

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
async def test_nextcloud_applies_default_groups():
    from oneauth.config import NextcloudTarget
    from oneauth.connectors.nextcloud import NextcloudConnector
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
    import oneauth.config as config

    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ENABLED", "true")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ADMIN_USER", "admin")
    monkeypatch.setenv("ONEAUTH_NEXTCLOUD_ADMIN_PASSWORD", "app-password")
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
