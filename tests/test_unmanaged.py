from na_sso.connectors.base import AccountDiscovery, RemoteAccount, SyncResult


class DiscoveryConnector:
    target_id = "shell"
    target_type = "ssh"
    display_name = "Engineering shell"

    def __init__(self, accounts):
        self.accounts = tuple(accounts)
        self.delete_calls = []

    async def discover_accounts(self):
        return AccountDiscovery(True, self.accounts)

    async def delete_user(self, user):
        self.delete_calls.append(user.username)
        return SyncResult(True, "deleted")


def test_discovery_is_read_only_excludes_system_accounts_and_persists_ignore(admin_client, monkeypatch):
    connector = DiscoveryConnector([
        RemoteAccount("root", "Root", uid=0),
        RemoteAccount("outside", "Outside Person", "outside@example.test", "active", 2001),
    ])
    monkeypatch.setattr("na_sso.unmanaged.get_connectors", lambda: [connector])

    response = admin_client.post("/unmanaged-accounts/discover", follow_redirects=False)

    assert response.status_code == 303
    assert connector.delete_calls == []
    page = admin_client.get("/unmanaged-accounts")
    assert "outside" in page.text and "Outside Person" in page.text
    assert ">root<" not in page.text
    from na_sso.db import get_session
    from na_sso.models import UnmanagedAccountFinding
    with get_session() as db:
        finding = db.query(UnmanagedAccountFinding).filter_by(username="outside").one()
        finding_id = finding.id
    ignored = admin_client.post(f"/unmanaged-accounts/{finding_id}/ignore", follow_redirects=False)
    assert ignored.status_code == 303
    admin_client.post("/unmanaged-accounts/discover")
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        assert finding.decision == "ignored" and finding.present


def test_adoption_links_remote_identity_without_connector_mutation(admin_client, monkeypatch):
    connector = DiscoveryConnector([
        RemoteAccount("adoptme", "Adopt Person", "adopt@example.test", "active", 2002),
    ])
    monkeypatch.setattr("na_sso.unmanaged.get_connectors", lambda: [connector])
    admin_client.post("/unmanaged-accounts/discover")
    from na_sso.db import get_session
    from na_sso.models import UnmanagedAccountFinding, ManagedUser
    with get_session() as db:
        finding_id = db.query(UnmanagedAccountFinding).filter_by(username="adoptme").one().id

    adopted = admin_client.post(f"/unmanaged-accounts/{finding_id}/adopt", data={
        "temporary_password": "V4lid!Comet-Bridge-2026",
        "confirm_password": "V4lid!Comet-Bridge-2026",
    }, follow_redirects=False)

    assert adopted.status_code == 303
    assert connector.delete_calls == []
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="adoptme").one()
        assert user.password_decision_kind == "initial"
        assert len(user.sync_states) == 1
        assert user.sync_states[0].target == "shell"
        assert user.sync_states[0].state == "chpw"


def test_remote_removal_requires_root_two_steps_and_one_use_token(admin_client, monkeypatch):
    connector = DiscoveryConnector([
        RemoteAccount("obsolete", "Obsolete Person", "", "disabled", 2003),
    ])
    monkeypatch.setattr("na_sso.unmanaged.get_connectors", lambda: [connector])
    from na_sso.config import get_settings
    file_config = get_settings().file
    file_config.unmanaged_account_policy.allow_removal = True
    monkeypatch.setattr(
        "na_sso.unmanaged.get_settings",
        lambda: type("Settings", (), {"file": file_config})(),
    )
    admin_client.post("/unmanaged-accounts/discover")
    from na_sso.db import get_session
    from na_sso.models import UnmanagedAccountFinding
    with get_session() as db:
        finding_id = db.query(UnmanagedAccountFinding).filter_by(username="obsolete").one().id

    rejected = admin_client.post(f"/unmanaged-accounts/{finding_id}/approve-removal", data={
        "confirmation": "obsolete", "recovery_acknowledged": "",
    })
    assert rejected.status_code == 422 and connector.delete_calls == []
    approved = admin_client.post(f"/unmanaged-accounts/{finding_id}/approve-removal", data={
        "confirmation": "obsolete", "recovery_acknowledged": "true",
    }, follow_redirects=False)
    assert approved.status_code == 303 and connector.delete_calls == []
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        token = finding.removal_token
        assert finding.decision == "removal_approved" and token
    executed = admin_client.post(f"/unmanaged-accounts/{finding_id}/execute-removal", data={
        "confirmation": "obsolete", "token": token,
    }, follow_redirects=False)
    assert executed.status_code == 303
    assert connector.delete_calls == ["obsolete"]
    replay = admin_client.post(f"/unmanaged-accounts/{finding_id}/execute-removal", data={
        "confirmation": "obsolete", "token": token,
    })
    assert replay.status_code == 409
    assert connector.delete_calls == ["obsolete"]
