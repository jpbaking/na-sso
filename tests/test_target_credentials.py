from httpx import Response
from datetime import datetime
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
        assert row.revision == 1
        assert row.last_probe_ok is True
        assert row.last_checked_at and row.last_success_at
        assert row.probe_attempt_count == 0 and row.next_probe_at is None
    assert [item.target_id for item in get_connectors()] == ["firewall"]
    configured_page = admin_client.get("/status")
    assert "fully configured" in configured_page.text
    assert ">verified<" not in configured_page.text
    assert ">reachable<" not in configured_page.text
    assert '<details class="disclosure-row" name="target-credentials">' in configured_page.text
    assert '<summary class="disclosure-summary">' in configured_page.text
    assert '<div class="disclosure-list disclosure-cards">' in configured_page.text
    assert '<div class="data-list disclosure-list">' not in configured_page.text
    assert "Change credentials" not in configured_page.text
    assert "Credential revision" in configured_page.text
    assert "Last successful check" in configured_page.text
    assert "Test connection" in configured_page.text
    assert "Replace credentials" in configured_page.text
    respx.post("https://fw.test/api/auth/user/search").mock(return_value=Response(401))
    page = admin_client.post("/targets/firewall/credentials", data={
        "api_key": "new-key", "api_secret": "new-secret",
    })
    assert get_connectors() == []
    assert "auth failed" in page.text
    assert '<details class="disclosure-row" name="target-credentials" open>' in page.text
    assert "Connection needs attention" in page.text
    assert "Check the management username" in page.text
    assert "Configure credentials" not in page.text
    assert "Test probe" not in page.text
    assert "Replace credentials" in page.text
    assert "Test connection" in page.text
    assert "Attempt 1; retry after" in page.text
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="firewall").one()
        assert row.revision == 2
        assert row.verified_at is None and row.last_probe_ok is False
        assert row.probe_failure_kind == "authentication"
        assert row.probe_attempt_count == 1 and row.next_probe_at
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    checked = admin_client.post("/targets/firewall/probe")
    assert "Target reachable" in checked.text
    assert "The target accepted the connection." in checked.text
    assert "Reachable" in checked.text and "No retry scheduled" in checked.text
    assert [item.target_id for item in get_connectors()] == ["firewall"]


@respx.mock
def test_reachability_history_retry_and_sanitised_detail(
    admin_client, tmp_path, monkeypatch
):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw.test}
""")
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    admin_client.post("/targets/firewall/credentials", data={
        "api_key": "operator-key", "api_secret": "operator-secret",
    })
    from na_sso.db import get_session
    from na_sso.models import TargetCredential
    from na_sso.target_credentials import (
        readiness_map,
        record_probe,
        retry_due_target_probes,
    )
    from na_sso.connectors import get_connectors

    record_probe(
        "firewall",
        False,
        "connection timed out at https://admin:secret@fw.test password=hunter2",
    )
    readiness = readiness_map()["firewall"]
    assert readiness.verified is True and readiness.reachable is False
    assert readiness.failure_kind == "unreachable"
    assert "secret" not in readiness.detail and "hunter2" not in readiness.detail
    assert [item.target_id for item in get_connectors()] == ["firewall"]
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="firewall").one()
        previous_success = row.last_success_at
        row.next_probe_at = row.next_probe_at.replace(year=2000)
        db.commit()

    import asyncio
    assert asyncio.run(retry_due_target_probes()) == 1
    readiness = readiness_map()["firewall"]
    assert readiness.reachable is True and readiness.failure_kind == ""
    assert readiness.probe_attempt_count == 0 and readiness.next_probe_at is None
    assert readiness.last_success_at >= previous_success


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


@respx.mock
def test_credential_rotation_leaves_openvpn_configuration_untouched(
    admin_client, tmp_path, monkeypatch
):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw.test}
""")
    respx.post("https://fw.test/api/auth/user/search").mock(
        return_value=Response(200, json={"rows": []})
    )
    admin_client.post(
        "/targets/firewall/credentials",
        data={"api_key": "first-key", "api_secret": "first-secret"},
    )

    from na_sso.db import get_session
    from na_sso.models import TargetCredential, TargetOpenvpnConfig

    verified_at = datetime(2026, 7, 22, 8, 30)
    updated_at = datetime(2026, 7, 22, 8, 31)
    expected = {
        "enabled": True,
        "vpnid": "vpn-server-1",
        "template": "PlainOpenVPN",
        "hostname": "vpn.example.test",
        "cert_lifetime_days": 397,
        "auth_posture": "cert_and_password",
        "verified_at": verified_at,
        "verify_detail": "OpenVPN export settings verified.",
        "updated_at": updated_at,
    }
    with get_session() as db:
        db.add(TargetOpenvpnConfig(target_id="firewall", **expected))
        db.commit()

    admin_client.post(
        "/targets/firewall/credentials",
        data={"api_key": "rotated-key", "api_secret": "rotated-secret"},
    )

    with get_session() as db:
        credentials = db.query(TargetCredential).filter_by(
            target_id="firewall"
        ).one()
        openvpn = db.query(TargetOpenvpnConfig).filter_by(
            target_id="firewall"
        ).one()
        assert credentials.revision == 2
        assert all(
            value not in credentials.encrypted_payload
            for value in (
                "vpn-server-1",
                "PlainOpenVPN",
                "vpn.example.test",
                "cert_and_password",
            )
        )
        assert {
            key: getattr(openvpn, key) for key in expected
        } == expected


