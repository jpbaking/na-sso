import os
import socket
import threading
import time
from dataclasses import dataclass

import httpx
import pytest
import uvicorn


class _UvicornThread:
    def __init__(self, app, name: str):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._port = self._socket.getsockname()[1]
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._port,
                log_level="error",
                access_log=False,
            )
        )
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._socket]},
            name=name,
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and self._thread.is_alive():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if not self._server.started:
            self.stop()
            raise RuntimeError(f"{self._thread.name} did not start")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._socket.close()
        if self._thread.is_alive():
            raise RuntimeError(f"{self._thread.name} did not stop")


@dataclass(frozen=True)
class BrowserServers:
    app_url: str
    mock_url: str


@pytest.fixture(scope="session")
def browser_servers(tmp_path_factory) -> BrowserServers:
    """Serve isolated real-app and mock-target instances on loopback."""
    from na_sso.mock_targets.app import app as mock_target_app

    mock_server = _UvicornThread(mock_target_app, "browser-mock-targets")
    mock_server.start()

    database_path = tmp_path_factory.mktemp("browser") / "browser.db"
    environment = {
        "NA_SSO_DATABASE_PATH": str(database_path),
        "NA_SSO_SECRET_KEY": "browser-test-secret",
        "NA_SSO_ADMIN_USERNAME": "admin",
        "NA_SSO_ADMIN_BOOTSTRAP_PASSWORD": "admin-pass",
        "NA_SSO_OPNSENSE_ENABLED": "true",
        "NA_SSO_OPNSENSE_BASE_URL": mock_server.url,
        "NA_SSO_OPNSENSE_API_KEY": "demo-key",
        "NA_SSO_OPNSENSE_API_SECRET": "demo-secret",
        "NA_SSO_OPNSENSE_VERIFY_TLS": "false",
        "NA_SSO_NEXUS_ENABLED": "true",
        "NA_SSO_NEXUS_BASE_URL": mock_server.url,
        "NA_SSO_NEXUS_ADMIN_USER": "admin",
        "NA_SSO_NEXUS_ADMIN_PASSWORD": "demo-password",
        "NA_SSO_NEXTCLOUD_ENABLED": "true",
        "NA_SSO_NEXTCLOUD_BASE_URL": mock_server.url,
        "NA_SSO_NEXTCLOUD_ADMIN_USER": "admin",
        "NA_SSO_NEXTCLOUD_ADMIN_PASSWORD": "demo-password",
    }
    original_environment = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)

    app_server = None
    try:
        import na_sso.config as config
        import na_sso.db as db

        config.get_settings.cache_clear()
        from na_sso.api_contract import reset_api_rate_limits

        reset_api_rate_limits()
        db._engine = None
        db._session_factory = None

        with httpx.Client(trust_env=False) as client:
            client.post(f"{mock_server.url}/__mock__/reset").raise_for_status()

        from na_sso.main import app, bootstrap_admin

        # Match the unit fixture's explicit isolated persistence bootstrap; the
        # real ASGI lifespan repeats these idempotent steps before serving.
        db.init_db()
        bootstrap_admin()

        app_server = _UvicornThread(app, "browser-na-sso")
        app_server.start()
        yield BrowserServers(app_url=app_server.url, mock_url=mock_server.url)
    finally:
        if app_server is not None:
            app_server.stop()
        mock_server.stop()

        if "db" in locals() and db._engine is not None:
            db._engine.dispose()
            db._engine = None
            db._session_factory = None
        if "config" in locals():
            config.get_settings.cache_clear()

        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def live_server_url(browser_servers: BrowserServers) -> str:
    return browser_servers.app_url


@pytest.fixture(scope="session")
def mock_server_url(browser_servers: BrowserServers) -> str:
    return browser_servers.mock_url


@pytest.fixture
def modern_target_config(tmp_path, mock_server_url: str):
    """Temporarily expose one UI-managed mock target to the live app."""
    config_path = tmp_path / "browser-targets.yaml"
    config_path.write_text(
        "version: 1\n"
        "targets:\n"
        "  - id: browser-nexus\n"
        "    type: nexus\n"
        "    display_name: Browser Nexus\n"
        f"    base_url: {mock_server_url}\n"
        "    verify_tls: false\n"
    )
    key = "NA_SSO_CONFIG_FILE"
    original = os.environ.get(key)
    os.environ[key] = str(config_path)

    import na_sso.config as config

    config.get_settings.cache_clear()
    try:
        yield "browser-nexus"
    finally:
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
        config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_mock_targets(mock_server_url: str):
    def reset() -> None:
        with httpx.Client(trust_env=False) as client:
            client.post(f"{mock_server_url}/__mock__/reset").raise_for_status()

    reset()
    try:
        yield
    finally:
        reset()
