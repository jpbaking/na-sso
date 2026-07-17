from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from na_sso.security import generate_password, public_key_from_private, validate_password


def test_browser_request_boundary_and_hardened_session_cookie(client):
    hostile = client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass"},
        headers={"Origin": "https://hostile.example"},
    )
    assert hostile.status_code == 403
    assert "Cross-site state-changing requests" in hostile.text
    assert hostile.headers["x-frame-options"] == "DENY"
    assert hostile.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert hostile.headers["x-content-type-options"] == "nosniff"
    assert hostile.headers["referrer-policy"] == "same-origin"

    fetch_metadata = client.post(
        "/api/v1/reconciliation/preview",
        json={},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert fetch_metadata.status_code == 403
    assert fetch_metadata.json()["error"]["code"] == "cross_site_request"

    accepted = client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    cookie = accepted.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_secure_cookie_attribute_is_deployment_configurable(client, monkeypatch):
    from na_sso.config import get_settings

    monkeypatch.setenv("NA_SSO_SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin-pass"},
            follow_redirects=False,
        )
        assert "Secure" in response.headers["set-cookie"]
    finally:
        get_settings.cache_clear()


def _expiry_config(tmp_path, monkeypatch, *, mode="grace", limit=1):
    path = tmp_path / "expiry-policy.yaml"
    limit_value = "null" if limit is None else str(limit)
    path.write_text(
        "password_policy:\n"
        "  expires_after_days: 90\n"
        f"  expiry_acknowledgement_mode: {mode}\n"
        "  expiry_acknowledgement_grace_days: 14\n"
        f"  expiry_acknowledgement_limit: {limit_value}\n"
    )
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(path))
    from na_sso.config import get_settings
    get_settings.cache_clear()
    return path


def test_generated_password_satisfies_central_policy(client):
    password = generate_password()
    assert validate_password(password).valid


def test_private_key_is_reduced_to_public_material():
    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = public_key_from_private(private)
    assert public.startswith("ssh-ed25519 ")
    assert "PRIVATE" not in public


def test_local_user_login_is_restricted_until_password_decision(admin_client):
    password = "V4lid!Orbit-Cloud-2026"
    response = admin_client.post("/users/new", data={
        "username": "localuser", "display_name": "Local Person", "email": "local@example.test",
        "password": password, "role": "user",
    }, follow_redirects=False)
    assert response.status_code == 303
    admin_client.post("/logout")
    response = admin_client.post("/login", data={"username": "localuser", "password": password}, follow_redirects=False)
    assert response.headers["location"] == "/account/password-decision"
    assert admin_client.get("/users", follow_redirects=False).status_code == 403
    decision_page = admin_client.get("/account/password-decision")
    assert '<body class="site-page">' in decision_page.text
    assert '<a href="/account" class="brand"' in decision_page.text
    assert '<form method="post" class="stack-3">' in decision_page.text
    assert 'class="input" id="current-password"' in decision_page.text
    assert 'id="password-submit" name="choice" value="change"' in decision_page.text
    assert "Change your temporary password" in decision_page.text
    assert 'name="choice" value="keep"' not in decision_page.text
    assert 'id="password-checks" aria-live="polite"' in decision_page.text
    assert 'id="confirm-password-field" hidden' in decision_page.text
    assert 'name="confirm_password"' in decision_page.text
    assert "confirmPasswordField.hidden=password.value.length===0" in decision_page.text
    assert "passwordGenerated.value='true'" in decision_page.text
    assert '<dialog id="generated-password-modal" class="modal"' in decision_page.text
    assert 'id="copy-generated-password"' in decision_page.text
    assert 'id="generated-password-value" type="password" readonly' in decision_page.text
    assert 'id="reveal-generated-password"' in decision_page.text
    assert 'name="credential_handoff_confirmed"' in decision_page.text
    assert "passwordSubmit.disabled=blocked" in decision_page.text
    assert "navigator.clipboard.writeText(generatedPassword)" in decision_page.text
    assert "generatedPassword=''" in decision_page.text
    replacement = "V4lid!Replacement-Star-2026"
    response = admin_client.post("/account/password-decision", data={
        "choice": "change", "current_password": password, "new_password": replacement,
        "confirm_password": replacement,
    }, follow_redirects=False)
    assert response.headers["location"] == "/login"
    response = admin_client.post("/login", data={"username": "localuser", "password": replacement}, follow_redirects=False)
    assert response.headers["location"] == "/account"


