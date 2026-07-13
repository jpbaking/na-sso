from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from oneauth.config import get_settings


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
    import oneauth.models  # noqa: F401  (register mappings)

    Base.metadata.create_all(get_engine())
    upgrades = {
        "managed_users": {
            "desired_action": "VARCHAR(16) NOT NULL DEFAULT 'ensure'",
            "deletion_requested_at": "DATETIME",
            "deleted_at": "DATETIME",
        },
        "sync_states": {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "DATETIME",
        },
    }
    with get_engine().begin() as connection:
        for table, columns in upgrades.items():
            existing = {item["name"] for item in inspect(connection).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def get_session() -> Session:
    get_engine()
    return _session_factory()
