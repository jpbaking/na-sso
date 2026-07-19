import pytest

from na_sso.models import ManagedUser, utcnow
from na_sso.security import hash_password


PASSWORD = "V4lid!Scoped-Role-2026"


def _add_account(username: str, role: str) -> int:
    from na_sso.db import get_session

    with get_session() as db:
        account = ManagedUser(
            username=username,
            display_name=username.replace("_", " ").title(),
            password_hash=hash_password(PASSWORD),
            password_changed_at=utcnow(),
            role=role,
            status="active",
            desired_action="ensure",
        )
        db.add(account)
        db.commit()
        return account.id


def _login(client, username: str):
    client.post("/logout")
    response = client.post(
        "/login", data={"username": username, "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


@pytest.mark.parametrize(("role", "home", "allowed", "denied", "visible", "hidden"), [
    ("user_operator", "/dashboard", "/users", ("/status", "/audit", "/audit/export.json", "/notifications", "/service-accounts"), "Users", ("Targets", "Audit", "Notifications", "Service accounts")),
    ("target_operator", "/dashboard", "/status", ("/users", "/users/bulk/import", "/reconciliation", "/assignment-profiles", "/access-reviews", "/audit", "/audit/export.json", "/notifications", "/service-accounts"), "Targets", ("Users", "Reconciliation", "Assignment profiles", "Access reviews", "Audit", "Notifications", "Service accounts")),
    ("auditor", "/dashboard", "/audit", ("/users", "/users/bulk/import", "/reconciliation", "/assignment-profiles", "/access-reviews", "/status", "/notifications", "/service-accounts"), "Audit", ("Users", "Reconciliation", "Assignment profiles", "Access reviews", "Targets", "Notifications", "Service accounts")),
    ("user", "/account", "/account", ("/users", "/users/bulk/import", "/reconciliation", "/assignment-profiles", "/access-reviews", "/status", "/audit", "/audit/export.json", "/notifications", "/service-accounts"), "My account", ("Users", "Reconciliation", "Assignment profiles", "Access reviews", "Targets", "Audit", "Notifications", "Service accounts")),
])
def test_scoped_roles_enforce_routes_and_navigation(
    admin_client, role, home, allowed, denied, visible, hidden
):
    username = role
    _add_account(username, role)
    login = _login(admin_client, username)
    assert login.headers["location"] == home

    page = admin_client.get(allowed)
    assert page.status_code == 200
    assert visible in page.text
    if role == "user_operator":
        assert 'class="sidebar-link-label">Reconciliation</span>' in page.text
        assert 'class="sidebar-link-label">Assignment profiles</span>' in page.text
        assert 'class="sidebar-link-label">Access reviews</span>' in page.text
    for label in hidden:
        assert f'class="sidebar-link-label">{label}</span>' not in page.text
    for path in denied:
        assert admin_client.get(path, follow_redirects=False).status_code == 403


def test_only_root_can_assign_roles_or_manage_operator_accounts(admin_client):
    auditor_id = _add_account("existing_auditor", "auditor")
    _add_account("delegated_users", "user_operator")
    _login(admin_client, "delegated_users")

    created = admin_client.post("/users/new", data={
        "username": "forged_operator",
        "display_name": "Forged operator",
        "email": "forged@example.test",
        "password": PASSWORD,
        "role": "target_operator",
    }, follow_redirects=False)
    assert created.status_code == 303
    assert admin_client.get(f"/users/{auditor_id}/edit").status_code == 403
    assert admin_client.post(f"/users/{auditor_id}", data={
        "display_name": "Changed", "email": "", "status": "disabled", "role": "user",
    }).status_code == 403

    from na_sso.db import get_session
    with get_session() as db:
        forged = db.query(ManagedUser).filter_by(username="forged_operator").one()
        auditor = db.get(ManagedUser, auditor_id)
        assert forged.role == "user"
        assert auditor.role == "auditor" and auditor.status == "active"


def test_root_role_assignment_is_explicit_audited_and_recovery_safe(admin_client):
    form = admin_client.get("/users/new")
    assert "Access role" in form.text
    assert "User operator" in form.text
    assert "Target operator" in form.text
    assert "Auditor" in form.text
    assert "Root security administrator" not in form.text

    response = admin_client.post("/users/new", data={
        "username": "audit_delegate",
        "display_name": "Audit Delegate",
        "email": "delegate@example.test",
        "password": PASSWORD,
        "role": "auditor",
    }, follow_redirects=False)
    assert response.status_code == 303

    from na_sso.db import get_session
    from na_sso.models import AuditEvent
    with get_session() as db:
        delegate = db.query(ManagedUser).filter_by(username="audit_delegate").one()
        assert delegate.role == "auditor"
        assignment = db.query(AuditEvent).filter_by(
            action="role.assigned", subject="audit_delegate"
        ).one()
        assert assignment.actor == "admin" and "role=auditor" in assignment.detail
        root = db.get(ManagedUser, 0)
        root.role = "user"
        db.commit()
        assert root.role == "root"


def test_target_mutations_and_audit_export_use_the_same_scope_checks(admin_client):
    _add_account("target_delegate", "target_operator")
    _add_account("audit_delegate_two", "auditor")

    _login(admin_client, "target_delegate")
    allowed = admin_client.post("/targets/missing/probe", follow_redirects=False)
    assert allowed.status_code == 303
    assert admin_client.get("/audit/export.json").status_code == 403

    _login(admin_client, "audit_delegate_two")
    assert admin_client.get("/audit/export.json").status_code == 200
    assert admin_client.post("/targets/missing/probe").status_code == 403


def test_legacy_admin_role_is_migrated_to_user_operator(client):
    from sqlalchemy import text
    from na_sso import db as database
    from na_sso.db import get_session

    account_id = _add_account("legacy_admin", "user")
    with get_session() as db:
        db.execute(text(
            "UPDATE managed_users SET role='admin' WHERE id=:id"
        ), {"id": account_id})
        db.commit()
    database.init_db()
    with get_session() as db:
        assert db.get(ManagedUser, account_id).role == "user_operator"
