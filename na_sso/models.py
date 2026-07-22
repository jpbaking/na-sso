from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session

from na_sso.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise SQLite's timezone-naive datetimes for safe comparisons."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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
    role: Mapped[str] = mapped_column(String(16), default="user")  # scoped role; see permissions.py
    password_decision_required: Mapped[bool] = mapped_column(Boolean, default=False)
    password_decision_kind: Mapped[str] = mapped_column(String(16), default="")  # initial|reset|expired
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    password_keep_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    password_keep_count: Mapped[int] = mapped_column(Integer, default=0)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    ssh_public_key: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled
    desired_action: Mapped[str] = mapped_column(String(16), default="ensure")
    active_operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
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
    ssh_keys: Mapped[list["UserSshKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def active_ssh_keys(self) -> list["UserSshKey"]:
        now = utcnow()
        return [
            key for key in self.ssh_keys
            if key.revoked_at is None and (key.expires_at is None or as_utc(key.expires_at) > now)
        ]

    @property
    def active_ssh_public_keys(self) -> list[str]:
        keys = [key.public_key for key in self.active_ssh_keys]
        return keys or ([self.ssh_public_key] if self.ssh_public_key else [])

    @property
    def is_root(self) -> bool:
        return self.role == "root"

    @property
    def password_expires_at(self) -> datetime | None:
        if self.is_root or self.password_decision_kind in {"initial", "reset"} or self.password_changed_at is None:
            return None
        from na_sso.config import get_settings
        days = get_settings().file.password_policy.expires_after_days
        changed_at = as_utc(self.password_changed_at)
        keep_until = as_utc(self.password_keep_until)
        expiry = changed_at + timedelta(days=days) if days is not None else None
        if keep_until and (expiry is None or keep_until > expiry):
            return keep_until
        return expiry


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSshKey(Base):
    __tablename__ = "user_ssh_keys"
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    public_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    algorithm: Mapped[str] = mapped_column(String(24))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[str | None] = mapped_column(String(64), default=None)
    replaced_by_id: Mapped[str | None] = mapped_column(String(36), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_source: Mapped[str] = mapped_column(String(40), default="not_reported")

    user: Mapped[ManagedUser] = relationship(back_populates="ssh_keys")


class TargetCredential(Base):
    __tablename__ = "target_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    auth_mode: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    probe_detail: Mapped[str] = mapped_column(Text, default="Not tested")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    probe_failure_kind: Mapped[str] = mapped_column(String(32), default="")
    probe_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


class TargetOpenvpnConfig(Base):
    __tablename__ = "target_openvpn_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    vpnid: Mapped[str] = mapped_column(String(64), default="")
    template: Mapped[str] = mapped_column(String(128), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    cert_lifetime_days: Mapped[int] = mapped_column(Integer, default=397)
    auth_posture: Mapped[str] = mapped_column(String(32), default="")
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    verify_detail: Mapped[str] = mapped_column(Text, default="Not verified")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


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
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
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
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)


class AdminMfa(Base):
    __tablename__ = "admin_mfa"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), unique=True, index=True)
    totp_secret: Mapped[str | None] = mapped_column(Text, default=None)
    totp_last_counter: Mapped[int] = mapped_column(Integer, default=-1)
    recovery_code_hashes: Mapped[str] = mapped_column(Text, default="[]")
    emergency_code_used_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    credential_id: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), default="Passkey")
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[str] = mapped_column(Text, default="[]")
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class WebhookEndpointState(Base):
    __tablename__ = "webhook_endpoint_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("endpoint_id", "dedupe_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    endpoint_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(256))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class LifecycleOperation(Base):
    __tablename__ = "lifecycle_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    command: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    actor: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128), index=True)
    requested_target: Mapped[str | None] = mapped_column(String(64), default=None)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), default=None)
    parent_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    completed_targets: Mapped[int] = mapped_column(Integer, default=0)
    failed_targets: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    attempts: Mapped[list["OperationTargetAttempt"]] = relationship(
        back_populates="operation", cascade="all, delete-orphan"
    )


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    approval_token: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    source: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(24), default="discovering", index=True)
    actor: Mapped[str] = mapped_column(String(64))
    scope_user_id: Mapped[int | None] = mapped_column(Integer, default=None)
    scope_target_id: Mapped[str | None] = mapped_column(String(64), default=None)
    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    drifted_targets: Mapped[int] = mapped_column(Integer, default=0)
    unknown_targets: Mapped[int] = mapped_column(Integer, default=0)
    destructive_targets: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    findings: Mapped[list["ReconciliationFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class UnmanagedAccountFinding(Base):
    __tablename__ = "unmanaged_account_findings"
    __table_args__ = (UniqueConstraint("target_id", "username"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    username: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    remote_status: Mapped[str] = mapped_column(String(24), default="unknown")
    remote_uid: Mapped[int | None] = mapped_column(Integer, default=None)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    decision: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decision_actor: Mapped[str | None] = mapped_column(String(64), default=None)
    removal_token: Mapped[str | None] = mapped_column(String(36), default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ReconciliationFinding(Base):
    __tablename__ = "reconciliation_findings"
    __table_args__ = (
        UniqueConstraint("run_id", "user_id", "target_id", "field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    target_name: Mapped[str] = mapped_column(String(128))
    field: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), index=True)
    desired: Mapped[str | None] = mapped_column(Text, default=None)
    actual: Mapped[str | None] = mapped_column(Text, default=None)
    detail: Mapped[str] = mapped_column(Text, default="")
    repair_status: Mapped[str] = mapped_column(String(24), default="not_requested")
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)

    run: Mapped[ReconciliationRun] = relationship(back_populates="findings")


class BulkWorkflow(Base):
    __tablename__ = "bulk_workflows"
    __table_args__ = (UniqueConstraint("actor", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16), default="csv")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="previewed", index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    rows: Mapped[list["BulkWorkflowRow"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class BulkWorkflowRow(Base):
    __tablename__ = "bulk_workflow_rows"
    __table_args__ = (UniqueConstraint("workflow_id", "row_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("bulk_workflows.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    username: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    target_ids: Mapped[str] = mapped_column(Text, default="[]")
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    validation_status: Mapped[str] = mapped_column(String(16), default="valid")
    result_status: Mapped[str] = mapped_column(String(24), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    encrypted_temporary_password: Mapped[str | None] = mapped_column(Text, default=None)

    workflow: Mapped[BulkWorkflow] = relationship(back_populates="rows")


class ApiIdempotencyRecord(Base):
    __tablename__ = "api_idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor", "method", "path", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer, default=None)
    response_body: Mapped[str | None] = mapped_column(Text, default=None)
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ServiceAccount(Base):
    __tablename__ = "service_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[str | None] = mapped_column(String(64), default=None)

    credentials: Mapped[list["ServiceAccountCredential"]] = relationship(
        back_populates="service_account", cascade="all, delete-orphan"
    )


class ServiceAccountCredential(Base):
    __tablename__ = "service_account_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    service_account_id: Mapped[str] = mapped_column(
        ForeignKey("service_accounts.id"), index=True
    )
    label: Mapped[str] = mapped_column(String(80))
    token_prefix: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[str | None] = mapped_column(String(64), default=None)

    service_account: Mapped[ServiceAccount] = relationship(back_populates="credentials")


class AssignmentProfile(Base):
    __tablename__ = "assignment_profiles"
    __table_args__ = (UniqueConstraint("profile_key", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    profile_key: Mapped[str] = mapped_column(String(36), index=True, default=lambda: str(uuid4()))
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    approval_token: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    targets: Mapped[list["AssignmentProfileTarget"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class AssignmentProfileTarget(Base):
    __tablename__ = "assignment_profile_targets"
    __table_args__ = (UniqueConstraint("profile_id", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("assignment_profiles.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    memberships: Mapped[str] = mapped_column(Text, default="[]")

    profile: Mapped[AssignmentProfile] = relationship(back_populates="targets")


class UserAssignmentProfile(Base):
    __tablename__ = "user_assignment_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("assignment_profiles.id"), index=True)
    assigned_by: Mapped[str] = mapped_column(String(64))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserAssignmentException(Base):
    __tablename__ = "user_assignment_exceptions"
    __table_args__ = (UniqueConstraint("user_id", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    assignment_mode: Mapped[str] = mapped_column(String(16), default="inherit")
    add_memberships: Mapped[str] = mapped_column(Text, default="[]")
    remove_memberships: Mapped[str] = mapped_column(Text, default="[]")
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProfileApplication(Base):
    __tablename__ = "profile_applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("assignment_profiles.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    approval_token: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    status: Mapped[str] = mapped_column(String(16), default="previewed", index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AccountLifecyclePolicy(Base):
    __tablename__ = "account_lifecycle_policies"

    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    inactivity_review_days: Mapped[int | None] = mapped_column(Integer, default=None)
    end_action: Mapped[str] = mapped_column(String(16), default="disable")
    start_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    end_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AccessReview(Base):
    __tablename__ = "access_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(24), default="manual", index=True)
    source_key: Mapped[str | None] = mapped_column(String(160), unique=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    approval_token: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    items: Mapped[list["AccessReviewItem"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class AccessReviewItem(Base):
    __tablename__ = "access_review_items"
    __table_args__ = (UniqueConstraint("review_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("access_reviews.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("managed_users.id"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attestation: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str | None] = mapped_column(String(64), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    operation_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)

    review: Mapped[AccessReview] = relationship(back_populates="items")


class OperationTargetAttempt(Base):
    __tablename__ = "operation_target_attempts"
    __table_args__ = (UniqueConstraint("operation_id", "target", "attempt_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("lifecycle_operations.id"), index=True)
    target: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32), default=None)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="running")
    result_state: Mapped[str] = mapped_column(String(32), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    operation: Mapped[LifecycleOperation] = relationship(back_populates="attempts")


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
            item.active_operation_id = None
            item.deletion_requested_at = None
            item.deleted_at = None
            item.pending_secret = None
            item.password_decision_required = False
            item.password_decision_kind = ""
            item.password_keep_until = None
            item.password_keep_count = 0
            if item.sync_states:
                raise ValueError("root account cannot have target synchronization state")
