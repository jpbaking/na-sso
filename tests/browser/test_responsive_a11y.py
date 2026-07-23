import json

import pytest
from playwright.sync_api import Locator, Page, expect

from na_sso.db import get_session
from na_sso.models import ManagedUser, utcnow
from na_sso.security import hash_password


pytestmark = pytest.mark.browser

_PASSWORD = "V4lid!Responsive-A11y-2026"
_VIEWPORTS = (
    pytest.param({"width": 390, "height": 844}, id="mobile-390x844"),
    pytest.param({"width": 768, "height": 1024}, id="tablet-768x1024"),
    pytest.param({"width": 1440, "height": 1000}, id="desktop-1440x1000"),
)


def _seed_user(username: str) -> int:
    with get_session() as db:
        user = ManagedUser(
            username=username,
            display_name=username.replace("-", " ").title(),
            email=f"{username}@example.test",
            password_hash=hash_password(_PASSWORD),
            password_changed_at=utcnow(),
        )
        db.add(user)
        db.commit()
        return user.id


def _sign_in(
    page: Page,
    base_url: str,
    *,
    username: str = "admin",
    password: str = "admin-pass",
) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()


def _assert_no_page_overflow(page: Page, surface: str) -> None:
    metrics = page.evaluate(
        """() => {
            const root = document.scrollingElement;
            return {
                scrollWidth: root.scrollWidth,
                innerWidth: window.innerWidth,
                internalOverflow: [...document.querySelectorAll(
                    '.table-wrap, .prose pre'
                )].filter(node => node.scrollWidth > node.clientWidth).length,
            };
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["innerWidth"], (
        f"{surface} overflowed the page body: {metrics}"
    )


def _assert_reachable(page: Page, control: Locator, label: str) -> None:
    expect(control).to_be_visible()
    control.scroll_into_view_if_needed()
    box = control.bounding_box()
    assert box is not None, f"{label} has no visible bounding box"
    viewport = page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"], (
        f"{label} is horizontally clipped: {box}, viewport={viewport}"
    )
    assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], (
        f"{label} is vertically clipped after scrolling: {box}, viewport={viewport}"
    )


def _navigate_admin(page: Page, name: str, viewport_width: int) -> None:
    sidebar = page.locator("aside[aria-label='Primary navigation']")
    if viewport_width <= 768:
        opener = page.get_by_role("button", name="Open navigation", exact=True)
        expect(opener).to_be_visible()
        opener.click()
        expect(sidebar).to_have_attribute("aria-hidden", "false")
    link = sidebar.get_by_role("link", name=name, exact=True)
    _assert_reachable(page, link, f"{name} navigation link")
    link.click()


@pytest.mark.parametrize("viewport", _VIEWPORTS)
def test_core_surfaces_fit_and_keep_navigation_and_actions_reachable(
    page: Page,
    live_server_url: str,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    username = f"responsive-{viewport['width']}"
    user_id = _seed_user(username)

    page.goto(f"{live_server_url}/login")
    expect(page.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()
    _assert_no_page_overflow(page, "sign-in")
    _assert_reachable(
        page,
        page.get_by_role("button", name="Sign in", exact=True),
        "sign-in action",
    )
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill("admin-pass")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page).to_have_url(f"{live_server_url}/dashboard")

    _navigate_admin(page, "Users", viewport["width"])
    expect(page).to_have_url(f"{live_server_url}/users")
    _assert_no_page_overflow(page, "users inventory")
    _assert_reachable(
        page,
        page.get_by_role("link", name="Add user", exact=True),
        "add-user action",
    )

    detail_link = page.locator(f'a[href="/users/{user_id}"]:visible').first
    _assert_reachable(page, detail_link, "managed-user detail link")
    detail_link.click()
    expect(page).to_have_url(f"{live_server_url}/users/{user_id}")
    _assert_no_page_overflow(page, "user detail")
    _assert_reachable(
        page,
        page.get_by_role("link", name="Edit account", exact=True),
        "edit-account action",
    )

    _navigate_admin(page, "Targets", viewport["width"])
    expect(page).to_have_url(f"{live_server_url}/status")
    _assert_no_page_overflow(page, "target status")
    target = page.locator('details[name="target-credentials"]').first
    _assert_reachable(page, target.locator("summary"), "target disclosure")
    target.locator("summary").click()
    _assert_no_page_overflow(page, "expanded target status")
    _assert_reachable(
        page,
        target.get_by_role("button", name="Test connection", exact=True),
        "test-connection action",
    )

    page.context.clear_cookies()
    _sign_in(page, live_server_url, username=username, password=_PASSWORD)
    expect(page).to_have_url(f"{live_server_url}/account")
    _assert_no_page_overflow(page, "managed-user My account")
    account_menu = page.get_by_label("Account menu")
    _assert_reachable(page, account_menu, "account navigation")
    account_menu.click()
    expect(
        page.get_by_role("link", name="My account", exact=True)
    ).to_be_visible()
    _assert_reachable(
        page,
        page.get_by_role("link", name="Change password", exact=True),
        "change-password action",
    )


def test_keyboard_sign_in_modal_focus_trap_and_inventory_control_names(
    page: Page, live_server_url: str
) -> None:
    username = "keyboard-a11y"
    _seed_user(username)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{live_server_url}/login")

    for _ in range(4):
        if page.locator("#username").evaluate(
            "(input) => document.activeElement === input"
        ):
            break
        page.keyboard.press("Tab")
    expect(page.locator("#username")).to_be_focused()
    page.keyboard.type("admin")
    page.keyboard.press("Tab")
    expect(page.locator("#password")).to_be_focused()
    page.keyboard.type("admin-pass")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server_url}/dashboard")

    page.goto(f"{live_server_url}/users/new")
    trigger = page.get_by_role("button", name="Generate", exact=True)
    trigger.focus()
    page.keyboard.press("Enter")
    dialog = page.locator("#generated-password-modal")
    expect(dialog).to_be_visible()
    assert dialog.evaluate("(node) => node.contains(document.activeElement)")

    focusable = dialog.locator(
        "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), "
        "select:not([disabled]), textarea:not([disabled]), "
        "[tabindex]:not([tabindex='-1'])"
    )
    focusable_count = focusable.count()
    assert focusable_count >= 2
    focusable.last.focus()
    page.keyboard.press("Tab")
    expect(focusable.first).to_be_focused()
    focusable.first.focus()
    page.keyboard.press("Shift+Tab")
    expect(focusable.last).to_be_focused()

    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()

    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    close = dialog.get_by_role("button", name="Close", exact=True)
    close.focus()
    page.keyboard.press("Enter")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()

    page.goto(f"{live_server_url}/users")
    inventory_snapshot = page.locator("main").aria_snapshot()
    for accessible_name in (
        "Add user",
        "Bulk import",
        "Filter users",
        "Search",
        f"Select {username}",
        username,
        "View",
        "Delete",
    ):
        assert f'"{accessible_name}"' in inventory_snapshot, (
            f"missing accessible name {accessible_name!r} in:\n"
            f"{inventory_snapshot}"
        )


def _focused_accessibility_scan(page: Page) -> list[dict[str, str]]:
    # No axe-core distribution exists in the offline npm/pip/system sources on
    # this host. This is the task's permitted deterministic fallback subset;
    # it deliberately makes no CDN request and has no intentional suppressions.
    return page.evaluate(
        """() => {
            const violations = [];
            const add = (rule, target, message) => violations.push({
                impact: 'serious', rule, target, message,
            });
            const visible = node => Boolean(
                node.getClientRects().length
                && getComputedStyle(node).visibility !== 'hidden'
            );
            const target = node => {
                if (node.id) return `#${CSS.escape(node.id)}`;
                const name = node.getAttribute('name');
                return name ? `${node.localName}[name="${name}"]` : node.localName;
            };

            const mains = document.querySelectorAll('main, [role="main"]');
            if (mains.length !== 1) {
                add('main-landmark', 'document', `expected one main landmark, found ${mains.length}`);
            }

            const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
                .filter(visible);
            if (!headings.length || headings[0].localName !== 'h1') {
                add('heading-order', 'document', 'visible heading hierarchy must start at h1');
            }
            for (let index = 1; index < headings.length; index += 1) {
                const previous = Number(headings[index - 1].localName.slice(1));
                const current = Number(headings[index].localName.slice(1));
                if (current > previous + 1) {
                    add(
                        'heading-order',
                        target(headings[index]),
                        `heading level jumps from h${previous} to h${current}`,
                    );
                }
            }

            document.querySelectorAll('img').forEach(image => {
                if (!image.hasAttribute('alt')) {
                    add('image-alt', target(image), 'image is missing an alt attribute');
                }
            });

            const controls = document.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
                + 'select, textarea'
            );
            controls.forEach(control => {
                const named = control.labels?.length
                    || control.getAttribute('aria-label')?.trim()
                    || control.getAttribute('aria-labelledby')?.trim()
                    || control.getAttribute('title')?.trim();
                if (!named) {
                    add('form-label', target(control), 'form control has no accessible label');
                }
            });

            document.querySelectorAll('button').forEach(button => {
                if (!visible(button)) return;
                const labelled = button.textContent.trim()
                    || button.getAttribute('aria-label')?.trim()
                    || button.getAttribute('aria-labelledby')?.trim()
                    || button.getAttribute('title')?.trim()
                    || [...button.querySelectorAll('img[alt]')]
                        .some(image => image.alt.trim());
                if (!labelled) {
                    add(
                        'button-name',
                        target(button),
                        `icon-only button has no accessible name: ${button.outerHTML.slice(0, 240)}`,
                    );
                }
            });

            if (!document.documentElement.getAttribute('lang')?.trim()) {
                add('html-lang', 'html', 'document language is not declared');
            }
            return violations;
        }"""
    )


def test_focused_offline_accessibility_rules_on_core_pages(
    page: Page, live_server_url: str
) -> None:
    user_id = _seed_user("a11y-scan")
    pages = [("login", f"{live_server_url}/login")]
    page.goto(pages[0][1])
    results = {"login": _focused_accessibility_scan(page)}

    _sign_in(page, live_server_url)
    expect(page).to_have_url(f"{live_server_url}/dashboard")
    pages = [
        ("users inventory", f"{live_server_url}/users"),
        ("user detail", f"{live_server_url}/users/{user_id}"),
        ("targets status", f"{live_server_url}/status"),
        ("account", f"{live_server_url}/account"),
        ("audit", f"{live_server_url}/audit"),
    ]
    for name, url in pages:
        page.goto(url)
        results[name] = _focused_accessibility_scan(page)

    serious_or_critical = {
        name: violations
        for name, violations in results.items()
        if any(
            violation["impact"] in {"serious", "critical"}
            for violation in violations
        )
    }
    assert not serious_or_critical, json.dumps(
        serious_or_critical,
        indent=2,
        sort_keys=True,
    )
