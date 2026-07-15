from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from na_sso.security import generate_password, public_key_from_private, validate_password


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
    assert admin_client.get("/users", follow_redirects=False).status_code == 303
    decision_page = admin_client.get("/account/password-decision")
    assert '<body class="site-page">' in decision_page.text
    assert '<a href="/account" class="brand"' in decision_page.text
    assert '<form method="post" class="stack-3">' in decision_page.text
    assert 'class="input" id="current-password"' in decision_page.text
    assert 'class="btn btn-primary" name="choice" value="change"' in decision_page.text
    assert "Change your temporary password" in decision_page.text
    assert 'name="choice" value="keep"' not in decision_page.text
    assert 'id="password-checks" aria-live="polite"' in decision_page.text
    assert 'id="confirm-password-field" hidden' in decision_page.text
    assert 'name="confirm_password"' in decision_page.text
    assert "confirmPasswordField.hidden=password.value.length===0" in decision_page.text
    assert "passwordGenerated.value='true'" in decision_page.text
    assert '<dialog id="generated-password-modal" class="modal"' in decision_page.text
    assert 'id="copy-generated-password"' in decision_page.text
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
    assert '<div class="data-value">user</div>' in account_page.text
    assert '<div class="data-value">not enrolled</div>' in account_page.text
    assert '<div class="data-key">Password expires</div>' in account_page.text
    assert 'class="btn btn-primary" id="generate-key"' in account_page.text
    assert 'id="key-form" method="post" action="/account/ssh-key"' in account_page.text
    assert "crypto.subtle.generateKey({name:'Ed25519'}" in account_page.text
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
