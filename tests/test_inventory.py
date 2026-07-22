from datetime import datetime, timezone
import re

from na_sso.inventory import InventoryParams, query_inventory
from na_sso.models import ManagedUser, SyncState


def _large_inventory(client):
    from na_sso.db import get_session

    with get_session() as db:
        for index in range(30):
            user = ManagedUser(
                username=f"user{index:02d}",
                display_name=f"Person {29 - index:02d}",
                email=f"user{index:02d}@example.test",
                status="disabled" if index == 0 else "active",
                password_decision_required=index == 1,
                password_decision_kind="initial" if index == 1 else "",
                desired_action="delete" if index in {2, 3} else "ensure",
                deleted_at=datetime.now(timezone.utc) if index == 2 else None,
            )
            db.add(user)
            db.flush()
            db.add(SyncState(
                user=user,
                target="cloud" if index % 2 == 0 else "shell",
                assigned=True,
                state="failed" if index in {4, 5} else "ok",
                next_retry_at=datetime.now(timezone.utc) if index == 5 else None,
            ))
        db.commit()


def test_inventory_parameters_are_bounded_and_urls_are_stable():
    params = InventoryParams.parse({
        "q": "  " + ("x" * 120) + "  ",
        "lifecycle": "unknown",
        "issues": "broken",
        "sort": "invalid",
        "direction": "sideways",
        "page": "-4",
        "per_page": "999",
    })
    assert len(params.search) == 100
    assert params.lifecycle == "all" and params.issues == "all"
    assert params.sort == "username" and params.direction == "asc"
    assert params.page == 1 and params.per_page == 100
    assert params.url(page=2) == (
        "/users?q=" + ("x" * 100)
        + "&lifecycle=all&target=&issues=all&sort=username&direction=asc&page=2&per_page=100"
    )


def test_inventory_search_filter_sort_page_and_summaries(client):
    _large_inventory(client)
    from na_sso.db import get_session

    with get_session() as db:
        first = query_inventory(db, InventoryParams())
        assert first.total == 31 and first.pages == 2
        assert len(first.items) == 25 and first.has_next and not first.has_previous

        second = query_inventory(db, InventoryParams(page=2))
        assert len(second.items) == 6 and second.has_previous and not second.has_next

        search = query_inventory(db, InventoryParams(search="person 07"))
        assert [item.user.username for item in search.items] == ["user22"]

        disabled = query_inventory(db, InventoryParams(lifecycle="disabled"))
        assert [item.user.username for item in disabled.items] == ["user00"]
        assert disabled.items[0].summary.lifecycle == "disabled"

        deleted = query_inventory(db, InventoryParams(lifecycle="deleted"))
        assert [item.user.username for item in deleted.items] == ["user02"]

        cloud = query_inventory(db, InventoryParams(target="cloud", per_page=100))
        assert len(cloud.items) == 15
        assert all(item.summary.assigned_targets == 1 for item in cloud.items)

        attention = query_inventory(db, InventoryParams(issues="attention"))
        assert {item.user.username for item in attention.items} == {"user04", "user05"}
        retrying = query_inventory(db, InventoryParams(issues="retrying"))
        assert [item.user.username for item in retrying.items] == ["user05"]
        assert retrying.items[0].summary.issue_count == 1
        assert retrying.items[0].summary.healthy_targets == 0

        descending = query_inventory(
            db, InventoryParams(sort="username", direction="desc", per_page=100)
        )
        assert descending.items[0].user.username == "user29"


def test_people_first_inventory_and_detail_surfaces(admin_client, monkeypatch):
    from types import SimpleNamespace
    from na_sso.db import get_session

    target = SimpleNamespace(
        target_id="cloud", target_type="nextcloud", display_name="Cloud access"
    )
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [target])
    with get_session() as db:
        user = ManagedUser(
            username="detailuser", display_name="Detail User",
            email="detail@example.test", password_decision_required=True,
            password_decision_kind="initial",
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud",
            assigned=True, state="chpw",
            detail="password change required before propagation",
        ))
        db.commit()
        user_id = user.id

    page = admin_client.get("/users?q=detail&sort=issues&direction=desc")
    assert 'aria-label="Filter users"' in page.text
    assert "Target coverage" in page.text and "Issues" in page.text
    assert 'class="inventory-cards stack-2" role="list"' in page.text
    assert 'href="/users/' + str(user_id) + '"' in page.text
    assert "Cloud access" not in page.text.split('<div class="table-wrap inventory-desktop">', 1)[1]

    detail = admin_client.get(f"/users/{user_id}")
    assert detail.status_code == 200
    assert "Assignments and state" in detail.text
    assert "Cloud access" in detail.text
    assert "Password change required" in detail.text
    assert f'href="/users/{user_id}/edit"' in detail.text

    edit = admin_client.get(f"/users/{user_id}/edit")
    assert edit.status_code == 200
    assert "Edit account" in edit.text


