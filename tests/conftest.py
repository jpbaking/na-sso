import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    os.environ["NA_SSO_DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["NA_SSO_SECRET_KEY"] = "test-secret"
    os.environ["NA_SSO_ADMIN_USERNAME"] = "admin"
    os.environ["NA_SSO_ADMIN_BOOTSTRAP_PASSWORD"] = "admin-pass"

    import na_sso.config as config
    import na_sso.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from fastapi.testclient import TestClient

    from na_sso.main import app

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
