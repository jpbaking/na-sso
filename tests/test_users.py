import pytest


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_bad_login_rejected(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_shared_page_shell_pins_footer_to_viewport(client, admin_client):
    login = client.get("/login")
    users = admin_client.get("/users")

    for response in (login, users):
        assert '<body class="site-page">' in response.text
        assert '<link rel="stylesheet" href="/app.css">' in response.text
        assert '<img src="/na-sso-logo.png" alt="">' in response.text
        assert response.text.index("<main>") < response.text.index('<footer class="footer">')

    for path in (
        "/na-sso-logo.png", "/favicon.svg", "/favicon.ico", "/apple-touch-icon.png",
        "/icon-192.png", "/icon-512.png",
    ):
        asset = client.get(path)
        assert asset.status_code == 200
        assert asset.content

    adaptation = client.get("/app.css")
    assert adaptation.status_code == 200
    assert ".app-sidebar" in adaptation.text and "flex-wrap: nowrap" in adaptation.text
    assert 'aria-label="Primary navigation"' in users.text
    assert 'data-mobile-sidebar-toggle aria-expanded="false"' in users.text
    assert 'aria-label="My account options"' in users.text
    header = users.text.split('<header class="nav-wrap">', 1)[1].split("</header>", 1)[0]
    assert 'href="/users" class="nav-link' not in header
    assert 'href="/account" class="nav-link' in header
    assert 'href="/account/password"' not in header
    assert 'href="/account/mfa"' not in header
    assert 'class="brand sidebar-brand"' in users.text
    assert 'data-sidebar-expand' in users.text
    assert 'class="sidebar-control-icon sidebar-collapse-idle"' in users.text
    assert 'class="sidebar-control-icon sidebar-collapse-hover"' in users.text
    assert 'class="sidebar-control-icon sidebar-expand-hover"' in users.text
    assert 'class="sidebar-expand-logo"' in users.text
    assert 'class="sidebar-expand-logo" src="/na-sso-logo.png"' in users.text
    assert 'm16 15-3-3 3-3' in users.text
    assert 'm14 9 3 3-3 3' in users.text
    assert 'href="https://github.com/jpbaking" rel="me"' in users.text

    navigation = users.text.split('<ul class="nav-links sidebar-links"', 1)[1].split("</ul>", 1)[0]
    expected_order = (
        "Users", "Assignment profiles", "Access reviews", "Unmanaged accounts",
        "Targets", "Reconciliation", "Service accounts", "Notifications", "Audit",
    )
    assert [navigation.index(f'class="sidebar-link-label">{label}</span>') for label in expected_order] == sorted(
        navigation.index(f'class="sidebar-link-label">{label}</span>') for label in expected_order
    )
    assert navigation.count('class="sidebar-link-icon"') == len(expected_order)
    for label in expected_order:
        assert f'aria-label="{label}" title="{label}"' in navigation

    assert ".sidebar-link-icon" in adaptation.text
    assert ".sidebar-collapsed .sidebar-link-label" in adaptation.text
    collapsed_rules = adaptation.text.split(".sidebar-collapsed .sidebar-brand", 1)[1].split(
        ".mobile-sidebar-toggle", 1
    )[0]
    assert ".sidebar-collapsed .sidebar-links .nav-link" in collapsed_rules
    assert ".sidebar-collapsed .sidebar-links {\n  display: none;" not in adaptation.text


def test_shared_page_container_can_expand_to_viewport(client):
    stylesheet = client.get("/design/components.css")
    assert stylesheet.status_code == 200
    assert ".container {\n  width: 100%;\n  max-width: none;" in stylesheet.text


def test_shared_modal_behavior_traps_keyboard_focus(client):
    script = client.get("/design/components.js")

    assert script.status_code == 200
    assert 'dlg.addEventListener("keydown"' in script.text
    assert 'e.key !== "Tab"' in script.text
    assert "document.activeElement === last" in script.text
    assert "document.activeElement === first" in script.text
    assert "first.focus()" in script.text and "last.focus()" in script.text


def test_new_user_form_advertises_username_contract(admin_client):
    page = admin_client.get("/users/new")
    assert "Lowercase letters, digits, underscores, dots and hyphens." in page.text
    assert 'pattern="[a-z0-9](?:[a-z0-9_.\\-]*[a-z0-9])?"' in page.text
    assert 'maxlength="64"' in page.text
    assert '<dialog id="generated-password-modal" class="modal"' in page.text
    assert 'id="copy-generated-password"' in page.text
    assert 'id="generated-password-value" type="password" readonly' in page.text
    assert 'id="reveal-generated-password"' in page.text
    assert 'id="confirm-password-handoff"' in page.text
    assert 'name="credential_handoff_confirmed"' in page.text
    assert 'id="confirm-password-field" hidden' in page.text
    assert 'name="confirm_password"' in page.text
    assert "confirmPasswordField.hidden=password.value.length===0" in page.text
    assert "passwordGenerated.value='true'" in page.text
    assert '<div class="result-row">' in page.text
    assert '<div class="result-block">' not in page.text
    assert '<div class="alert alert-info" id="generated-password-guidance">' in page.text
    assert "The full password will not be shown again after this window is closed." in page.text
    assert "navigator.clipboard.writeText(generatedPassword)" in page.text
    assert "generatedPasswordValue.select()" in page.text
    assert "userSubmit.disabled=blocked" in page.text
    assert "generatedPassword=''" in page.text


def test_generated_user_password_requires_confirmed_handoff(admin_client):
    data = {
        "username": "generated-gate",
        "display_name": "Handoff Gate",
        "email": "",
        "password": "V4lid!Copper-Zebra-2026",
        "password_generated": "true",
    }
    rejected = admin_client.post("/users/new", data=data)
    assert rejected.status_code == 422
    assert "confirm the handoff before creating the user" in rejected.text
    assert 'value="V4lid!Copper-Zebra-2026"' not in rejected.text

    accepted = admin_client.post("/users/new", data={
        **data, "credential_handoff_confirmed": "true",
    }, follow_redirects=False)
    assert accepted.status_code == 303


def test_admin_manual_password_requires_matching_confirmation(admin_client):
    response = admin_client.post("/users/new", data={
        "username": "mismatch", "display_name": "Mismatch User",
        "email": "mismatch@example.test", "password": "V4lid!Meteor-Cloud-2026",
        "confirm_password": "different", "password_generated": "false",
    })
    assert response.status_code == 422
    assert "Password confirmation does not match." in response.text


def test_rejected_create_preserves_safe_fields_targets_and_focuses_error(
    admin_client, monkeypatch
):
    from types import SimpleNamespace

    target = SimpleNamespace(
        target_id="safe-target",
        target_type="nextcloud",
        display_name="Safe target",
    )
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [target])
    response = admin_client.post("/users/new", data={
        "username": "preserved-user",
        "display_name": "Preserved Name",
        "email": "preserved@example.test",
        "password": "short",
        "confirm_password": "short",
        "target_ids": "safe-target",
    })

    assert response.status_code == 422
    assert 'id="error-summary" role="alert" tabindex="-1"' in response.text
    assert "if (errorSummary) errorSummary.focus();" in response.text
    assert 'value="preserved-user"' in response.text
    assert 'value="Preserved Name"' in response.text
    assert 'value="preserved@example.test"' in response.text
    assert 'value="safe-target" checked' in response.text
    assert 'value="short"' not in response.text


