from datetime import timedelta


def _session():
    from na_sso.db import get_session
    return get_session()


def _seed(db):
    from na_sso.lifecycle import OperationStatus, SyncStateValue
    from na_sso.models import (
        AuditEvent,
        LifecycleOperation,
        ManagedUser,
        ReconciliationFinding,
        ReconciliationRun,
        SyncState,
        UnmanagedAccountFinding,
        UserSshKey,
        WebhookDelivery,
        utcnow,
    )

    now = utcnow()
    alice = ManagedUser(username="alice", status="active", password_changed_at=now - timedelta(days=80))
    bob = ManagedUser(username="bob", status="disabled")
    db.add_all([alice, bob])
    db.flush()

    db.add_all([
        SyncState(user_id=alice.id, target="gitea", state=SyncStateValue.OK.value),
        SyncState(user_id=alice.id, target="jenkins", state=SyncStateValue.FAILED.value),
        SyncState(user_id=bob.id, target="gitea", state=SyncStateValue.PENDING.value),
        SyncState(user_id=bob.id, target="jenkins", state=SyncStateValue.OK.value, retired=True),
    ])
    db.add_all([
        LifecycleOperation(command="create", status=OperationStatus.SUCCEEDED.value,
                           actor="admin", subject="alice"),
        LifecycleOperation(command="disable", status=OperationStatus.FAILED.value,
                           actor="admin", subject="bob"),
    ])
    db.add(UserSshKey(user_id=alice.id, name="laptop", public_key="ssh-ed25519 AAA",
                      fingerprint="SHA256:x", algorithm="ssh-ed25519",
                      expires_at=now + timedelta(days=10)))

    run = ReconciliationRun(status="in_sync", actor="admin", completed_at=now)
    db.add(run)
    db.flush()
    db.add_all([
        ReconciliationFinding(run_id=run.id, user_id=alice.id, username="alice",
                              target_id="gitea", target_name="Gitea", field="memberships",
                              state="drift"),
        ReconciliationFinding(run_id=run.id, user_id=alice.id, username="alice",
                              target_id="gitea", target_name="Gitea", field="password",
                              state="match"),
    ])
    db.add(UnmanagedAccountFinding(target_id="gitea", target_type="gitea",
                                   username="ghost", decision="pending"))
    db.add_all([
        AuditEvent(actor="admin", action="user.create", subject="alice"),
        AuditEvent(actor="admin", action="user.disable", subject="bob"),
    ])
    db.add_all([
        WebhookDelivery(endpoint_id="ops", event_type="user.created", dedupe_key="1",
                        payload="{}", status="delivered"),
        WebhookDelivery(endpoint_id="ops", event_type="user.created", dedupe_key="2",
                        payload="{}", status="failed"),
    ])
    db.commit()


def test_eager_datasets_empty(client):
    from na_sso.dashboard import eager_datasets

    with _session() as db:
        data = eager_datasets(db, ["gitea", "jenkins"])
    assert data["tiles"]["users"] == {"total": 0, "active": 0, "disabled": 0,
                                      "spark": [0] * 30}
    assert data["tiles"]["targets"] == {"healthy": 0, "total": 2}
    assert data["tiles"]["findings"]["open"] == 0
    assert data["tiles"]["operations_24h"]["total"] == 0
    assert data["sync_health"]["labels"] == ["gitea", "jenkins"]
    assert all(v == 0 for series in data["sync_health"]["series"] for v in series["values"])
    assert len(data["operations_timeline"]["labels"]) == 14
    assert data["recon_findings"]["slices"] == []


def test_eager_datasets_seeded(client):
    from na_sso.dashboard import eager_datasets

    with _session() as db:
        _seed(db)
        data = eager_datasets(db, ["gitea", "jenkins"])

    users = data["tiles"]["users"]
    assert users["total"] == 2 and users["active"] == 1 and users["disabled"] == 1
    assert users["spark"][-1] == 2

    findings = data["tiles"]["findings"]
    assert findings == {"open": 2, "drift": 1, "unmanaged": 1}

    ops = data["tiles"]["operations_24h"]
    assert ops["total"] == 2 and ops["succeeded"] == 1 and ops["failed"] == 1

    sync = data["sync_health"]
    by_name = {series["name"]: series["values"] for series in sync["series"]}
    assert sync["labels"] == ["gitea", "jenkins"]
    assert by_name["In sync"] == [1, 0]
    assert by_name["Pending"] == [1, 0]
    assert by_name["Error"] == [0, 1]  # retired jenkins row excluded

    timeline = {series["name"]: sum(series["values"])
                for series in data["operations_timeline"]["series"]}
    assert timeline == {"Succeeded": 1, "Failed": 1}

    expiry = {series["name"]: series["values"] for series in data["expiry_horizon"]["series"]}
    assert expiry["SSH keys"] == [0, 1, 1]  # 10-day expiry: in ≤30 and ≤60
    # alice's password (changed 80 days ago, 90-day policy) expires within 30 days
    assert expiry["Passwords"][2] >= expiry["Passwords"][1] >= 0

    assert data["recon_findings"]["slices"] == [{"label": "Memberships", "value": 1}]


def test_insights_datasets(client):
    from na_sso.dashboard import insights_datasets

    with _session() as db:
        data = insights_datasets(db)
        assert data["lifecycle"]["slices"] == []
        assert data["access_review"] == {"open": False}

        _seed(db)
        data = insights_datasets(db)

    lifecycle = {s["label"]: s["value"] for s in data["lifecycle"]["slices"]}
    assert lifecycle == {"Active": 1, "Disabled": 1}

    audit = data["audit_timeline"]
    assert len(audit["labels"]) == 14
    assert sum(audit["series"][0]["values"]) == 2

    webhooks = data["webhooks"]
    assert webhooks["total"] == 2 and webhooks["success_rate"] == 50

    assert data["access_review"] == {"open": False}


def test_access_review_progress(client):
    from na_sso.dashboard import insights_datasets
    from na_sso.models import AccessReview, AccessReviewItem, ManagedUser, utcnow

    with _session() as db:
        user = ManagedUser(username="carol")
        db.add(user)
        db.flush()
        review = AccessReview(name="Q3 review", status="open",
                              due_at=utcnow() + timedelta(days=7), created_by="admin")
        db.add(review)
        db.flush()
        db.add_all([
            AccessReviewItem(review_id=review.id, user_id=user.id, username="carol",
                             decision="retain"),
            AccessReviewItem(review_id=review.id, user_id=0, username="admin",
                             decision="pending"),
        ])
        db.commit()
        data = insights_datasets(db)

    review_data = data["access_review"]
    assert review_data["open"] is True
    assert review_data["name"] == "Q3 review"
    assert review_data["decided"] == 1 and review_data["pending"] == 1 and review_data["total"] == 2


def test_dashboard_page_and_insights_routes(admin_client):
    r = admin_client.get("/dashboard")
    assert r.status_code == 200
    assert "Operations overview" in r.text
    assert "dashboard-data" in r.text

    r = admin_client.get("/dashboard/insights")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"lifecycle", "audit_timeline", "webhooks", "access_review"}


def test_dashboard_requires_login(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = client.get("/dashboard/insights", follow_redirects=False)
    assert r.status_code == 303
