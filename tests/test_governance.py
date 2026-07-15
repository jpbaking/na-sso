from datetime import datetime, timedelta, timezone
import re

from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
from na_sso.models import ManagedUser, SyncState
from na_sso.security import hash_password


PASSWORD = "V4lid!Governance-2026"


class GovernanceConnector(Connector):
    target_id = "cloud"
    target_type = "nextcloud"
    display_name = "Cloud"
    capabilities = IdentityCapabilities(password=False)

    def __init__(self):
        self.ensured = []
        self.disabled = []
        self.deleted = []

    async def ensure_user(self, user, password):
        self.ensured.append(user.username)
        return SyncResult(True, "saved")

    async def disable_user(self, user):
        self.disabled.append(user.username)
        return SyncResult(True, "disabled")

    async def delete_user(self, user):
        self.deleted.append(user.username)
        return SyncResult(True, "deleted")

    async def probe(self):
        return SyncResult(True, "reachable")


def _install(monkeypatch, connector):
    monkeypatch.setattr("na_sso.connectors.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.users.get_connectors", lambda: [connector])
    monkeypatch.setattr("na_sso.sync.get_connectors", lambda: [connector])


def _governed_user(username="governed_user"):
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username=username, display_name="Governed User",
            email=f"{username}@example.test", password_hash=hash_password(PASSWORD),
            password_changed_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="cloud", target_type="nextcloud",
            assigned=True, state="ok",
        ))
        db.commit()
        return user.id


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M")