def test_user_create_feedback_is_explicit_and_one_time(admin_client):
    response = admin_client.post("/users/new", data={
        "username": "noticed",
        "display_name": "Noticed User",
        "email": "",
        "password": "V4lid!Copper-Zebra-2026",
    }, follow_redirects=False)
    assert response.status_code == 303

    page = admin_client.get("/users")
    assert 'role="status" tabindex="-1" data-feedback' in page.text
    assert "User created" in page.text
    assert "must replace the temporary password" in page.text
    assert "User created" not in admin_client.get("/users").text


def test_rejected_update_preserves_safe_profile_and_assignment(admin_client, monkeypatch):
    from types import SimpleNamespace
    from na_sso.connectors import IdentityValidation

    admin_client.post("/users/new", data={
        "username": "update-preserved",
        "display_name": "Before",
        "email": "before@example.test",
        "password": "V4lid!Copper-Zebra-2026",
    })
    target = SimpleNamespace(
        target_id="safe-target",
        target_type="nextcloud",
        display_name="Safe target",
        validate_identity=lambda _user: IdentityValidation(True),
    )
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [target])

    response = admin_client.post("/users/1", data={
        "display_name": "After Safe",
        "email": "after@example.test",
        "password": "short",
        "confirm_password": "short",
        "status": "disabled",
        "target_ids": "safe-target",
    })

    assert response.status_code == 422
    assert 'value="After Safe"' in response.text
    assert 'value="after@example.test"' in response.text
    assert 'value="safe-target" checked' in response.text
    assert 'value="short"' not in response.text


