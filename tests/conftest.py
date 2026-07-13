import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    os.environ["ONEAUTH_DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["ONEAUTH_SECRET_KEY"] = "test-secret"
    os.environ["ONEAUTH_ADMIN_USERNAME"] = "admin"
    os.environ["ONEAUTH_ADMIN_BOOTSTRAP_PASSWORD"] = "admin-pass"

    import oneauth.config as config
    import oneauth.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from fastapi.testclient import TestClient

    from oneauth.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client):
    r = client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return client
