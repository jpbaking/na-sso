import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.browser


def test_root_admin_can_sign_in(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")

    expect(page).to_have_title("Sign in — Not Another SSO")
    expect(page.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()

    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill("admin-pass")
    page.get_by_role("button", name="Sign in", exact=True).click()

    expect(page).to_have_url(f"{live_server_url}/dashboard")
    expect(page).to_have_title("Dashboard — Not Another SSO")
    expect(page.get_by_label("Primary navigation")).to_be_visible()
    expect(page.get_by_role("link", name="Dashboard", exact=True)).to_be_visible()
