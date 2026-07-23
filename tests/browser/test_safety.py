import re

import pytest
from playwright.sync_api import Page, expect

from na_sso.db import get_session
from na_sso.models import ManagedUser, utcnow
from na_sso.security import hash_password


pytestmark = pytest.mark.browser

_PASSWORD = "V4lid!Browser-Safety-2026"


def _sign_in(
    page: Page,
    base_url: str,
    *,
    username: str = "admin",
    password: str = "admin-pass",
) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page).to_have_url(
        f"{base_url}/dashboard" if username == "admin" else f"{base_url}/account"
    )


def _seed_user(username: str, *, deleted: bool = False) -> int:
    with get_session() as db:
        user = ManagedUser(
            username=username,
            display_name=username.replace("-", " ").title(),
            email=f"{username}@example.test",
            password_hash=hash_password(_PASSWORD),
            password_changed_at=utcnow(),
            desired_action="delete" if deleted else "ensure",
            deleted_at=utcnow() if deleted else None,
            deletion_requested_at=utcnow() if deleted else None,
            status="disabled" if deleted else "active",
        )
        db.add(user)
        db.commit()
        return user.id


def _inventory_row(page: Page, username: str):
    return page.locator(".inventory-desktop tbody tr").filter(
        has=page.get_by_text(username, exact=True)
    )