async def test_expired_password_grace_is_dated_audited_and_limited(
    admin_client, tmp_path, monkeypatch
):
    fixed_now = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("na_sso.models.utcnow", lambda: fixed_now)
    _expiry_config(tmp_path, monkeypatch)
    password = "V4lid!Orbit-Cloud-2026"
    admin_client.post("/users/new", data={
        "username": "expiryuser", "display_name": "Expiry User",
        "email": "expiry@example.test", "password": password,
    })

    from na_sso.db import get_session
    from na_sso.models import AuditEvent, ManagedUser, as_utc
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="expiryuser").one()
        user.password_changed_at = fixed_now - timedelta(days=120)
        user.password_decision_required = True
        user.password_decision_kind = "expired"
        db.commit()
        original_changed_at = as_utc(user.password_changed_at)

    admin_client.post("/logout")
    login = admin_client.post(
        "/login", data={"username": "expiryuser", "password": password},
        follow_redirects=False,
    )
    assert login.headers["location"] == "/account/password-decision"
    page = admin_client.get("/account/password-decision")
    assert "14-day grace acknowledgement" in page.text
    assert "expire again on 2026-07-29" in page.text
    assert "acknowledgement 1 of 1" in page.text
    assert ">Keep until 2026-07-29</button>" in page.text

    kept = admin_client.post("/account/password-decision", data={
        "choice": "keep", "current_password": password,
    }, follow_redirects=False)
    assert kept.status_code == 303 and kept.headers["location"] == "/account"
    notice = admin_client.get("/account")
    assert "current password remains active until 2026-07-29" in notice.text
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="expiryuser").one()
        assert as_utc(user.password_changed_at) == original_changed_at
        assert as_utc(user.password_keep_until) == fixed_now + timedelta(days=14)
        assert user.password_keep_count == 1
        event = db.query(AuditEvent).filter_by(
            action="password.keep_acknowledged", subject="expiryuser"
        ).one()
        assert "mode=grace" in event.detail
        assert "next_expiry=2026-07-29T09:00:00+00:00" in event.detail
        assert "acknowledgement=1/1" in event.detail
        user.password_keep_until = fixed_now - timedelta(seconds=1)
        db.commit()

    from na_sso.sync import expire_due
    assert await expire_due() == 1
    limited = admin_client.get("/account/password-decision")
    assert "used all 1 allowed acknowledgement" in limited.text
    assert 'name="choice" value="keep"' not in limited.text
    rejected = admin_client.post("/account/password-decision", data={
        "choice": "keep", "current_password": password,
    })
    assert rejected.status_code == 200
    assert "reached its acknowledgement limit" in rejected.text


def test_expired_password_acknowledgement_can_be_disabled(
    admin_client, tmp_path, monkeypatch
):
    _expiry_config(tmp_path, monkeypatch, mode="disabled")
    password = "V4lid!Copper-Zebra-2026"
    admin_client.post("/users/new", data={
        "username": "noackuser", "display_name": "No Ack",
        "email": "noack@example.test", "password": password,
    })
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="noackuser").one()
        user.password_decision_required = True
        user.password_decision_kind = "expired"
        db.commit()

    admin_client.post("/logout")
    admin_client.post("/login", data={"username": "noackuser", "password": password})
    page = admin_client.get("/account/password-decision")
    assert "Policy does not allow expired passwords to be kept" in page.text
    assert 'name="choice" value="keep"' not in page.text
    rejected = admin_client.post("/account/password-decision", data={
        "choice": "keep", "current_password": password,
    })
    assert rejected.status_code == 200
    assert "disabled by policy" in rejected.text


def test_root_cannot_be_mutated_through_user_routes(admin_client):
    admin_client.post("/users/0/delete", follow_redirects=False)
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        root = db.get(ManagedUser, 0)
        assert root.role == "root"
        assert root.display_name == "SUPERADMIN"
        assert root.status == "active"
        assert root.desired_action == "local_only"


def test_superadmin_target_cells_are_na(admin_client, monkeypatch):
    from types import SimpleNamespace
    target = SimpleNamespace(target_id="verified_target", target_type="ssh",
                             display_name="Verified target")
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [target])
    response = admin_client.get("/users")
    assert response.status_code == 200
    assert "SUPERADMIN" in response.text
    assert "N/A" in response.text
    assert 'data-user-id="0"' not in response.text
    assert 'href="/users/0"' not in response.text
    assert 'action="/users/0/delete"' not in response.text
    assert "Protected system account" in response.text


