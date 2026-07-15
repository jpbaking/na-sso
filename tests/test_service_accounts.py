import re
from datetime import timedelta

from na_sso.permissions import MANAGE_USERS, VIEW_AUDIT


def _create_account(admin_client, *, name="deploy_bot", permissions=(MANAGE_USERS,)):
    response = admin_client.post(
        "/service-accounts",
        data={
            "name": name,
            "description": "Deployment automation",
            "permissions": list(permissions),
            "expires_at": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].split("?", 1)[0].rsplit("/", 1)[-1]


def _issue(admin_client, account_id, *, label="runner", days=30):
    response = admin_client.post(
        f"/service-accounts/{account_id}/credentials",
        data={"label": label, "expires_in_days": days},
    )
    assert response.status_code == 200
    token = re.search(r'nas_[a-f0-9]{12}_[A-Za-z0-9_-]{32,}', response.text).group(0)
    return response, token


def test_scoped_service_account_token_is_one_time_hashed_expiring_and_audited(
    admin_client,
):
    from na_sso.db import get_session
    from na_sso.models import AuditEvent, ServiceAccount, ServiceAccountCredential

    account_id = _create_account(admin_client)
    response, token = _issue(admin_client, account_id)
    assert response.headers["cache-control"] == "no-store"
    assert "Shown once" in response.text

    with get_session() as db:
        account = db.get(ServiceAccount, account_id)
        credential = db.query(ServiceAccountCredential).one()
        assert account.name == "deploy_bot"
        assert token not in credential.token_hash
        assert credential.expires_at is not None
        audit_text = " ".join(event.detail for event in db.query(AuditEvent).all())
        assert token not in audit_text

    detail = admin_client.get(f"/service-accounts/{account_id}")
    assert token not in detail.text
    assert "deploy_bot" in detail.text and "runner" in detail.text
    assert "Service accounts" in admin_client.get("/users").text

    bearer = {"Authorization": f"Bearer {token}"}
    allowed = admin_client.get("/api/v1/users", headers=bearer)
    assert allowed.status_code == 200
    assert allowed.json()["data"][0]["role"] == "root"
    forbidden = admin_client.get("/api/v1/audit", headers=bearer)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    identity = admin_client.get("/api/v1", headers=bearer).json()["data"]["principal"]
    assert identity == {
        "username": "service:deploy_bot", "type": "service_account",
        "role": "service_account",
    }
    with get_session() as db:
        assert db.query(ServiceAccountCredential).one().last_used_at is not None


def test_service_account_rotation_individual_revoke_account_revoke_and_expiry(
    admin_client,
):
    from na_sso.db import get_session
    from na_sso.models import ServiceAccountCredential, utcnow

    account_id = _create_account(
        admin_client, name="audit_export", permissions=(VIEW_AUDIT,),
    )
    _first_page, first = _issue(admin_client, account_id, label="old")
    _second_page, second = _issue(admin_client, account_id, label="replacement")
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {first}"}
    ).status_code == 200
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {second}"}
    ).status_code == 200

    with get_session() as db:
        credentials = db.query(ServiceAccountCredential).order_by(
            ServiceAccountCredential.created_at
        ).all()
        first_id, second_id = credentials[0].id, credentials[1].id
    admin_client.post(
        f"/service-accounts/{account_id}/credentials/{first_id}/revoke"
    )
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {first}"}
    ).status_code == 401
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {second}"}
    ).status_code == 200

    with get_session() as db:
        replacement = db.get(ServiceAccountCredential, second_id)
        replacement.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {second}"}
    ).status_code == 401

    _third_page, third = _issue(admin_client, account_id, label="third")
    admin_client.post(f"/service-accounts/{account_id}/revoke")
    assert admin_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {third}"}
    ).status_code == 401
    revoked = admin_client.get(f"/service-accounts/{account_id}")
    assert ">revoked<" in revoked.text and "Issue an expiring credential" not in revoked.text


def test_service_account_validation_and_token_lifetime_bounds(admin_client):
    rejected = admin_client.post(
        "/service-accounts",
        data={"name": "Bad Name", "permissions": MANAGE_USERS},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    page = admin_client.get(rejected.headers["location"])
    assert "Service account rejected" in page.text

    account_id = _create_account(admin_client, name="bounded_bot")
    too_long = admin_client.post(
        f"/service-accounts/{account_id}/credentials",
        data={"label": "runner", "expires_in_days": 366},
        follow_redirects=False,
    )
    assert too_long.status_code == 303
    assert "Credential not issued" in admin_client.get(too_long.headers["location"]).text