def test_lifecycle_policy_tracks_owner_timing_inactivity_and_scheduled_actions(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.governance import apply_lifecycle_automation
    from na_sso.models import AccessReview, AccountLifecyclePolicy, AuditEvent

    connector = GovernanceConnector()
    _install(monkeypatch, connector)
    user_id = _governed_user()
    now = datetime.now(timezone.utc)
    future_start = now + timedelta(days=1)
    future_end = now + timedelta(days=10)

    saved = admin_client.post(f"/users/{user_id}/lifecycle-policy", data={
        "owner": "Platform team", "reason": "Temporary migration support",
        "starts_at": _iso(future_start), "ends_at": _iso(future_end),
        "temporary": "yes", "inactivity_review_days": 30,
        "end_action": "disable",
    }, follow_redirects=False)
    assert saved.status_code == 303 and connector.disabled == ["governed_user"]
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        policy = db.get(AccountLifecyclePolicy, user_id)
        assert user.status == "disabled"
        assert policy.owner == "Platform team" and policy.temporary
        assert policy.reason == "Temporary migration support"
        assert policy.next_review_at is not None and policy.start_applied_at is None
        policy.starts_at = now - timedelta(minutes=1)
        policy.ends_at = now + timedelta(days=1)
        db.commit()

    import asyncio
    assert asyncio.run(apply_lifecycle_automation()) == 1
    assert connector.ensured == ["governed_user"]
    with get_session() as db:
        assert db.get(ManagedUser, user_id).status == "active"
        policy = db.get(AccountLifecyclePolicy, user_id)
        policy.ends_at = now - timedelta(minutes=1)
        policy.end_applied_at = None
        policy.next_review_at = now - timedelta(minutes=1)
        db.commit()

    processed = asyncio.run(apply_lifecycle_automation())
    assert processed == 2
    assert connector.disabled == ["governed_user", "governed_user"]
    with get_session() as db:
        policy = db.get(AccountLifecyclePolicy, user_id)
        assert policy.end_applied_at is not None and policy.next_review_at is None
        review = db.query(AccessReview).filter_by(source="inactivity").one()
        assert review.status == "open" and review.items[0].owner == "Platform team"
        assert db.query(AuditEvent).filter_by(action="access_review.inactivity_opened").count() == 1


def test_access_review_preview_reminder_attestation_and_destructive_confirmation(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import (
        AccessReview, AccessReviewItem, AccountLifecyclePolicy, AuditEvent,
    )

    connector = GovernanceConnector()
    _install(monkeypatch, connector)
    retain_id = _governed_user("retain_user")
    delete_id = _governed_user("delete_user")
    with get_session() as db:
        db.add_all([
            AccountLifecyclePolicy(
                user_id=retain_id, owner="Owner A", reason="Operational need",
                inactivity_review_days=45, updated_by="admin",
            ),
            AccountLifecyclePolicy(
                user_id=delete_id, owner="Owner B", reason="Expired project",
                updated_by="admin",
            ),
        ])
        db.commit()

    due = datetime.now(timezone.utc) + timedelta(days=7)
    preview = admin_client.post("/access-reviews/preview", data={
        "name": "Quarterly access review", "due_at": _iso(due),
        "user_ids": [retain_id, delete_id],
    }, follow_redirects=False)
    assert preview.status_code == 303
    page = admin_client.get(preview.headers["location"])
    assert "No decisions available yet" in page.text
    assert "Owner A" in page.text and "Expired project" in page.text
    token = re.search(r'name="approval_token" value="([^"]+)"', page.text).group(1)
    with get_session() as db:
        review = db.query(AccessReview).filter_by(source="manual").one()
        assert review.status == "draft" and len(review.items) == 2
        review_id = review.id
        retain_item = db.query(AccessReviewItem).filter_by(
            review_id=review.id, user_id=retain_id
        ).one()
        delete_item = db.query(AccessReviewItem).filter_by(
            review_id=review.id, user_id=delete_id
        ).one()

    opened = admin_client.post(
        f"/access-reviews/{review_id}/open",
        data={"approval_token": token}, follow_redirects=False,
    )
    assert opened.status_code == 303
    reminded = admin_client.post(
        f"/access-reviews/{review_id}/remind", follow_redirects=False
    )
    assert reminded.status_code == 303
    with get_session() as db:
        assert all(item.reminded_at for item in db.get(AccessReview, review_id).items)
        assert db.query(AuditEvent).filter_by(action="access_review.reminded").count() == 2

    retained = admin_client.post(
        f"/access-reviews/{review_id}/items/{retain_item.id}",
        data={"decision": "retain", "attestation": "Owner confirmed continued operational need."},
        follow_redirects=False,
    )
    assert retained.status_code == 303
    rejected_delete = admin_client.post(
        f"/access-reviews/{review_id}/items/{delete_item.id}",
        data={"decision": "delete", "attestation": "Owner confirmed the project has ended."},
        follow_redirects=False,
    )
    assert rejected_delete.status_code == 303 and connector.deleted == []
    with get_session() as db:
        assert db.get(AccessReviewItem, delete_item.id).decision == "pending"

    deleted = admin_client.post(
        f"/access-reviews/{review_id}/items/{delete_item.id}",
        data={
            "decision": "delete", "attestation": "Owner confirmed the project has ended.",
            "confirm_delete": "yes",
        }, follow_redirects=False,
    )
    assert deleted.status_code == 303 and connector.deleted == ["delete_user"]
    with get_session() as db:
        review = db.get(AccessReview, review_id)
        assert review.status == "completed"
        retain_policy = db.get(AccountLifecyclePolicy, retain_id)
        assert retain_policy.last_reviewed_at and retain_policy.next_review_at
        delete_user = db.get(ManagedUser, delete_id)
        assert delete_user.desired_action == "delete" and delete_user.deleted_at is not None
        assert db.get(AccessReviewItem, delete_item.id).operation_id
        assert db.query(AuditEvent).filter_by(action="access_review.retain").count() == 1
        assert db.query(AuditEvent).filter_by(action="access_review.delete").count() == 1


def test_successful_login_refreshes_activity_timestamp(client):
    from na_sso.db import get_session

    user_id = _governed_user("activity_user")
    response = client.post(
        "/login", data={"username": "activity_user", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with get_session() as db:
        assert db.get(ManagedUser, user_id).last_authenticated_at is not None
