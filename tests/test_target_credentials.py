from httpx import Response
import respx


def test_target_status_distinguishes_unreachable_from_auth_failure():
    from oneauth.status import _configuration_status

    assert _configuration_status(
        configured=True, verified=False, detail="connection timed out"
    ) == "Unreachable"
    assert _configuration_status(
        configured=True, verified=False, detail="HTTP 401 Unauthorized"
    ) == "auth failed"


def _registry(tmp_path, monkeypatch, text):
    path = tmp_path / "targets.yaml"
    path.write_text(text)
    monkeypatch.setenv("ONEAUTH_CONFIG_FILE", str(path))
    import oneauth.config as config
    config.get_settings.cache_clear()


@respx.mock
def test_api_credentials_are_encrypted_and_probe_gated(admin_client, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw.test}
""")
    from oneauth.connectors import get_connectors
    assert get_connectors() == []
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    assert admin_client.post("/targets/firewall/credentials", data={
        "api_key": "operator-key", "api_secret": "operator-secret"
    }, follow_redirects=False).status_code == 303
    from oneauth.db import get_session
    from oneauth.models import TargetCredential
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="firewall").one()
        assert "operator-key" not in row.encrypted_payload
        assert "operator-secret" not in row.encrypted_payload
        assert row.verified_at is not None
    assert [item.target_id for item in get_connectors()] == ["firewall"]
    configured_page = admin_client.get("/status")
    assert "fully configured" in configured_page.text
    assert ">verified<" not in configured_page.text
    assert ">reachable<" not in configured_page.text
    respx.post("https://fw.test/api/auth/user/search").mock(return_value=Response(401))
    admin_client.post("/targets/firewall/credentials", data={"api_key": "new-key", "api_secret": "new-secret"})
    assert get_connectors() == []
    page = admin_client.get("/status")
    assert "auth failed" in page.text
    assert "Test probe" not in page.text
    assert ">SAVE<" in page.text


def test_ssh_password_or_uploaded_key_is_encrypted(admin_client, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: shell, type: ssh, display_name: Shell, host: shell.test, host_key_sha256: 'SHA256:AAAAAAAAAAAAAAAAAAAA', platform: debian}
""")
    from oneauth.connectors.base import SyncResult
    from oneauth.connectors.ssh import SSHConnector

    async def reachable(_connector):
        return SyncResult(True, "reachable")

    monkeypatch.setattr(SSHConnector, "probe", reachable)
    assert admin_client.post("/targets/shell/credentials", data={
        "auth_mode": "password", "admin_user": "provisioner", "password": "admin-secret"
    }, follow_redirects=False).status_code == 303
    from oneauth.connectors.base import build_unverified_connector
    assert build_unverified_connector("shell")._target.management_password.get_secret_value() == "admin-secret"
    private = "-----BEGIN OPENSSH PRIVATE KEY-----\nredacted-test-material\n-----END OPENSSH PRIVATE KEY-----\n"
    assert admin_client.post("/targets/shell/credentials", data={
        "auth_mode": "private_key", "admin_user": "key-admin"
    }, files={"private_key": ("admin.key", private, "text/plain")}, follow_redirects=False).status_code == 303
    assert build_unverified_connector("shell")._target.management_private_key.get_secret_value() == private
    from oneauth.db import get_session
    from oneauth.models import TargetCredential
    with get_session() as db:
        assert "redacted-test-material" not in db.query(TargetCredential).filter_by(target_id="shell").one().encrypted_payload


def test_target_credential_routes_require_auth(client):
    assert client.post("/targets/forged/credentials", data={}, follow_redirects=False).status_code == 303
    assert client.post("/targets/forged/probe", follow_redirects=False).status_code == 303
