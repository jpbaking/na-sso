import pytest
import respx
from httpx import Response

from oneauth.connectors.base import Connector, SyncResult


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
    from oneauth.models import ManagedUser

    return ManagedUser(username=username, display_name="J", email="j@x", status=status)


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