def test_admin_navigation_exposes_account_security_and_root_change_reauthenticates(
    admin_client,
):
    for path in ("/users", "/users/new", "/status", "/audit"):
        page = admin_client.get(path)
        assert '<a href="/account" class="nav-link">My account</a>' in page.text
        assert '<a href="/account/password" class="nav-link">Change password</a>' not in page.text
        assert '<a href="/account/mfa" class="nav-link">MFA</a>' not in page.text

    account = admin_client.get("/account")
    assert '<a href="/users" class="brand sidebar-brand"' in account.text
    assert '<a href="/account" class="nav-link active" aria-current="page">My account</a>' in account.text
    assert '<a href="/account/password" class="btn btn-primary">Change password</a>' in account.text
    assert '<a href="/account/mfa" class="btn btn-secondary">Manage MFA</a>' in account.text

    rejected = admin_client.post("/account/password", data={
        "current_password": "wrong-current-password",
        "new_password": "N3w!Marble-Quartz-2027",
        "confirm_password": "N3w!Marble-Quartz-2027",
        "choice": "change",
    })
    assert rejected.status_code == 200
    assert "Invalid current password." in rejected.text
    assert 'id="error-summary" role="alert" tabindex="-1"' in rejected.text

    changed = admin_client.post("/account/password", data={
        "current_password": "admin-pass",
        "new_password": "N3w!Marble-Quartz-2027",
        "confirm_password": "N3w!Marble-Quartz-2027",
        "choice": "change",
    }, follow_redirects=False)
    assert changed.status_code == 303 and changed.headers["location"] == "/login"
    notice = admin_client.get("/login")
    assert "Password changed" in notice.text
    assert "Sign in again with the new password." in notice.text
    assert admin_client.post("/login", data={
        "username": "admin", "password": "admin-pass",
    }).status_code == 401
    assert admin_client.post("/login", data={
        "username": "admin", "password": "N3w!Marble-Quartz-2027",
    }, follow_redirects=False).status_code == 303


def test_private_key_enrollment_persists_only_public_key(admin_client):
    password = "V4lid!Comet-Bridge-2026"
    admin_client.post("/users/new", data={"username": "keyuser", "display_name": "Key Person",
        "email": "key@example.test", "password": password})
    admin_client.post("/logout")
    admin_client.post("/login", data={"username": "keyuser", "password": password})
    replacement = "V4lid!Meteor-Orbit-2026"
    admin_client.post("/account/password-decision", data={"choice": "change", "current_password": password,
        "new_password": replacement, "confirm_password": replacement})
    admin_client.post("/login", data={"username": "keyuser", "password": replacement})
    account_page = admin_client.get("/account")
    assert '<body class="site-page">' in account_page.text
    assert '<a href="/account" class="brand"' in account_page.text
    assert '<div class="data-value">keyuser</div>' in account_page.text
    assert '<div class="data-value">Key Person</div>' in account_page.text
    assert '<div class="data-value">key@example.test</div>' in account_page.text
    assert '<div class="data-value">active</div>' in account_page.text
    assert "Managed user" in account_page.text
    assert "Can manage only their own password and SSH key." in account_page.text
    assert '<div class="data-value">not enrolled</div>' in account_page.text
    assert '<div class="data-key">Password expires</div>' in account_page.text
    assert 'class="btn btn-primary" id="generate-key"' in account_page.text
    assert 'id="key-form" method="post" action="/account/ssh-key"' in account_page.text
    assert "crypto.subtle.generateKey({name:'Ed25519'}" in account_page.text
    assert 'id="private-key-output" rows="8" readonly' in account_page.text
    assert 'id="new-key-fingerprint"' in account_page.text
    assert 'id="enrol-key" type="submit" disabled' in account_page.text
    assert "crypto.subtle.digest('SHA-256',blob)" in account_page.text
    assert "document.getElementById('key-form').submit()" not in account_page.text
    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    response = admin_client.post("/account/ssh-key", data={"private_key": private}, follow_redirects=False)
    assert response.status_code == 303
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="keyuser").one()
        assert user.ssh_public_key.startswith("ssh-ed25519 ")
        assert "PRIVATE" not in user.ssh_public_key
        enrolled = user.ssh_public_key
    invalid = admin_client.post("/account/ssh-key", data={"public_key": "invalid"})
    assert invalid.status_code == 422
    with get_session() as db:
        assert db.query(ManagedUser).filter_by(username="keyuser").one().ssh_public_key == enrolled