def test_root_affordances_are_protected_and_account_security_requires_current_password(
    page: Page, live_server_url: str
) -> None:
    managed_id = _seed_user("root-control-proof")
    _sign_in(page, live_server_url)
    page.goto(f"{live_server_url}/users")

    root_row = _inventory_row(page, "admin")
    expect(root_row).to_have_count(1)
    expect(root_row.get_by_text("Protected system account", exact=True)).to_be_visible()
    expect(root_row.locator("input[type=checkbox]")).to_be_disabled()
    expect(root_row.locator("a, button, select")).to_have_count(0)
    expect(root_row).to_contain_text("N/A")

    managed_row = _inventory_row(page, "root-control-proof")
    expect(managed_row).to_have_count(1)
    expect(managed_row.locator("input[type=checkbox]")).to_be_enabled()
    expect(
        managed_row.get_by_role("link", name="View", exact=True)
    ).to_have_attribute("href", f"/users/{managed_id}")
    expect(managed_row.locator(f'a[href="/users/{managed_id}/delete"]')).to_be_visible()

    page.locator("aside").get_by_label("Account menu").click()
    page.locator("aside").get_by_role("link", name="My account", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/account")
    expect(page.get_by_role("heading", name="Account", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Account password", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Multi-factor authentication", exact=True)).to_be_visible()

    page.get_by_role("link", name="Change password", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/account/password")
    expect(page.get_by_text("// Account security", exact=True)).to_be_visible()
    expect(page.get_by_label("Current password", exact=True)).to_be_visible()
    expect(page.get_by_label("New password", exact=True)).to_be_visible()


def test_invalid_create_and_restore_keep_context_and_focus_visible_errors(
    page: Page, live_server_url: str
) -> None:
    restore_id = _seed_user("restore-error-proof", deleted=True)
    _sign_in(page, live_server_url)
    page.goto(f"{live_server_url}/users/new")

    page.get_by_label("Username", exact=True).fill("preserved-browser")
    page.get_by_label("Display name", exact=True).fill("Preserved Browser")
    page.get_by_label("Email", exact=True).fill("preserved-browser@example.test")
    page.get_by_label("Password", exact=True).fill("short")
    page.get_by_label("Confirm password", exact=True).fill("short")
    selected_targets = ("opnsense", "nextcloud")
    for target_id in selected_targets:
        page.locator(f'input[name="target_ids"][value="{target_id}"]').check()
    page.get_by_role("button", name="Create user", exact=True).click()

    expect(page).to_have_url(f"{live_server_url}/users/new")
    summary = page.locator("#error-summary")
    expect(summary).to_be_visible()
    expect(summary).to_contain_text("Password")
    expect(summary).to_be_focused()
    password = page.locator("#password")
    password_error = page.locator("#password-error")
    expect(password_error).to_be_visible()
    expect(password_error).to_contain_text("Password")
    expect(password).to_have_attribute("aria-invalid", "true")
    password_descriptions = password.get_attribute("aria-describedby")
    assert password_descriptions is not None
    assert {"password-checks", "password-error"} <= set(
        password_descriptions.split()
    )
    expect(page.get_by_label("Username", exact=True)).to_have_value("preserved-browser")
    expect(page.get_by_label("Display name", exact=True)).to_have_value("Preserved Browser")
    expect(page.get_by_label("Email", exact=True)).to_have_value(
        "preserved-browser@example.test"
    )
    for target_id in selected_targets:
        expect(
            page.locator(f'input[name="target_ids"][value="{target_id}"]')
        ).to_be_checked()
    password_field = page.locator("#password").locator("xpath=..")
    minimum_length = password_field.locator(".data-row").filter(
        has_text="Minimum length"
    )
    expect(minimum_length).to_contain_text("not yet")

    page.goto(f"{live_server_url}/users/{restore_id}/restore")
    expect(page.get_by_role("heading", name="Restore user", exact=True)).to_be_visible()
    page.get_by_label("New temporary password", exact=True).fill("short")
    page.get_by_label("Confirm temporary password", exact=True).fill("short")
    page.get_by_role("button", name="Restore user", exact=True).click()

    expect(page).to_have_url(f"{live_server_url}/users/{restore_id}/restore")
    restore_error = page.locator("#error-summary")
    expect(restore_error).to_be_visible()
    expect(restore_error).to_contain_text("Password")
    expect(restore_error).to_be_focused()
    restore_password = page.locator("#restore-password")
    restore_password_error = page.locator("#restore-password-error")
    expect(restore_password_error).to_be_visible()
    expect(restore_password_error).to_contain_text("Password")
    expect(restore_password).to_have_attribute("aria-invalid", "true")
    restore_descriptions = restore_password.get_attribute("aria-describedby")
    assert restore_descriptions is not None
    assert {"password-checks", "restore-password-error"} <= set(
        restore_descriptions.split()
    )
    expect(page.get_by_role("heading", name="Restore user", exact=True)).to_be_visible()


def test_target_credentials_failure_stays_actionable_then_recovers(
    page: Page, live_server_url: str, modern_target_config: str
) -> None:
    _sign_in(page, live_server_url)
    page.goto(f"{live_server_url}/status")
    target = page.locator('details[name="target-credentials"]').filter(
        has=page.get_by_text("Browser Nexus", exact=True)
    )
    expect(target).to_have_count(1)
    target.locator("summary").click()

    credentials = target.locator(
        f'form[action="/targets/{modern_target_config}/credentials"]'
    )
    credentials.get_by_label("Admin user", exact=True).fill("admin")
    credentials.get_by_label("Admin password", exact=True).fill(
        "invalid-browser-secret"
    )
    credentials.get_by_role("button", name="Save credentials", exact=True).click()

    expect(page).to_have_url(
        re.compile(rf"{re.escape(live_server_url)}/status\?target={modern_target_config}$")
    )
    expect(target).to_have_attribute("open", "")
    expect(target.get_by_role("alert")).to_contain_text("Connection needs attention")
    expect(target).to_contain_text("auth failed")
    expect(target).not_to_contain_text("invalid-browser-secret")
    last_checked = target.locator(".data-row").filter(has_text="Last checked")
    expect(last_checked).not_to_contain_text("Not checked")
    retry = target.get_by_role("button", name="Test connection", exact=True)
    expect(retry).to_be_visible()
    retry.click()
    expect(page.locator("[data-feedback]")).to_contain_text("Connection check failed")
    expect(target).to_have_attribute("open", "")

    credentials.get_by_label("Admin user", exact=True).fill("admin")
    credentials.get_by_label("Admin password", exact=True).fill("demo-password")
    credentials.get_by_role("button", name="Replace credentials", exact=True).click()

    expect(page.locator("[data-feedback]")).to_contain_text("Credentials verified")
    expect(target).to_have_attribute("open", "")
    expect(target).to_contain_text("Verified")
    expect(target).to_contain_text("Reachable")
    expect(target.get_by_role("button", name="Test connection", exact=True)).to_be_visible()


def test_generated_password_and_browser_ssh_require_saved_confirmation(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user("handoff-gating")
    _sign_in(page, live_server_url)
    page.goto(f"{live_server_url}/users/new")

    create = page.get_by_role("button", name="Create user", exact=True)
    page.get_by_role("button", name="Generate", exact=True).click()
    generated = page.locator("#generated-password-value")
    expect(generated).to_be_visible()
    expect(generated).to_have_attribute("readonly", "")
    expect(create).to_be_disabled()
    page.get_by_role("button", name="Show", exact=True).click()
    expect(generated).to_have_attribute("type", "text")
    secret = generated.input_value()
    assert len(secret) >= 16
    assert generated.evaluate(
        "(input) => input.selectionStart === 0 && "
        "input.selectionEnd === input.value.length"
    )
    expect(create).to_be_disabled()
    page.get_by_role("button", name="I saved this password", exact=True).click()
    expect(create).to_be_enabled()

    page.context.clear_cookies()
    _sign_in(
        page,
        live_server_url,
        username="handoff-gating",
        password=_PASSWORD,
    )
    page.goto(f"{live_server_url}/account")
    expect(page.get_by_role("heading", name="SSH keys", exact=True)).to_be_visible()
    enrol = page.get_by_role("button", name="3. Enrol public key", exact=True)
    page.get_by_role("button", name="1. Generate in browser", exact=True).click()
    handoff = page.locator("#key-handoff")
    expect(handoff).to_be_visible()
    private_key = page.locator("#private-key-output")
    expect(private_key).to_have_value(re.compile(r"BEGIN PRIVATE KEY"))
    expect(enrol).to_be_disabled()

    with page.expect_download() as download_info:
        page.get_by_role("button", name="2. Save private key file", exact=True).click()
    assert download_info.value.suggested_filename == "na-sso_ed25519"
    expect(enrol).to_be_disabled()
    page.get_by_label(
        "I saved the private key file or copied the full value above.",
        exact=True,
    ).check()
    expect(enrol).to_be_enabled()
    page.get_by_label("Key name", exact=True).fill("Browser handoff proof")
    enrol.click()

    expect(page).to_have_url(f"{live_server_url}/account")
    expect(page.locator("[data-feedback]")).to_contain_text("SSH key enrolled")
    expect(page.get_by_text("Browser handoff proof", exact=True)).to_be_visible()
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert [key.name for key in user.active_ssh_keys] == ["Browser handoff proof"]
