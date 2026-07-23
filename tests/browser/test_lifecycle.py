import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from playwright.sync_api import Page, expect

from na_sso.db import get_session
from na_sso.models import ManagedUser, SyncState, utcnow
from na_sso.security import encrypt_secret, hash_password


pytestmark = pytest.mark.browser

_PASSWORD = "V4lid!Browser-Journey-2026"


def _sign_in(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill("admin-pass")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page).to_have_url(f"{base_url}/dashboard")


def _seed_local_user(username: str, *, pending_secret: bool = False) -> int:
    with get_session() as db:
        user = ManagedUser(
            username=username,
            display_name=username.replace("-", " ").title(),
            email=f"{username}@example.test",
            password_hash=hash_password(_PASSWORD),
            password_changed_at=utcnow(),
            pending_secret=encrypt_secret(_PASSWORD) if pending_secret else None,
        )
        db.add(user)
        db.commit()
        return user.id


def _seed_synced_user(username: str) -> int:
    user_id = _seed_local_user(username, pending_secret=True)
    from na_sso.sync import _user_locks, sync_user

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(
            asyncio.run, sync_user(user_id, actor="browser-arrange")
        ).result()
    _user_locks.pop(user_id, None)
    with get_session() as db:
        states = db.get(ManagedUser, user_id).sync_states
        assert len(states) == 3
        assert all(state.state == "ok" for state in states)
    return user_id


def _set_mock_availability(mock_url: str, target: str, available: bool) -> None:
    with httpx.Client(trust_env=False) as client:
        response = client.post(
            f"{mock_url}/__mock__/availability/{target}",
            data={"available": str(available).lower()},
        )
        assert response.status_code == 303


def _wait_for_sync_state(page: Page, user_id: int, target: str, state: str) -> None:
    page.wait_for_function(
        r"""async ({ userId, target, expected }) => {
            const response = await fetch('/events/sync?once=true');
            const body = await response.text();
            const line = body.split('\n').find(item => item.startsWith('data: '));
            if (!line) return false;
            const snapshot = JSON.parse(line.slice(6));
            const user = snapshot.users.find(item => item.id === userId);
            return user?.states?.[target]?.state === expected;
        }""",
        arg={"userId": user_id, "target": target, "expected": state},
        timeout=10_000,
    )


def _target_card(page: Page, target_name: str):
    return page.locator("article.card").filter(
        has=page.get_by_role("heading", name=target_name, exact=True)
    )


def _operation_id_for(user_id: int, target: str) -> str:
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        return next(state.operation_id for state in user.sync_states if state.target == target)


def test_sse_state_rendering_matches_fresh_server_render(page: Page, live_server_url: str) -> None:
    user_id = _seed_local_user("state-parity")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        db.add(SyncState(
            user=user,
            target="opnsense",
            target_type="opnsense",
            state="pending",
        ))
        db.commit()

    _sign_in(page, live_server_url)
    detail_url = f"{live_server_url}/users/{user_id}"
    page.goto(detail_url)
    live_cell = page.locator('[data-sync-cell][data-target="opnsense"]')
    expect(live_cell).to_be_visible()

    fresh_page = page.context.new_page()
    fresh_page.route("**/events/sync", lambda route: route.abort())
    cases = [
        ("ok", True, "ok", "", None),
        ("pending", True, "pending", "", None),
        (
            "failed-with-retry",
            True,
            "failed",
            "mock target unavailable",
            utcnow() + timedelta(minutes=5),
        ),
        ("unassigned-disabled", False, "unassigned", "disabled on target", None),
        ("awaiting-credentials", True, "awaiting_credentials", "", None),
        ("unsupported", True, "unsupported", "target cannot disable", None),
    ]
    try:
        for name, assigned, state_value, detail, next_retry_at in cases:
            live_cell.evaluate(
                "(cell, token) => { const probe = document.createElement('i'); "
                "probe.dataset.sseProbe = token; cell.append(probe); }",
                name,
            )
            with get_session() as db:
                state = db.query(SyncState).filter_by(
                    user_id=user_id, target="opnsense"
                ).one()
                state.assigned = assigned
                state.retired = False
                state.state = state_value
                state.detail = detail
                state.attempt_count = 2 if next_retry_at else 0
                state.next_retry_at = next_retry_at
                db.commit()

            fresh_page.goto(detail_url)
            fresh_badge = _target_card(fresh_page, "OPNsense").locator("span.badge")
            expect(fresh_badge).to_be_visible()
            expected_label = fresh_badge.text_content()
            assert expected_label is not None
            expected_description = fresh_badge.get_attribute("aria-label")
            assert expected_description is not None

            expect(live_cell.locator(f'[data-sse-probe="{name}"]')).to_have_count(
                0, timeout=5_000
            )
            live_badge = live_cell.locator("span.badge")
            expect(live_badge).to_have_text(expected_label)
            expect(live_badge).to_have_attribute("aria-label", expected_description)
    finally:
        fresh_page.close()


