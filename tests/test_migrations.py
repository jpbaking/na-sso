import sqlite3


def test_legacy_database_gets_retry_and_soft_delete_columns(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE managed_users (id INTEGER PRIMARY KEY, username VARCHAR(64), display_name VARCHAR(128), email VARCHAR(254), status VARCHAR(16), pending_secret TEXT, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE sync_states (id INTEGER PRIMARY KEY, user_id INTEGER, target VARCHAR(32), state VARCHAR(16), detail TEXT, updated_at DATETIME);
        INSERT INTO managed_users VALUES (1, 'legacy', '', '', 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """)
    monkeypatch.setenv("ONEAUTH_DATABASE_PATH", str(path))
    import oneauth.config as config
    import oneauth.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    db.init_db()
    db.init_db()
    with sqlite3.connect(path) as connection:
        managed = {row[1] for row in connection.execute("PRAGMA table_info(managed_users)")}
        states = {row[1] for row in connection.execute("PRAGMA table_info(sync_states)")}
        assert {"desired_action", "deletion_requested_at", "deleted_at"} <= managed
        assert {"attempt_count", "next_retry_at"} <= states
        assert connection.execute("SELECT username FROM managed_users").fetchone() == ("legacy",)
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()
