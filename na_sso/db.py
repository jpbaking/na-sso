from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from na_sso.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        db_path = Path(get_settings().database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    import na_sso.models  # noqa: F401  (register mappings)

    Base.metadata.create_all(get_engine())
    upgrades = {
        "managed_users": {
            "desired_action": "VARCHAR(16) NOT NULL DEFAULT 'ensure'",
            "active_operation_id": "VARCHAR(36)",
            "deletion_requested_at": "DATETIME",
            "deleted_at": "DATETIME",
            "password_hash": "VARCHAR(128)",
            "role": "VARCHAR(16) NOT NULL DEFAULT 'user'",
            "password_decision_required": "BOOLEAN NOT NULL DEFAULT 0",
            "password_decision_kind": "VARCHAR(16) NOT NULL DEFAULT ''",
            "password_changed_at": "DATETIME",
            "last_authenticated_at": "DATETIME",
            "password_keep_until": "DATETIME",
            "password_keep_count": "INTEGER NOT NULL DEFAULT 0",
            "session_version": "INTEGER NOT NULL DEFAULT 1",
            "ssh_public_key": "TEXT",
        },
        "sync_states": {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "DATETIME",
            "target_type": "VARCHAR(32)",
            "assigned": "BOOLEAN NOT NULL DEFAULT 1",
            "retired": "BOOLEAN NOT NULL DEFAULT 0",
            "operation_id": "VARCHAR(36)",
        },
        "audit_events": {
            "operation_id": "VARCHAR(36)",
        },
        "target_credentials": {
            "last_checked_at": "DATETIME",
            "last_success_at": "DATETIME",
            "last_probe_ok": "BOOLEAN",
            "probe_failure_kind": "VARCHAR(32) NOT NULL DEFAULT ''",
            "probe_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_probe_at": "DATETIME",
        },
        "admin_mfa": {
            "totp_last_counter": "INTEGER NOT NULL DEFAULT -1",
        },
        "lifecycle_operations": {
            "parent_id": "VARCHAR(36)",
        },
    }
    with get_engine().begin() as connection:
        for table, columns in upgrades.items():
            existing = {item["name"] for item in inspect(connection).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        _migrate_legacy_targets(connection)
        connection.execute(text(
            "INSERT INTO user_ssh_keys "
            "(id, user_id, name, public_key, fingerprint, algorithm, enrolled_at, last_used_source) "
            "SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
            "substr(lower(hex(randomblob(2))),2) || '-a' || substr(lower(hex(randomblob(2))),2) || '-' || "
            "lower(hex(randomblob(6))), id, 'Migrated key', ssh_public_key, "
            "'legacy:' || id, CASE WHEN ssh_public_key LIKE 'ssh-rsa %' THEN 'rsa' ELSE 'ed25519' END, "
            "COALESCE(updated_at, created_at), 'not_reported' FROM managed_users u "
            "WHERE ssh_public_key IS NOT NULL AND ssh_public_key != '' AND NOT EXISTS "
            "(SELECT 1 FROM user_ssh_keys k WHERE k.user_id=u.id)"
        ))
        from na_sso.security import ssh_public_key_fingerprint
        legacy_keys = connection.execute(text(
            "SELECT id, public_key FROM user_ssh_keys WHERE fingerprint LIKE 'legacy:%'"
        )).mappings()
        for key in legacy_keys:
            fingerprint = ssh_public_key_fingerprint(key["public_key"])
            if fingerprint:
                connection.execute(
                    text("UPDATE user_ssh_keys SET fingerprint=:fingerprint WHERE id=:id"),
                    {"fingerprint": fingerprint, "id": key["id"]},
                )
        connection.execute(text(
            "UPDATE managed_users SET password_decision_kind='initial' "
            "WHERE password_decision_required=1 AND password_decision_kind=''"
        ))
        connection.execute(text(
            "UPDATE managed_users SET role='user_operator' WHERE role='admin'"
        ))
        connection.execute(text(
            "UPDATE managed_users SET pending_secret=NULL "
            "WHERE password_decision_kind IN ('initial', 'reset')"
        ))
        connection.execute(text(
            "UPDATE sync_states SET state='chpw', detail='password change required before propagation', "
            "attempt_count=0, next_retry_at=NULL WHERE assigned=1 AND retired=0 "
            "AND state!='pending_chpw_disable' AND user_id IN "
            "(SELECT id FROM managed_users WHERE password_decision_kind IN ('initial', 'reset'))"
        ))


def _migrate_legacy_targets(connection) -> None:
    """Map old type-keyed rows only when the configured target is unambiguous."""
    settings = get_settings()
    configured = list(settings.file.targets) if settings.config_file else []
    by_type: dict[str, list] = {}
    for target in configured:
        by_type.setdefault(target.type, []).append(target)
    rows = connection.execute(text("SELECT id, target, target_type FROM sync_states")).mappings()
    for row in rows:
        legacy_type = row["target_type"] or row["target"]
        matches = by_type.get(legacy_type, [])
        if row["target_type"] is None and len(matches) == 1:
            connection.execute(text("UPDATE sync_states SET target=:target, target_type=:type, retired=0 WHERE id=:id"),
                               {"target": matches[0].id, "type": legacy_type, "id": row["id"]})
        elif row["target_type"] is None:
            connection.execute(text("UPDATE sync_states SET target_type=:type, assigned=0, retired=1, state='retired', detail=:detail, next_retry_at=NULL WHERE id=:id"),
                               {"type": legacy_type, "detail": "legacy target mapping is ambiguous or unavailable", "id": row["id"]})
        elif not any(target.id == row["target"] for target in configured):
            connection.execute(text("UPDATE sync_states SET retired=1, state='retired', next_retry_at=NULL WHERE id=:id"), {"id": row["id"]})


def get_session() -> Session:
    get_engine()
    return _session_factory()
