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
    from na_sso.api_contract import reset_api_rate_limits
    reset_api_rate_limits()
    db._engine = None
    db._session_factory = None

    from fastapi.testclient import TestClient

    from na_sso.main import app, bootstrap_admin

    # Unit/route tests initialise persistence explicitly and exercise workers
    # through their dedicated functions. Avoid long-lived background tasks in
    # the in-process client; Compose integration covers the application lifespan.
    db.init_db()
    bootstrap_admin()
    c = TestClient(app)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture()
def admin_client(client):
    r = client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return client
