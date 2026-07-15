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
            "deletion_requested_at": "DATETIME",
            "deleted_at": "DATETIME",
            "password_hash": "VARCHAR(128)",
            "role": "VARCHAR(16) NOT NULL DEFAULT 'user'",
            "password_decision_required": "BOOLEAN NOT NULL DEFAULT 0",
            "password_changed_at": "DATETIME",
            "session_version": "INTEGER NOT NULL DEFAULT 1",
            "ssh_public_key": "TEXT",
        },
        "sync_states": {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "DATETIME",
            "target_type": "VARCHAR(32)",
            "assigned": "BOOLEAN NOT NULL DEFAULT 1",
            "retired": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    with get_engine().begin() as connection:
        for table, columns in upgrades.items():
            existing = {item["name"] for item in inspect(connection).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        _migrate_legacy_targets(connection)


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
