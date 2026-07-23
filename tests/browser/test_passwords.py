import re
from datetime import timedelta

import pytest
from playwright.sync_api import Page, expect

from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.lifecycle import sync_state_payload
from na_sso.models import ManagedUser, SyncState, as_utc, utcnow
from na_sso.security import hash_password


pytestmark = pytest.mark.browser

_TEMPORARY = "V4lid!Orbit-Cloud-2026"
_CHANGED = "N3w!Meteor-River-2027"
_NORMAL_CURRENT = "V4lid!Copper-Zebra-2026"
_NORMAL_CHANGED = "N3w!Marble-Quartz-2027"
_EXPIRED_CURRENT = "V4lid!Comet-Bridge-2026"
_EXPIRED_CHANGED = "N3w!Aurora-Forest-2027"


def _seed_user(
    username: str,
    password: str,
    *,
    decision_kind: str = "",
    expired: bool = False,
) -> int:
    changed_at = utcnow()
    if expired:
        expiry_days = get_settings().file.password_policy.expires_after_days
        assert expiry_days is not None
        changed_at -= timedelta(days=expiry_days + 30)
        decision_kind = "expired"
    with get_session() as db:
        user = ManagedUser(
            username=username,
            display_name=username.replace("-", " ").title(),
            email=f"{username}@example.test",
            password_hash=hash_password(password),
            password_changed_at=changed_at,
            password_decision_required=bool(decision_kind),
            password_decision_kind=decision_kind,
        )
        db.add(user)
        db.commit()
        return user.id


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()


def _change_password(
    page: Page,
    *,
    current_password: str,
    new_password: str,
) -> None:
    page.get_by_label("Current password", exact=True).fill(current_password)
    page.get_by_label("New password", exact=True).fill(new_password)
    page.get_by_label("Confirm new password", exact=True).fill(new_password)
    page.get_by_role("button", name="Change password", exact=True).click()


def _expect_password_changed_notice(page: Page, base_url: str) -> None:
    expect(page).to_have_url(f"{base_url}/login")
    notice = page.locator("[data-feedback]")
    expect(notice).to_be_visible()
    expect(notice.locator(".alert-title")).to_have_text("Password changed")
    expect(notice).to_contain_text("Sign in again with the new password.")
    expect(notice).to_contain_text("Target synchronization has started.")