def test_managed_user_my_access_is_plain_actionable_and_support_configured(
    admin_client, tmp_path, monkeypatch
):
    config_path = tmp_path / "access.yaml"
    config_path.write_text("""
support_policy:
  label: Contact identity operations
  url: mailto:identity@example.test
  guidance: Share your username and target name; never send passwords or private keys.
targets:
  - {id: cloud, type: nextcloud, display_name: Company Cloud, base_url: https://cloud.example.test}
""")
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    from na_sso.config import get_settings
    get_settings.cache_clear()
    from na_sso.db import get_session
    from na_sso.models import ManagedUser, SyncState
    from na_sso.security import generate_ssh_keypair, hash_password
    _, public = generate_ssh_keypair()
    with get_session() as db:
        user = ManagedUser(
            username="accessuser", display_name="Access User",
            email="access@example.test", password_hash=hash_password("V4lid!Access-Cloud-2026"),
            ssh_public_key=public,
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud", assigned=True,
            state="failed", detail="https://admin:secret@cloud.example.test refused",
            next_retry_at=datetime(2026, 7, 16, 10, 30, tzinfo=timezone.utc),
        ))
        db.commit()

    admin_client.post("/logout")
    login = admin_client.post("/login", data={
        "username": "accessuser", "password": "V4lid!Access-Cloud-2026",
    }, follow_redirects=False)
    assert login.headers["location"] == "/account"
    page = admin_client.get("/account")
    assert "My access" in page.text and "Company Cloud" in page.text
    assert "The latest target operation failed" in page.text
    assert "Password" in page.text
    assert "Automatic retry after 2026-07-16 10:30:00 UTC" in page.text
    assert 'href="mailto:identity@example.test"' in page.text
    assert "Contact identity operations" in page.text
    assert "never send passwords or private keys" in page.text
    assert "SHA256:" in page.text
    assert "admin:secret" not in page.text


def test_generated_password_change_requires_confirmed_handoff(admin_client):
    replacement = "N3w!Marble-Quartz-2027"
    rejected = admin_client.post("/account/password", data={
        "current_password": "admin-pass",
        "new_password": replacement,
        "password_generated": "true",
        "choice": "change",
    })
    assert rejected.status_code == 200
    assert "confirm the handoff before changing it" in rejected.text

    assert admin_client.post("/login", data={
        "username": "admin", "password": "admin-pass",
    }, follow_redirects=False).status_code == 303
    accepted = admin_client.post("/account/password", data={
        "current_password": "admin-pass",
        "new_password": replacement,
        "password_generated": "true",
        "credential_handoff_confirmed": "true",
        "choice": "change",
    }, follow_redirects=False)
    assert accepted.status_code == 303


def test_server_generated_private_key_uses_one_time_styled_result(admin_client, monkeypatch):
    from unittest.mock import AsyncMock

    password = "V4lid!Meteor-Key-2026"
    admin_client.post("/users/new", data={
        "username": "fallbackuser", "display_name": "Fallback User",
        "email": "fallback@example.test", "password": password,
    })
    admin_client.post("/logout")
    admin_client.post("/login", data={"username": "fallbackuser", "password": password})
    replacement = "V4lid!Comet-Orbit-2026"
    admin_client.post("/account/password-decision", data={"choice": "change", "current_password": password,
        "new_password": replacement, "confirm_password": replacement})
    admin_client.post("/login", data={"username": "fallbackuser", "password": replacement})
    from na_sso.config import get_settings
    settings = get_settings()
    file_config = settings.file
    file_config.ssh_key_policy.allow_server_fallback = True
    monkeypatch.setattr("na_sso.config.get_settings", lambda: type("Settings", (), {"file": file_config})())
    monkeypatch.setattr("na_sso.sync.sync_user", AsyncMock())

    response = admin_client.post("/account/ssh-key/generate")

    assert response.status_code == 200, response.text
    assert '<body class="site-page">' in response.text
    assert '<a href="/account" class="brand"' in response.text
    assert 'id="private-key"' in response.text
    assert 'id="copy-private-key"' in response.text
    assert 'id="save-private-key"' in response.text
    assert "-----BEGIN PRIVATE KEY-----" in response.text
