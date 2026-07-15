from datetime import timedelta
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _public_key() -> str:
    return ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()


def _login_user(admin_client, username: str = "keyowner") -> str:
    password = "V4lid!Comet-Bridge-2026"
    admin_client.post("/users/new", data={
        "username": username, "display_name": "Key Owner",
        "email": f"{username}@example.test", "password": password,
        "confirm_password": password,
    })
    admin_client.post("/logout")
    admin_client.post("/login", data={"username": username, "password": password})
    replacement = "V4lid!Meteor-Orbit-2027"
    admin_client.post("/account/password-decision", data={
        "choice": "change", "current_password": password,
        "new_password": replacement, "confirm_password": replacement,
    })
    admin_client.post("/login", data={"username": username, "password": replacement})
    return replacement


def test_named_keys_can_coexist_and_one_key_can_be_revoked(admin_client):
    _login_user(admin_client)
    first = admin_client.post("/account/ssh-key", data={
        "name": "Work laptop", "public_key": _public_key(), "expires_on": "2027-07-15",
    }, follow_redirects=False)
    second = admin_client.post("/account/ssh-key", data={
        "name": "Home desktop", "public_key": _public_key(), "expires_on": "2027-08-15",
    }, follow_redirects=False)
    assert first.status_code == second.status_code == 303

    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="keyowner").one()
        assert {key.name for key in user.active_ssh_keys} == {"Work laptop", "Home desktop"}
        revoked_id = next(key.id for key in user.active_ssh_keys if key.name == "Work laptop")
    page = admin_client.get("/account")
    assert "2 enrolled" in page.text
    assert "Last used: not reported by target" in page.text

    response = admin_client.post(f"/account/ssh-key/{revoked_id}/revoke", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="keyowner").one()
        assert [key.name for key in user.active_ssh_keys] == ["Home desktop"]


def test_rotation_revokes_old_key_only_after_new_key_sync_succeeds(admin_client, monkeypatch):
    _login_user(admin_client, "rotator")
    admin_client.post("/account/ssh-key", data={"name": "Old", "public_key": _public_key()})
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="rotator").one()
        old_id = user.active_ssh_keys[0].id
    monkeypatch.setattr("na_sso.sync.sync_user", AsyncMock(return_value="missing-operation"))

    response = admin_client.post("/account/ssh-key", data={
        "name": "New", "public_key": _public_key(), "replace_key_id": old_id,
    }, follow_redirects=False)

    assert response.status_code == 303
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="rotator").one()
        assert {key.name for key in user.active_ssh_keys} == {"Old", "New"}


def test_emergency_revoke_requires_password_and_removes_every_key(admin_client):
    password = _login_user(admin_client, "emergency")
    for name in ("Laptop", "Recovery"):
        admin_client.post("/account/ssh-key", data={"name": name, "public_key": _public_key()})
    rejected = admin_client.post("/account/ssh-keys/emergency-revoke", data={
        "current_password": "wrong", "confirmation": "REVOKE ALL KEYS",
    })
    assert rejected.status_code == 422
    accepted = admin_client.post("/account/ssh-keys/emergency-revoke", data={
        "current_password": password, "confirmation": "REVOKE ALL KEYS",
    }, follow_redirects=False)
    assert accepted.status_code == 303
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="emergency").one()
        assert user.active_ssh_keys == []
        assert user.ssh_public_key is None


async def test_expired_keys_are_revoked_and_synchronized(admin_client, monkeypatch):
    _login_user(admin_client, "expiring")
    admin_client.post("/account/ssh-key", data={"name": "Old", "public_key": _public_key()})
    from na_sso.db import get_session
    from na_sso.models import ManagedUser, utcnow
    with get_session() as db:
        user = db.query(ManagedUser).filter_by(username="expiring").one()
        user.active_ssh_keys[0].expires_at = utcnow() - timedelta(seconds=1)
        user_id = user.id
        db.commit()
    sync = AsyncMock()
    monkeypatch.setattr("na_sso.sync.sync_user", sync)
    from na_sso.ssh_keys import expire_due_ssh_keys

    assert await expire_due_ssh_keys() == 1
    sync.assert_awaited_once_with(user_id, actor="ssh-key-expiry")
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        assert user.active_ssh_keys == []
        assert user.ssh_public_key is None