def test_bulk_preview_partial_outcome_and_replay_are_safe(admin_client, monkeypatch):
    from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
    from na_sso.db import get_session
    from na_sso.models import AuditEvent, LifecycleOperation

    class BulkConnector(Connector):
        capabilities = IdentityCapabilities(password=False)

        def __init__(self):
            self.name = "cloud"
            self.display_name = "Cloud access"
            self.target_type = "nextcloud"

        async def ensure_user(self, user, password):
            return SyncResult(True, "saved")

        async def disable_user(self, user):
            return SyncResult(True, "disabled")

        async def delete_user(self, user):
            return SyncResult(True, "deleted")

        async def probe(self):
            return SyncResult(True, "reachable")

    connector = BulkConnector()
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    with get_session() as db:
        user = ManagedUser(username="bulkuser", display_name="Bulk User")
        db.add(user)
        db.commit()
        user_id = user.id

    preview = admin_client.post("/users/bulk/preview", data={
        "user_ids": [user_id, 0], "action": "assign", "target_id": "cloud",
    })
    assert preview.status_code == 200
    assert "No changes have been made" in preview.text
    assert "missing or protected selection" in preview.text
    token = re.search(r'name="replay_token" value="([^"]+)"', preview.text).group(1)
    with get_session() as db:
        assert db.get(ManagedUser, user_id).sync_states == []

    payload = {
        "user_ids": [user_id, 0], "action": "assign", "target_id": "cloud",
        "replay_token": token,
    }
    result = admin_client.post("/users/bulk/execute", data=payload, follow_redirects=False)
    assert result.status_code == 303
    notice = admin_client.get("/users")
    assert "Bulk action partially complete" in notice.text
    assert "1 account(s) accepted; 1 failed validation" in notice.text
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert len(user.sync_states) == 1 and user.sync_states[0].target == "cloud"
        parents = db.query(LifecycleOperation).filter_by(command="bulk").all()
        assert len(parents) == 1 and parents[0].status == "partially_failed"
        assert parents[0].completed_targets == 1 and parents[0].failed_targets == 1
        assert db.query(AuditEvent).filter_by(
            action="bulk.assign", operation_id=parents[0].id
        ).count() == 1
        correlation = parents[0].id[:8]

    replay = admin_client.post("/users/bulk/execute", data=payload, follow_redirects=False)
    assert replay.status_code == 303
    replay_notice = admin_client.get("/users")
    assert "Bulk action already processed" in replay_notice.text
    assert correlation in replay_notice.text
    with get_session() as db:
        assert db.query(LifecycleOperation).filter_by(command="bulk").count() == 1
        assert db.query(AuditEvent).filter_by(action="bulk.assign").count() == 1


def test_bulk_preview_warns_only_when_selected_operation_is_unsupported(
    admin_client, monkeypatch,
):
    from types import SimpleNamespace

    from na_sso.db import get_session

    connector = SimpleNamespace(
        target_id="cloud",
        target_type="nextcloud",
        display_name="Cloud access",
        ensure_supported=True,
        disable_supported=False,
        delete_supported=True,
    )
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    with get_session() as db:
        user = ManagedUser(username="bulk-warning", display_name="Bulk Warning")
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user,
            target="cloud",
            target_type="nextcloud",
            assigned=True,
            state="ok",
        ))
        db.commit()
        user_id = user.id

    unsupported = admin_client.post("/users/bulk/preview", data={
        "user_ids": user_id,
        "action": "unassign",
        "target_id": "cloud",
    })
    assert unsupported.status_code == 200
    assert "This target cannot disable accounts" in unsupported.text

    connector.disable_supported = True
    supported = admin_client.post("/users/bulk/preview", data={
        "user_ids": user_id,
        "action": "unassign",
        "target_id": "cloud",
    })
    assert supported.status_code == 200
    assert "This target cannot disable accounts" not in supported.text


def test_bulk_offboard_disable_and_retry_actions_execute(admin_client, monkeypatch):
    from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
    from na_sso.db import get_session

    class BulkConnector(Connector):
        capabilities = IdentityCapabilities(password=False)

        def __init__(self):
            self.name = "cloud"
            self.display_name = "Cloud access"
            self.target_type = "nextcloud"

        async def ensure_user(self, user, password): return SyncResult(True, "saved")
        async def disable_user(self, user): return SyncResult(True, "disabled")
        async def delete_user(self, user): return SyncResult(True, "deleted")
        async def probe(self): return SyncResult(True, "reachable")

    connector = BulkConnector()
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])
    with get_session() as db:
        user = ManagedUser(username="bulkflows", display_name="Bulk Flows")
        db.add(user)
        db.flush()
        db.add(SyncState(user=user, target="cloud", target_type="nextcloud", assigned=True, state="ok"))
        db.commit()
        user_id = user.id

    def execute(action, target_id=""):
        preview = admin_client.post("/users/bulk/preview", data={
            "user_ids": user_id, "action": action, "target_id": target_id,
        })
        token = re.search(r'name="replay_token" value="([^"]+)"', preview.text).group(1)
        return admin_client.post("/users/bulk/execute", data={
            "user_ids": user_id, "action": action, "target_id": target_id,
            "replay_token": token,
        }, follow_redirects=False)

    assert execute("unassign", "cloud").status_code == 303
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert not state.assigned and state.state == "unassigned"

    assert execute("assign", "cloud").status_code == 303
    with get_session() as db:
        state = db.get(ManagedUser, user_id).sync_states[0]
        assert state.assigned and state.state == "ok"
        state.state = "failed"
        state.detail = "offline"
        db.commit()

    assert execute("retry").status_code == 303
    with get_session() as db:
        assert db.get(ManagedUser, user_id).sync_states[0].state == "ok"

    assert execute("disable").status_code == 303
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.status == "disabled"
        assert user.sync_states[0].state == "ok"