@respx.mock
def test_token_target_credentials_are_encrypted_verified_and_write_only(
    admin_client, tmp_path, monkeypatch
):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: gitlab, type: gitlab, display_name: GitLab, base_url: https://gitlab.test}
""")
    respx.get("https://gitlab.test/api/v4/user").mock(
        return_value=Response(200, json={"id": 1, "username": "admin", "is_admin": True})
    )

    response = admin_client.post("/targets/gitlab/credentials", data={
        "auth_mode": "token", "api_token": "administrator-token",
    }, follow_redirects=False)

    assert response.status_code == 303
    from na_sso.connectors import get_connectors
    from na_sso.db import get_session
    from na_sso.models import TargetCredential
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="gitlab").one()
        assert row.auth_mode == "token" and row.verified_at is not None
        assert "administrator-token" not in row.encrypted_payload
    assert [connector.target_type for connector in get_connectors()] == ["gitlab"]
    page = admin_client.get("/status")
    assert "Administrator API token" in page.text
    assert "administrator-token" not in page.text


@respx.mock
def test_npm_password_credentials_are_encrypted_verified_and_write_only(
    admin_client, tmp_path, monkeypatch
):
    _registry(tmp_path, monkeypatch, """targets:
  - {id: npm, type: npm, display_name: Nginx Proxy Manager, base_url: https://npm.test}
""")
    token = respx.post("https://npm.test/api/tokens").mock(
        return_value=Response(200, json={"token": "short-lived-jwt"})
    )
    users = respx.get("https://npm.test/api/users").mock(
        return_value=Response(200, json=[])
    )

    response = admin_client.post("/targets/npm/credentials", data={
        "auth_mode": "password",
        "admin_user": "admin@example.test",
        "password": "administrator-password",
    }, follow_redirects=False)

    assert response.status_code == 303 and token.called and users.called
    from na_sso.connectors import get_connectors
    from na_sso.connectors.npm import NpmConnector
    from na_sso.db import get_session
    from na_sso.models import TargetCredential
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id="npm").one()
        assert row.auth_mode == "password" and row.verified_at is not None
        assert "admin@example.test" not in row.encrypted_payload
        assert "administrator-password" not in row.encrypted_payload
    connectors = get_connectors()
    assert len(connectors) == 1 and isinstance(connectors[0], NpmConnector)
    page = admin_client.get("/status")
    assert "Admin user" in page.text and "Admin password" in page.text
    assert "admin@example.test" not in page.text
    assert "administrator-password" not in page.text
