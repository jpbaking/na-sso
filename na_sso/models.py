from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session

from na_sso.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminAccount(Base):
    __tablename__ = "admin_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManagedUser(Base):
    __tablename__ = "managed_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    password_hash: Mapped[str | None] = mapped_column(String(128), default=None)
    role: Mapped[str] = mapped_column(String(16), default="user")  # user|admin|root
    password_decision_required: Mapped[bool] = mapped_column(Boolean, default=False)
    password_decision_kind: Mapped[str] = mapped_column(String(16), default="")  # initial|reset|expired
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    ssh_public_key: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled
    desired_action: Mapped[str] = mapped_column(String(16), default="ensure")
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Fernet-encrypted password awaiting propagation; cleared once all targets sync.
    pending_secret: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sync_states: Mapped[list["SyncState"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_root(self) -> bool:
        return self.role == "root"

    @property
    def password_expires_at(self) -> datetime | None:
        if self.is_root or self.password_decision_kind in {"initial", "reset"} or self.password_changed_at is None:
            return None
        from na_sso.config import get_settings
        days = get_settings().file.password_policy.expires_after_days
        return self.password_changed_at + timedelta(days=days) if days is not None else None


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetCredential(Base):
    __tablename__ = "target_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    auth_mode: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    probe_detail: Mapped[str] = mapped_column(Text, default="Not tested")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


class SyncState(Base):
    __tablename__ = "sync_states"
    __table_args__ = (UniqueConstraint("user_id", "target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    target: Mapped[str] = mapped_column(String(64))  # stable configured target ID
    target_type: Mapped[str | None] = mapped_column(String(32), default=None)
    assigned: Mapped[bool] = mapped_column(Boolean, default=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[ManagedUser] = relationship(back_populates="sync_states")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


@event.listens_for(Session, "before_flush")
def enforce_root_invariants(session: Session, _flush_context, _instances) -> None:
    for item in session.deleted:
        if isinstance(item, ManagedUser) and item.role == "root":
            raise ValueError("root account cannot be deleted")
    for item in session.new.union(session.dirty):
        if isinstance(item, ManagedUser) and (item.role == "root" or item.id == 0):
            item.id = 0
            item.role = "root"
            item.status = "active"
            item.desired_action = "local_only"
            item.deletion_requested_at = None
            item.deleted_at = None
            item.pending_secret = None
            item.password_decision_required = False
            item.password_decision_kind = ""
            if item.sync_states:
                raise ValueError("root account cannot have target synchronization state")
