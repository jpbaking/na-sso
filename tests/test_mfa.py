import re
from time import time
from types import SimpleNamespace

import pytest
from webauthn.helpers import bytes_to_base64url

from na_sso.mfa import _totp
from na_sso.models import AdminMfa, AuditEvent, ManagedUser, WebAuthnCredential, utcnow
from na_sso.security import hash_password


@pytest.fixture()
def mfa_client(tmp_path, monkeypatch):
    config_path = tmp_path / "mfa.yaml"
    config_path.write_text("""
admin_mfa_policy:
  required: true
  allowed_methods: [webauthn, totp]
  issuer: NA-SSO Test
  rp_id: testserver
  expected_origin: http://testserver
  reauthentication_minutes: 10
""")
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(tmp_path / "mfa.db"))
    monkeypatch.setenv("NA_SSO_SECRET_KEY", "mfa-test-secret")
    monkeypatch.setenv("NA_SSO_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("NA_SSO_ADMIN_BOOTSTRAP_PASSWORD", "admin-pass")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("NA_SSO_ROOT_RECOVERY_CODE", "Root-Emergency-Once-2026")

    import na_sso.config as config
    import na_sso.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    from fastapi.testclient import TestClient
    from na_sso.main import app
    with TestClient(app) as client:
        yield client
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()


def _login(client, username="admin", password="admin-pass"):
    return client.post(
        "/login", data={"username": username, "password": password},
        follow_redirects=False,
    )


def _enrol_totp(client):
    login = _login(client)
    assert login.headers["location"] == "/account/mfa"
    assert client.get("/users", follow_redirects=False).headers["location"] == "/account/mfa"
    setup = client.post("/account/mfa/totp/start")
    secret = re.search(r'id="totp-secret" value="([A-Z2-7]+)"', setup.text).group(1)
    confirmed = client.post(
        "/account/mfa/totp/confirm", data={"code": _totp(secret)}
    )
    codes = re.findall(r"[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}", confirmed.text)
    assert confirmed.status_code == 200 and len(codes) == 10
    return secret, codes


def test_required_totp_login_recovery_and_secret_storage(mfa_client):
    secret, codes = _enrol_totp(mfa_client)
    assert mfa_client.get("/users").status_code == 200

    from na_sso.db import get_session
    with get_session() as db:
        row = db.query(AdminMfa).filter_by(user_id=0).one()
        assert row.totp_secret and secret not in row.totp_secret
        assert not any(code in row.recovery_code_hashes for code in codes)
        assert db.query(AuditEvent).filter_by(action="mfa.totp.enrolled").count() == 1

    mfa_client.post("/logout")
    assert _login(mfa_client).headers["location"] == "/login/mfa"
    verified = mfa_client.post(
        "/login/mfa/code", data={"code": _totp(secret, int(time()) + 30)},
        follow_redirects=False
    )
    assert verified.headers["location"] == "/users"

    mfa_client.post("/logout")
    _login(mfa_client)
    recovered = mfa_client.post(
        "/login/mfa/code", data={"code": codes[0]}, follow_redirects=False
    )
    assert recovered.headers["location"] == "/users"
    mfa_client.post("/logout")
    _login(mfa_client)
    reused = mfa_client.post("/login/mfa/code", data={"code": codes[0]})
    assert reused.status_code == 401


def test_root_emergency_recovery_is_one_use_per_configured_value(mfa_client):
    _enrol_totp(mfa_client)
    mfa_client.post("/logout")
    _login(mfa_client)
    first = mfa_client.post(
        "/login/mfa/code", data={"code": "Root-Emergency-Once-2026"},
        follow_redirects=False,
    )
    assert first.headers["location"] == "/users"
    mfa_client.post("/logout")
    _login(mfa_client)
    second = mfa_client.post(
        "/login/mfa/code", data={"code": "Root-Emergency-Once-2026"}
    )
    assert second.status_code == 401
    from na_sso.db import get_session
    with get_session() as db:
        event = db.query(AuditEvent).filter(
            AuditEvent.detail == "method=root_emergency_code"
        ).one()
        assert event.actor == "admin"


def test_webauthn_enrol_login_and_final_factor_revocation(mfa_client, monkeypatch):
    _enrol_totp(mfa_client)
    options = mfa_client.post("/account/mfa/webauthn/options")
    assert options.status_code == 200 and options.json()["rp"]["id"] == "testserver"
    verified_registration = SimpleNamespace(
        credential_id=b"credential-id",
        credential_public_key=b"public-key-cbor",
        sign_count=2,
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        "na_sso.mfa.verify_registration_response", lambda **_: verified_registration
    )
    enrolled = mfa_client.post("/account/mfa/webauthn/verify", json={
        "name": "Office security key",
        "credential": {"id": "browser-value", "response": {"transports": ["usb"]}},
    })
    assert enrolled.status_code == 200 and enrolled.json() == {
        "ok": True, "recovery_codes": []
    }

    from na_sso.db import get_session
    with get_session() as db:
        credential = db.query(WebAuthnCredential).one()
        credential_id = credential.id
        assert credential.name == "Office security key"
        assert credential.public_key != "public-key-cbor"

    # With two methods, TOTP can be revoked; the final required passkey cannot.
    removed = mfa_client.post("/account/mfa/totp/revoke", follow_redirects=False)
    assert removed.status_code == 303
    retained = mfa_client.post(
        f"/account/mfa/webauthn/{credential_id}/revoke", follow_redirects=False
    )
    assert retained.status_code == 303
    with get_session() as db:
        assert db.query(WebAuthnCredential).count() == 1
        assert db.query(AdminMfa).one().totp_secret is None

    mfa_client.post("/logout")
    assert _login(mfa_client).headers["location"] == "/login/mfa"
    authentication_options = mfa_client.post("/login/mfa/webauthn/options")
    assert authentication_options.status_code == 200
    monkeypatch.setattr(
        "na_sso.mfa.verify_authentication_response",
        lambda **_: SimpleNamespace(new_sign_count=3),
    )
    authenticated = mfa_client.post("/login/mfa/webauthn/verify", json={
        "id": bytes_to_base64url(b"credential-id"), "response": {},
    })
    assert authenticated.status_code == 200
    assert authenticated.json()["redirect"] == "/users"
    assert mfa_client.get("/users").status_code == 200


def test_mfa_changes_require_fresh_password_and_managed_users_are_unaffected(
    mfa_client, monkeypatch
):
    _enrol_totp(mfa_client)
    from na_sso import mfa
    future = int(mfa.time()) + 11 * 60
    monkeypatch.setattr("na_sso.mfa.time", lambda: future)
    page = mfa_client.get("/account/mfa")
    assert "Confirm your identity" in page.text
    blocked = mfa_client.post("/account/mfa/totp/start", follow_redirects=False)
    assert blocked.status_code == 303
    rejected = mfa_client.post(
        "/account/mfa/reauth", data={"password": "wrong"}
    )
    assert rejected.status_code == 401
    accepted = mfa_client.post(
        "/account/mfa/reauth", data={"password": "admin-pass"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303

    from na_sso.db import get_session
    with get_session() as db:
        user = ManagedUser(
            username="ordinary", password_hash=hash_password("Ordinary!Pass-2026"),
            password_changed_at=utcnow(), role="user", status="active",
        )
        db.add(user)
        db.commit()
    mfa_client.post("/logout")
    login = _login(mfa_client, "ordinary", "Ordinary!Pass-2026")
    assert login.headers["location"] == "/account"
    assert mfa_client.get("/account").status_code == 200