def test_temporary_password_routes_to_chpw_and_reports_change_before_relogin(
    page: Page, live_server_url: str
) -> None:
    _seed_user(
        "temporary-password",
        _TEMPORARY,
        decision_kind="initial",
    )

    _login(page, live_server_url, "temporary-password", _TEMPORARY)
    expect(page).to_have_url(f"{live_server_url}/account/password-decision")
    expect(
        page.get_by_role("heading", name="Change your temporary password", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("Replace the temporary password", exact=False)).to_be_visible()

    _change_password(
        page,
        current_password=_TEMPORARY,
        new_password=_CHANGED,
    )
    _expect_password_changed_notice(page, live_server_url)

    _login(page, live_server_url, "temporary-password", _CHANGED)
    expect(page).to_have_url(f"{live_server_url}/account")


def test_normal_account_password_change_reports_outcome_and_rotates_login(
    page: Page, live_server_url: str
) -> None:
    _seed_user("normal-password", _NORMAL_CURRENT)
    _login(page, live_server_url, "normal-password", _NORMAL_CURRENT)
    expect(page).to_have_url(f"{live_server_url}/account")
    page.get_by_role("link", name="Change password", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/account/password")

    _change_password(
        page,
        current_password=_NORMAL_CURRENT,
        new_password=_NORMAL_CHANGED,
    )
    _expect_password_changed_notice(page, live_server_url)

    _login(page, live_server_url, "normal-password", _NORMAL_CURRENT)
    expect(page).to_have_url(f"{live_server_url}/login")
    expect(page.locator("#error-summary")).to_contain_text("Invalid credentials.")

    _login(page, live_server_url, "normal-password", _NORMAL_CHANGED)
    expect(page).to_have_url(f"{live_server_url}/account")


def test_admin_reset_states_handoff_then_user_completes_temporary_flow(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user("admin-reset", _NORMAL_CURRENT)
    _login(page, live_server_url, "admin", "admin-pass")
    expect(page).to_have_url(f"{live_server_url}/dashboard")
    page.goto(f"{live_server_url}/users/{user_id}/edit")

    page.get_by_role("button", name="Generate", exact=True).click()
    modal = page.locator("#generated-password-modal")
    expect(modal).to_be_visible()
    expect(modal.get_by_role("heading", name="Generated password", exact=True)).to_be_visible()
    expect(modal).to_contain_text("Copy this password now")
    expect(modal).to_contain_text("The full password will not be shown again")
    page.get_by_role("button", name="Show", exact=True).click()
    temporary_password = page.locator("#generated-password-value").input_value()
    assert temporary_password
    page.get_by_role("button", name="I saved this password", exact=True).click()
    page.get_by_role("button", name="Save changes", exact=True).click()

    expect(page).to_have_url(f"{live_server_url}/users")
    reset_notice = page.locator("[data-feedback]")
    expect(reset_notice).to_be_visible()
    expect(reset_notice.locator(".alert-title")).to_have_text("Changes saved")
    expect(reset_notice).to_contain_text(
        "admin-reset was updated; a temporary password was set and the user "
        "must change it at next sign-in. Target synchronization has started."
    )

    page.context.clear_cookies()
    _login(page, live_server_url, "admin-reset", temporary_password)
    expect(page).to_have_url(f"{live_server_url}/account/password-decision")
    expect(
        page.get_by_role("heading", name="Change your temporary password", exact=True)
    ).to_be_visible()
    _change_password(
        page,
        current_password=temporary_password,
        new_password=_CHANGED,
    )
    _expect_password_changed_notice(page, live_server_url)


def test_expired_password_change_routes_to_expiry_flow_and_reports_outcome(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user(
        "expired-change",
        _EXPIRED_CURRENT,
        expired=True,
    )
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert as_utc(user.password_expires_at) < utcnow()
        assert user.password_decision_kind == "expired"

    _login(page, live_server_url, "expired-change", _EXPIRED_CURRENT)
    expect(page).to_have_url(f"{live_server_url}/account/password-decision")
    expect(
        page.get_by_role("heading", name="Accept or change your password", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("14-day grace acknowledgement", exact=False)).to_be_visible()

    _change_password(
        page,
        current_password=_EXPIRED_CURRENT,
        new_password=_EXPIRED_CHANGED,
    )
    _expect_password_changed_notice(page, live_server_url)


def test_expired_password_keep_previews_date_then_reports_recorded_extension(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user(
        "expired-keep",
        _EXPIRED_CURRENT,
        expired=True,
    )
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        original_changed_at = as_utc(user.password_changed_at)
        assert as_utc(user.password_expires_at) < utcnow()

    _login(page, live_server_url, "expired-keep", _EXPIRED_CURRENT)
    expect(page).to_have_url(f"{live_server_url}/account/password-decision")
    preview = page.locator(".alert-info").filter(
        has_text="14-day grace acknowledgement"
    )
    expect(preview).to_be_visible()
    preview_text = preview.inner_text()
    date_match = re.search(r"expire again on (\d{4}-\d{2}-\d{2})", preview_text)
    assert date_match is not None
    extension_date = date_match.group(1)
    expect(preview).to_contain_text("acknowledgement 1 of 1")
    keep = page.get_by_role(
        "button",
        name=f"Keep until {extension_date}",
        exact=True,
    )
    expect(keep).to_be_visible()
    page.get_by_label("Current password", exact=True).fill(_EXPIRED_CURRENT)
    keep.click()

    expect(page).to_have_url(f"{live_server_url}/account")
    notice = page.locator("[data-feedback]")
    expect(notice).to_be_visible()
    expect(notice.locator(".alert-title")).to_have_text("Password kept")
    expect(notice).to_contain_text(
        f"The current password remains active until {extension_date}."
    )
    expect(notice).to_contain_text("The acknowledgement was recorded.")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert as_utc(user.password_changed_at) == original_changed_at
        assert as_utc(user.password_keep_until).date().isoformat() == extension_date
        assert user.password_keep_count == 1
        assert user.password_decision_required is False


def test_my_access_matches_assigned_db_states_and_hides_unassigned_target(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user("access-truth", _NORMAL_CURRENT)
    retry_at = utcnow() + timedelta(minutes=5)
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        db.add_all([
            SyncState(
                user=user,
                target="opnsense",
                target_type="opnsense",
                assigned=True,
                state="ok",
                detail="mock target matches",
            ),
            SyncState(
                user=user,
                target="nexus",
                target_type="nexus",
                assigned=True,
                state="failed",
                detail="mock target unavailable",
                attempt_count=2,
                next_retry_at=retry_at,
            ),
            SyncState(
                user=user,
                target="nextcloud",
                target_type="nextcloud",
                assigned=False,
                state="unassigned",
                detail="disabled on target",
            ),
        ])
        db.commit()
        expected = {
            state.target: sync_state_payload(
                state.state,
                assigned=state.assigned,
                retired=state.retired,
                desired_action=user.desired_action,
                next_retry_at=state.next_retry_at,
            )
            for state in user.sync_states
        }
    assert expected["opnsense"]["state"] == "ok"
    assert expected["nexus"]["state"] == "failed"
    assert expected["nextcloud"]["assigned"] is False

    _login(page, live_server_url, "access-truth", _NORMAL_CURRENT)
    expect(page).to_have_url(f"{live_server_url}/account")
    expect(page.get_by_role("heading", name="Assigned targets", exact=True)).to_be_visible()

    ok_card = page.locator("article.card").filter(
        has=page.get_by_role("heading", name="OPNsense", exact=True)
    )
    ok_presentation = expected["opnsense"]["presentation"]
    expect(ok_card.locator("span.badge")).to_have_text(ok_presentation["label"])
    expect(ok_card.locator("span.badge")).to_have_attribute(
        "aria-label",
        f"{ok_presentation['label']}. {ok_presentation['description']}",
    )
    expect(ok_card.locator(".card-desc")).to_have_text(ok_presentation["description"])

    failed_card = page.locator("article.card").filter(
        has=page.get_by_role("heading", name="Nexus Repository", exact=True)
    )
    failed_presentation = expected["nexus"]["presentation"]
    expect(failed_card.locator("span.badge")).to_have_text(
        failed_presentation["label"]
    )
    expect(failed_card.locator("span.badge")).to_have_attribute(
        "aria-label",
        f"{failed_presentation['label']}. {failed_presentation['description']}",
    )
    expect(failed_card.locator(".card-desc")).to_have_text(
        failed_presentation["description"]
    )
    expect(failed_card).to_contain_text("Automatic retry after")
    guidance = failed_card.locator(".alert-info")
    expect(guidance).to_contain_text("Operator help needed")
    expect(guidance).to_contain_text(
        "Share your username and the affected target name; "
        "do not send passwords or private keys."
    )
    expect(guidance).to_contain_text("Contact your NA-SSO administrator")

    expect(
        page.get_by_role("heading", name="Nextcloud", exact=True)
    ).to_have_count(0)
    with get_session() as db:
        states = {
            state.target: (state.state, state.assigned, state.next_retry_at)
            for state in db.get(ManagedUser, user_id).sync_states
        }
    assert states["opnsense"][:2] == ("ok", True)
    assert states["nexus"][0:2] == ("failed", True)
    assert states["nexus"][2] is not None
    assert states["nextcloud"][:2] == ("unassigned", False)
