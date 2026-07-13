def test_unauthenticated_redirects_to_login(client):
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_bad_login_rejected(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_user_crud_roundtrip(admin_client):
    c = admin_client
    r = c.post(
        "/users/new",
        data={"username": "jdoe", "display_name": "J. Doe", "email": "j@d.oe",
              "password": "Secret-123456789"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = c.get("/users")
    assert "jdoe" in r.text and "pending" in r.text

    r = c.post(
        "/users/1",
        data={"display_name": "Jane Doe", "email": "j@d.oe", "password": "",
              "status": "disabled"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = c.get("/users")
    assert "disabled" in r.text

    r = c.post("/users/1/delete", follow_redirects=False)
    assert r.status_code == 303
    r = c.get("/users")
    assert "jdoe" not in r.text


def test_duplicate_username_rejected(admin_client):
    c = admin_client
    data = {"username": "dup", "display_name": "", "email": "", "password": "x-123456789"}
    assert c.post("/users/new", data=data, follow_redirects=False).status_code == 303
    assert c.post("/users/new", data=data).status_code == 422


def test_password_never_plaintext_in_db(admin_client, tmp_path):
    admin_client.post(
        "/users/new",
        data={"username": "sec", "display_name": "", "email": "",
              "password": "SuperSecret-42x"},
    )
    blob = (tmp_path / "test.db").read_bytes()
    assert b"SuperSecret-42x" not in blob

    from oneauth.db import get_session
    from oneauth.models import ManagedUser
    from oneauth.security import decrypt_secret

    with get_session() as db:
        u = db.query(ManagedUser).filter(ManagedUser.username == "sec").one()
        assert decrypt_secret(u.pending_secret) == "SuperSecret-42x"
