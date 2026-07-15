from httpx import Response
import pytest
import respx


def test_target_status_distinguishes_unreachable_from_auth_failure():
    from na_sso.status import _configuration_status

    assert _configuration_status(
        configured=True, verified=False, detail="connection timed out"
    ) == "Unreachable"
    assert _configuration_status(
        configured=True, verified=False, detail="HTTP 401 Unauthorized"
    ) == "auth failed"


def _registry(tmp_path, monkeypatch, text):
    path = tmp_path / "targets.yaml"
    path.write_text(text)
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(path))
    import na_sso.config as config
    config.get_settings.cache_clear()


@respx.mock
def test_api_credentials_are_encrypted_and_probe_gated(admin_client, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw.test}
""")
    from na_sso.connectors import get_connectors
    assert get_connectors() == []
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    assert admin_client.post("/targets/firewall/credentials", data={
        "api_key": "operator-key", "api_secret": "operator-secret"
    }, follow_redirects=False).status_code == 303
    from na_sso.db import get_session
    from na_sso.models import TargetCredential
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
    assert '<details class="disclosure-row" name="target-credentials">' in configured_page.text
    assert '<summary class="disclosure-summary">' in configured_page.text
    assert "Change credentials" not in configured_page.text
    respx.post("https://fw.test/api/auth/user/search").mock(return_value=Response(401))
    admin_client.post("/targets/firewall/credentials", data={"api_key": "new-key", "api_secret": "new-secret"})
    assert get_connectors() == []
    page = admin_client.get("/status")
    assert "auth failed" in page.text
    assert '<details class="disclosure-row" name="target-credentials">' in page.text
    assert '<details class="disclosure-row" name="target-credentials" open>' not in page.text
    assert "Configure credentials" not in page.text
    assert "Test probe" not in page.text
    assert ">SAVE<" in page.text


def test_ssh_password_key_or_combined_credentials_are_encrypted(admin_client, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: shell, type: ssh, display_name: Shell, host: shell.test, host_key_sha256: 'SHA256:AAAAAAAAAAAAAAAAAAAA', platform: debian}
""")
    from na_sso.connectors.base import SyncResult
    from na_sso.connectors.ssh import SSHConnector

    async def reachable(_connector):
        return SyncResult(True, "reachable")

    monkeypatch.setattr(SSHConnector, "probe", reachable)
    from na_sso.target_credentials import save_credentials
    with pytest.raises(ValueError, match="management credentials"):
        save_credentials("shell", "password_and_private_key", {
            "management_user": "incomplete", "management_password": "password-only"
        })
    assert admin_client.post("/targets/shell/credentials", data={
        "auth_mode": "password", "admin_user": "provisioner", "password": "admin-secret"
    }, follow_redirects=False).status_code == 303
    from na_sso.connectors.base import build_unverified_connector
    assert build_unverified_connector("shell")._target.management_password.get_secret_value() == "admin-secret"
    private = "-----BEGIN OPENSSH PRIVATE KEY-----\nredacted-test-material\n-----END OPENSSH PRIVATE KEY-----\n"
    assert admin_client.post("/targets/shell/credentials", data={
        "auth_mode": "private_key", "admin_user": "key-admin"
    }, files={"private_key": ("admin.key", private, "text/plain")}, follow_redirects=False).status_code == 303
    assert build_unverified_connector("shell")._target.management_private_key.get_secret_value() == private
    assert admin_client.post("/targets/shell/credentials", data={
        "auth_mode": "password_and_private_key", "admin_user": "two-factor-admin",
        "password": "second-factor"
    }, files={"private_key": ("admin.key", private, "text/plain")}, follow_redirects=False).status_code == 303
    combined = build_unverified_connector("shell")._target
    assert combined.management_password.get_secret_value() == "second-factor"
    assert combined.management_private_key.get_secret_value() == private
    from na_sso.db import get_session
    from na_sso.models import TargetCredential
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="shell").one()
        assert row.auth_mode == "password_and_private_key"
        assert "redacted-test-material" not in row.encrypted_payload
        assert "second-factor" not in row.encrypted_payload
    page = admin_client.get("/status")
    assert 'value="password_and_private_key" selected' in page.text
    assert "data-ssh-credentials" in page.text
    assert "data-auth-password" in page.text
    assert "data-auth-private-key" in page.text


def test_target_credential_routes_require_auth(client):
    assert client.post("/targets/forged/credentials", data={}, follow_redirects=False).status_code == 303
    assert client.post("/targets/forged/probe", follow_redirects=False).status_code == 303