def test_forced_outage_manual_retry_recovers_under_one_operation(
    page: Page, live_server_url: str, mock_server_url: str
) -> None:
    user_id = _seed_synced_user("retry-recovery")
    _set_mock_availability(mock_server_url, "nexus", False)
    _sign_in(page, live_server_url)

    page.goto(f"{live_server_url}/users/{user_id}/edit")
    page.get_by_label("Status").select_option("disabled")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/users")
    _wait_for_sync_state(page, user_id, "nexus", "failed")

    operation_id = _operation_id_for(user_id, "nexus")
    page.goto(f"{live_server_url}/users/{user_id}")
    nexus = _target_card(page, "Nexus Repository")
    expect(nexus.get_by_text("Retrying", exact=True)).to_be_visible()
    expect(nexus.get_by_text(re.compile(r"Attempt 1 is scheduled"))).to_be_visible()
    retry = nexus.get_by_role("button", name="Retry Nexus Repository", exact=True)
    expect(retry).to_be_visible()

    _set_mock_availability(mock_server_url, "nexus", True)
    retry.click()
    expect(page).to_have_url(f"{live_server_url}/users")
    _wait_for_sync_state(page, user_id, "nexus", "ok")

    page.goto(f"{live_server_url}/audit/operations/{operation_id}")
    expect(page.get_by_text(operation_id, exact=True)).to_be_visible()
    outcome = page.locator(".data-row").filter(has_text="Outcome")
    expect(outcome.get_by_text("succeeded", exact=True)).to_be_visible()
    nexus_attempts = page.locator("article.card").filter(
        has=page.get_by_role("heading", name="nexus", exact=True)
    )
    expect(nexus_attempts).to_have_count(2)
    expect(nexus_attempts.nth(0)).to_contain_text("Attempt 1 · failed")
    expect(nexus_attempts.nth(1)).to_contain_text("Attempt 2 · succeeded")
    expect(page.get_by_text(re.compile(r"sync\.retry"))).to_be_visible()


def test_chpw_delete_hides_restore_until_terminal_then_allows_purge(
    page: Page, live_server_url: str, mock_server_url: str
) -> None:
    username = "delete-contract"
    user_id = _seed_synced_user(username)
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        user.password_decision_required = True
        user.password_decision_kind = "reset"
        for state in user.sync_states:
            state.state = "chpw"
            state.detail = "password change required before propagation"
        db.commit()

    _set_mock_availability(mock_server_url, "nexus", False)
    _sign_in(page, live_server_url)
    page.goto(f"{live_server_url}/users/{user_id}")
    expect(page.get_by_text("Password change required", exact=True)).to_have_count(3)
    page.get_by_role("link", name="Delete", exact=True).click()
    expect(page.get_by_role("heading", name="Delete user", exact=True)).to_be_visible()
    page.get_by_role("button", name="Delete user everywhere", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/users")
    _wait_for_sync_state(page, user_id, "nexus", "failed")

    operation_id = _operation_id_for(user_id, "nexus")
    row = page.locator("table tbody tr").filter(has_text=username)
    expect(row).to_contain_text("Deletion must finish before recovery")
    expect(row.locator(f'a[href="/users/{user_id}/restore"]')).to_have_count(0)
    expect(row.locator(f'a[href="/users/{user_id}/purge"]')).to_have_count(0)

    page.goto(f"{live_server_url}/users/{user_id}")
    nexus = _target_card(page, "Nexus Repository")
    expect(nexus.get_by_text("Retrying", exact=True)).to_be_visible()
    _set_mock_availability(mock_server_url, "nexus", True)
    nexus.get_by_role("button", name="Retry Nexus Repository", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/users")
    _wait_for_sync_state(page, user_id, "nexus", "ok")

    page.goto(f"{live_server_url}/audit/operations/{operation_id}")
    expect(page.get_by_text(operation_id, exact=True)).to_be_visible()
    expect(page.locator(".data-row").filter(has_text="Outcome")).to_contain_text(
        "succeeded"
    )
    expect(page.locator(".data-row").filter(has_text="Progress")).to_contain_text(
        "3/3 complete · 0 failed"
    )

    page.goto(f"{live_server_url}/users")
    row = page.locator("table tbody tr").filter(has_text=username)
    expect(row.get_by_role("link", name="Restore", exact=True)).to_be_visible()
    purge = row.get_by_role("link", name="Purge", exact=True)
    expect(purge).to_be_visible()
    purge.click()
    expect(page.get_by_role("heading", name="Purge local record", exact=True)).to_be_visible()
    page.get_by_label(f'Type "{username}" to confirm').fill(username)
    page.get_by_role(
        "button", name="Permanently purge local record", exact=True
    ).click()
    expect(page).to_have_url(f"{live_server_url}/users")
    expect(page.get_by_text("Local record purged", exact=True)).to_be_visible()