def test_user_crud_roundtrip(admin_client):
    c = admin_client
    r = c.post(
        "/users/new",
        data={"username": "jdoe", "display_name": "J. Doe", "email": "j@d.oe",
              "password": "V4lid!Jupiter-Cloud"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = c.get("/users")
    assert "jdoe" in r.text and "CHPW" in r.text
    assert "Password expires" in r.text and "after CHPW" in r.text

    r = c.post(
        "/users/1",
        data={"display_name": "Jane Doe", "email": "j@d.oe", "password": "",
              "status": "disabled"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = c.get("/users")
    assert "disabled" in r.text and "Changes saved" in r.text

    confirmation = c.get("/users/1/delete")
    assert confirmation.status_code == 200
    assert "Delete user everywhere" in confirmation.text
    assert "Restore and purge remain unavailable" in confirmation.text
    assert 'onsubmit="return confirm' not in confirmation.text
    r = c.post("/users/1/delete", follow_redirects=False)
    assert r.status_code == 303
    r = c.get("/users")
    assert "jdoe" in r.text and "deleted" in r.text and "Purge" in r.text
    assert "0 of 0 targets complete" in r.text and "operation " in r.text
    purge = c.get("/users/1/purge")
    assert 'for="confirm-username"' in purge.text
    assert "Type jdoe to confirm" in purge.text
    rejected = c.post("/users/1/purge", data={"confirm_username": "wrong"})
    assert rejected.status_code == 422
    assert "Type jdoe exactly" in rejected.text
    r = c.post(
        "/users/1/purge", data={"confirm_username": "jdoe"}, follow_redirects=False
    )
    assert r.status_code == 303
    page = c.get("/users")
    assert "Local record purged" in page.text
    assert 'href="/users/1"' not in page.text


def test_soft_deleted_user_can_restore_with_new_password(admin_client):
    admin_client.post("/users/new", data={"username": "restoreme", "display_name": "", "email": "", "password": "V4lid!First-Secret-2026"})
    admin_client.post("/users/1/delete")
    restore = admin_client.get("/users/1/restore")
    assert restore.status_code == 200
    assert '<label class="label" for="restore-password">New temporary password</label>' in restore.text
    assert "Confirm temporary password" in restore.text
    mismatch = admin_client.post("/users/1/restore", data={
        "password": "V4lid!New-Secret-2026",
        "confirm_password": "different",
    })
    assert mismatch.status_code == 422
    assert "Password confirmation does not match." in mismatch.text
    assert 'value="V4lid!New-Secret-2026"' not in mismatch.text
    response = admin_client.post("/users/1/restore", data={
        "password": "V4lid!New-Secret-2026",
        "confirm_password": "V4lid!New-Secret-2026",
    }, follow_redirects=False)
    assert response.status_code == 303
    feedback = admin_client.get("/users")
    assert "User restored" in feedback.text
    assert "replace the temporary password" in feedback.text
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.get(ManagedUser, 1)
        assert user.desired_action == "ensure" and user.deleted_at is None
        assert user.pending_secret is None
        assert user.password_decision_kind == "reset"


def test_failed_delete_shows_correlated_progress_and_blocking_target(
    admin_client, monkeypatch
):
    from na_sso.connectors import Connector, SyncResult

    class DeleteConnector(Connector):
        def __init__(self):
            self.name = "nextcloud"
            self.ok = True

        async def ensure_user(self, user, password):
            return SyncResult(self.ok, "saved" if self.ok else "offline")

        async def disable_user(self, user):
            return SyncResult(self.ok, "disabled" if self.ok else "offline")

        async def delete_user(self, user):
            return SyncResult(self.ok, "deleted" if self.ok else "offline")

        async def probe(self):
            return SyncResult(self.ok)

    connector = DeleteConnector()
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    admin_client.post("/users/new", data={
        "username": "delete-progress",
        "display_name": "Delete Progress",
        "email": "",
        "password": "V4lid!Copper-Zebra-2026",
        "target_ids": "nextcloud",
    })
    connector.ok = False
    admin_client.post("/users/1/delete")

    from na_sso.db import get_session
    from na_sso.models import LifecycleOperation
    with get_session() as db:
        operation = db.query(LifecycleOperation).filter_by(
            user_id=1, command="delete"
        ).one()

    page = admin_client.get("/users")
    assert "0 of 1 targets complete" in page.text
    assert "Waiting for nextcloud" in page.text
    assert operation.id[:8] in page.text
    assert "Deletion must finish before recovery" in page.text


def test_duplicate_username_rejected(admin_client):
    c = admin_client
    data = {"username": "dup", "display_name": "", "email": "", "password": "V4lid!Orbit-Cloud-2026"}
    assert c.post("/users/new", data=data, follow_redirects=False).status_code == 303
    assert c.post("/users/new", data=data).status_code == 422


@pytest.mark.parametrize("username", [
    "john.doe", "john-doe", "john_doe", "service_account", "a.b-c_d",
])
def test_username_accepts_supported_separators(admin_client, username):
    response = admin_client.post("/users/new", data={
        "username": username, "display_name": "", "email": "",
        "password": "V4lid!Username-Policy-2026",
    }, follow_redirects=False)
    assert response.status_code == 303


@pytest.mark.parametrize("username", [
    ".john", "john.", "-john", "john-", "_john", "john_", "john@doe", "john doe",
])
def test_username_rejects_unsupported_or_edge_separators(admin_client, username):
    response = admin_client.post("/users/new", data={
        "username": username, "display_name": "", "email": "",
        "password": "V4lid!Username-Policy-2026",
    })
    assert response.status_code == 422
    assert "separators cannot be first or last" in response.text


def test_username_rejects_more_than_64_characters(admin_client):
    response = admin_client.post("/users/new", data={
        "username": "a" * 65, "display_name": "", "email": "",
        "password": "V4lid!Username-Policy-2026",
    })
    assert response.status_code == 422


def test_password_never_plaintext_in_db(admin_client, tmp_path):
    admin_client.post(
        "/users/new",
        data={"username": "sec", "display_name": "", "email": "",
              "password": "V4lid!Orbit-Cloud"},
    )
    blob = (tmp_path / "test.db").read_bytes()
    assert b"V4lid!Orbit-Cloud" not in blob

    from na_sso.db import get_session
    from na_sso.models import ManagedUser

    with get_session() as db:
        u = db.query(ManagedUser).filter(ManagedUser.username == "sec").one()
        assert u.pending_secret is None
        assert u.password_decision_kind == "initial"
