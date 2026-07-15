import sqlite3


def _legacy_db(path, targets=("opnsense",)):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE managed_users (id INTEGER PRIMARY KEY, username VARCHAR(64), display_name VARCHAR(128), email VARCHAR(254), status VARCHAR(16), pending_secret TEXT, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE sync_states (id INTEGER PRIMARY KEY, user_id INTEGER, target VARCHAR(32), state VARCHAR(16), detail TEXT, updated_at DATETIME);
        INSERT INTO managed_users VALUES (1, 'legacy', '', '', 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """)
        for index, target in enumerate(targets, 1):
            connection.execute("INSERT INTO sync_states VALUES (?, 1, ?, 'ok', '', CURRENT_TIMESTAMP)", (index, target))


def test_legacy_database_gets_retry_and_soft_delete_columns(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE managed_users (id INTEGER PRIMARY KEY, username VARCHAR(64), display_name VARCHAR(128), email VARCHAR(254), status VARCHAR(16), pending_secret TEXT, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE sync_states (id INTEGER PRIMARY KEY, user_id INTEGER, target VARCHAR(32), state VARCHAR(16), detail TEXT, updated_at DATETIME);
        INSERT INTO managed_users VALUES (1, 'legacy', '', '', 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """)
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(path))
    import na_sso.config as config
    import na_sso.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    db.init_db()
    db.init_db()
    with sqlite3.connect(path) as connection:
        managed = {row[1] for row in connection.execute("PRAGMA table_info(managed_users)")}
        states = {row[1] for row in connection.execute("PRAGMA table_info(sync_states)")}
        audit = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)")}
        credentials = {row[1] for row in connection.execute("PRAGMA table_info(target_credentials)")}
        operations = {row[1] for row in connection.execute("PRAGMA table_info(lifecycle_operations)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "desired_action", "deletion_requested_at", "deleted_at",
            "active_operation_id", "password_keep_until", "password_keep_count",
            "last_authenticated_at",
        } <= managed
        assert {"attempt_count", "next_retry_at", "operation_id"} <= states
        assert "operation_id" in audit
        assert {
            "last_checked_at", "last_success_at", "last_probe_ok",
            "probe_failure_kind", "probe_attempt_count", "next_probe_at",
        } <= credentials
        assert "parent_id" in operations
        assert {
            "lifecycle_operations", "operation_target_attempts", "admin_mfa",
            "webauthn_credentials", "webhook_endpoint_states", "webhook_deliveries",
            "reconciliation_runs", "reconciliation_findings",
            "bulk_workflows", "bulk_workflow_rows",
            "assignment_profiles", "assignment_profile_targets",
            "user_assignment_profiles", "user_assignment_exceptions",
            "profile_applications",
            "account_lifecycle_policies", "access_reviews", "access_review_items",
            "api_idempotency_records",
            "service_accounts", "service_account_credentials",
            "user_ssh_keys", "unmanaged_account_findings",
        } <= tables
        assert connection.execute("SELECT username FROM managed_users").fetchone() == ("legacy",)
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()


def test_legacy_target_maps_only_to_one_matching_instance(tmp_path, monkeypatch):
    path = tmp_path / "legacy-target.db"
    config_path = tmp_path / "na-sso.yaml"
    _legacy_db(path)
    config_path.write_text("""targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw, api_key: key, api_secret: secret}
""")
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(path))
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    import na_sso.config as config
    import na_sso.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    db.init_db()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT target, target_type, assigned, retired FROM sync_states").fetchone() == ("firewall", "opnsense", 1, 0)
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()


def test_legacy_target_with_multiple_matches_is_retired(tmp_path, monkeypatch):
    path = tmp_path / "ambiguous-target.db"
    config_path = tmp_path / "na-sso.yaml"
    _legacy_db(path)
    config_path.write_text("""targets:
  - {id: fw_a, type: opnsense, display_name: A, base_url: https://a, api_key: key, api_secret: secret}
  - {id: fw_b, type: opnsense, display_name: B, base_url: https://b, api_key: key, api_secret: secret}
""")
    monkeypatch.setenv("NA_SSO_DATABASE_PATH", str(path))
    monkeypatch.setenv("NA_SSO_CONFIG_FILE", str(config_path))
    import na_sso.config as config
    import na_sso.db as db
    config.get_settings.cache_clear()
    db._engine = db._session_factory = None
    db.init_db()
    with sqlite3.connect(path) as connection:
        target, assigned, retired, state = connection.execute("SELECT target, assigned, retired, state FROM sync_states").fetchone()
        assert (target, assigned, retired, state) == ("opnsense", 0, 1, "retired")
    db._engine = db._session_factory = None
    config.get_settings.cache_clear()
