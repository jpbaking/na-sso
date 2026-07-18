from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ENV_REF_RE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordPolicy(StrictModel):
    min_length: int = Field(default=14, ge=8, le=1024)
    max_length: int = Field(default=128, ge=8, le=4096)
    require_lowercase: bool = True
    require_uppercase: bool = True
    require_digit: bool = True
    require_symbol: bool = True
    max_repeated_characters: int | None = Field(default=3, ge=1, le=128)
    max_numeric_sequence: int | None = Field(default=3, ge=2, le=32)
    min_identity_distance: int | None = Field(default=4, ge=1, le=128)
    reject_identity_terms: bool = True
    history_size: int = Field(default=3, ge=0, le=100)
    expires_after_days: int | None = Field(default=90, ge=1, le=3650)
    expiry_acknowledgement_mode: Literal["disabled", "renewal", "grace"] = "grace"
    expiry_acknowledgement_grace_days: int = Field(default=14, ge=1, le=3650)
    expiry_acknowledgement_limit: int | None = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> "PasswordPolicy":
        if self.max_length < self.min_length:
            raise ValueError("password_policy.max_length must be >= min_length")
        if self.min_identity_distance and self.min_identity_distance > self.max_length:
            raise ValueError("min_identity_distance cannot exceed max_length")
        return self


class SshKeyPolicy(StrictModel):
    allowed_algorithms: list[Literal["ed25519", "rsa"]] = ["ed25519"]
    rsa_min_bits: int = Field(default=3072, ge=2048, le=16384)
    browser_generation: bool = True
    allow_server_fallback: bool = False
    default_expiry_days: int | None = Field(default=365, ge=1, le=3650)
    max_expiry_days: int | None = Field(default=730, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_algorithms(self) -> "SshKeyPolicy":
        if not self.allowed_algorithms or len(set(self.allowed_algorithms)) != len(self.allowed_algorithms):
            raise ValueError("allowed_algorithms must contain unique values")
        if self.default_expiry_days and self.max_expiry_days and self.default_expiry_days > self.max_expiry_days:
            raise ValueError("ssh_key_policy.default_expiry_days cannot exceed max_expiry_days")
        return self


class SupportPolicy(StrictModel):
    label: str = Field(default="Contact your NA-SSO administrator", min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=500)
    guidance: str = Field(
        default="Share your username and the affected target name; do not send passwords or private keys.",
        max_length=300,
    )

    @model_validator(mode="after")
    def validate_url(self) -> "SupportPolicy":
        if self.url and not re.match(r"^(?:https?://|mailto:)", self.url):
            raise ValueError("support_policy.url must use https, http, or mailto")
        return self


class AuditPolicy(StrictModel):
    retention_days: int | None = Field(default=365, ge=1, le=36500)
    export_page_size: int = Field(default=500, ge=25, le=5000)


class ReconciliationPolicy(StrictModel):
    enabled: bool = False
    interval_seconds: int = Field(default=3600, ge=60, le=2592000)
    scan_seconds: int = Field(default=60, ge=5, le=3600)
    retry_base_seconds: int = Field(default=60, ge=5, le=86400)
    retry_max_seconds: int = Field(default=3600, ge=5, le=604800)
    max_attempts: int = Field(default=5, ge=1, le=20)
    max_users_per_run: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_retries(self) -> "ReconciliationPolicy":
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("reconciliation_policy.retry_max_seconds must be >= retry_base_seconds")
        return self


class UnmanagedAccountPolicy(StrictModel):
    enabled: bool = True
    max_accounts_per_target: int = Field(default=1000, ge=1, le=10000)
    ssh_min_uid: int = Field(default=1000, ge=0, le=60000)
    excluded_usernames: list[str] = ["root", "nobody", "sync", "shutdown", "halt"]
    excluded_prefixes: list[str] = ["_", "systemd-", "svc-"]
    allow_removal: bool = False

    @model_validator(mode="after")
    def validate_exclusions(self) -> "UnmanagedAccountPolicy":
        if len(set(self.excluded_usernames)) != len(self.excluded_usernames):
            raise ValueError("unmanaged_account_policy.excluded_usernames must be unique")
        if len(set(self.excluded_prefixes)) != len(self.excluded_prefixes):
            raise ValueError("unmanaged_account_policy.excluded_prefixes must be unique")
        return self
class LifecycleAutomationPolicy(StrictModel):
    scan_seconds: int = Field(default=60, ge=5, le=3600)
    default_review_interval_days: int = Field(default=90, ge=1, le=3650)
    reminder_days_before_due: int = Field(default=7, ge=0, le=365)
    max_review_accounts: int = Field(default=1000, ge=1, le=5000)


class AutomationApiPolicy(StrictModel):
    enabled: bool = True
    requests_per_minute: int = Field(default=120, ge=10, le=10000)
    max_page_size: int = Field(default=100, ge=25, le=500)
    idempotency_retention_hours: int = Field(default=24, ge=1, le=720)
    default_token_days: int = Field(default=90, ge=1, le=3650)
    max_token_days: int = Field(default=365, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_token_lifetime(self) -> "AutomationApiPolicy":
        if self.max_token_days < self.default_token_days:
            raise ValueError("automation_api_policy.max_token_days must be >= default_token_days")
        return self


class AdminMfaPolicy(StrictModel):
    required: bool = False
    allowed_methods: list[Literal["webauthn", "totp"]] = ["webauthn", "totp"]
    issuer: str = Field(default="NA-SSO", min_length=1, max_length=64)
    rp_id: str | None = Field(default=None, max_length=253)
    expected_origin: str | None = Field(default=None, max_length=500)
    reauthentication_minutes: int = Field(default=10, ge=1, le=60)

    @model_validator(mode="after")
    def validate_methods(self) -> "AdminMfaPolicy":
        if not self.allowed_methods or len(set(self.allowed_methods)) != len(self.allowed_methods):
            raise ValueError("admin_mfa_policy.allowed_methods must contain unique methods")
        if self.expected_origin and not re.match(r"^https?://[^/]+$", self.expected_origin):
            raise ValueError("admin_mfa_policy.expected_origin must be an http(s) origin without a path")
        return self


NotificationEvent = Literal[
    "sync.persistent_failure",
    "password.expired",
    "lifecycle.completed",
    "approval.completed",
    "access_review.reminder",
]


class WebhookEndpoint(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    url: str = Field(max_length=1000)
    secret: SecretStr
    enabled: bool = True
    events: list[NotificationEvent]

    @model_validator(mode="after")
    def validate_endpoint(self) -> "WebhookEndpoint":
        parsed = urlparse(self.url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("webhook URL must use https (http is allowed only for localhost)")
        if not self.events or len(set(self.events)) != len(self.events):
            raise ValueError("webhook events must contain unique supported values")
        return self


class NotificationPolicy(StrictModel):
    enabled: bool = False
    persistent_failure_attempts: int = Field(default=3, ge=1, le=100)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_base_seconds: int = Field(default=30, ge=1, le=86400)
    retry_max_seconds: int = Field(default=3600, ge=1, le=604800)
    delivery_scan_seconds: int = Field(default=10, ge=1, le=3600)
    endpoints: list[WebhookEndpoint] = []

    @model_validator(mode="after")
    def validate_notifications(self) -> "NotificationPolicy":
        ids = [endpoint.id for endpoint in self.endpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("notification endpoint IDs must be unique")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("notification retry_max_seconds must be >= retry_base_seconds")
        return self


class TargetBase(StrictModel):
    id: str
    display_name: str = Field(min_length=1, max_length=128)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_id(self) -> "TargetBase":
        if not TARGET_ID_RE.fullmatch(self.id):
            raise ValueError("target id must match ^[a-z][a-z0-9_-]{1,63}$")
        return self


class OpnsenseTarget(TargetBase):
    type: Literal["opnsense"]
    base_url: str
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    verify_tls: bool = True
    default_groups: list[str] = []


class NexusTarget(TargetBase):
    type: Literal["nexus"]
    base_url: str
    admin_user: str | None = None
    admin_password: SecretStr | None = None
    default_roles: list[str] = ["nx-anonymous"]
    verify_tls: bool = True


class NextcloudTarget(TargetBase):
    type: Literal["nextcloud"]
    base_url: str
    admin_user: str | None = None
    admin_password: SecretStr | None = None
    verify_tls: bool = True
    default_groups: list[str] = []


class TokenApiTarget(TargetBase):
    base_url: str
    api_token: SecretStr | None = None
    verify_tls: bool = True


class GitlabTarget(TokenApiTarget):
    type: Literal["gitlab"]


class GiteaTarget(TokenApiTarget):
    type: Literal["gitea"]


class ImmichTarget(TokenApiTarget):
    type: Literal["immich"]


class JenkinsTarget(TargetBase):
    type: Literal["jenkins"]
    base_url: str
    admin_user: str | None = None
    api_token: SecretStr | None = None
    verify_tls: bool = True


class SshTarget(TargetBase):
    type: Literal["ssh"]
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    management_user: str | None = None
    management_password: SecretStr | None = None
    management_private_key: SecretStr | None = None
    management_private_key_file: str | None = None
    host_key_sha256: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{20,}={0,2}$")
    platform: Literal["debian", "ubuntu", "rhel", "rocky"]
    allow_relaxed_usernames: bool = False
    mode: Literal["password", "key", "password_and_key"] = "password_and_key"
    default_groups: list[str] = []

    @model_validator(mode="after")
    def validate_management_key(self) -> "SshTarget":
        key_sources = sum(bool(item) for item in (
            self.management_private_key, self.management_private_key_file
        ))
        if key_sources > 1:
            raise ValueError("only one SSH management private-key source is allowed")
        return self


Target = Annotated[
    OpnsenseTarget | NexusTarget | NextcloudTarget | GitlabTarget | GiteaTarget
    | ImmichTarget | JenkinsTarget | SshTarget,
    Field(discriminator="type"),
]


class FileConfig(StrictModel):
    version: Literal[1] = 1
    password_policy: PasswordPolicy = PasswordPolicy()
    ssh_key_policy: SshKeyPolicy = SshKeyPolicy()
    support_policy: SupportPolicy = SupportPolicy()
    audit_policy: AuditPolicy = AuditPolicy()
    reconciliation_policy: ReconciliationPolicy = ReconciliationPolicy()
    unmanaged_account_policy: UnmanagedAccountPolicy = UnmanagedAccountPolicy()
    lifecycle_automation_policy: LifecycleAutomationPolicy = LifecycleAutomationPolicy()
    automation_api_policy: AutomationApiPolicy = AutomationApiPolicy()
    admin_mfa_policy: AdminMfaPolicy = AdminMfaPolicy()
    notification_policy: NotificationPolicy = NotificationPolicy()
    targets: list[Target] = []

    @model_validator(mode="after")
    def validate_targets(self) -> "FileConfig":
        ids = [target.id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("target IDs must be unique")
        for target in self.targets:
            groups = getattr(target, "default_groups", [])
            if len(groups) != len(set(groups)) or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", group) for group in groups):
                raise ValueError(f"{target.id} default groups must be unique safe identifiers")
        return self


def _resolve_env(value: object, path: str = "config") -> object:
    if isinstance(value, dict):
        return {key: _resolve_env(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, str) and (match := ENV_REF_RE.fullmatch(value)):
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"missing environment variable {name} referenced by {path}")
        return os.environ[name]
    return value


def load_file_config(path: str | Path) -> FileConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return FileConfig.model_validate(_resolve_env(raw))


class Settings(BaseSettings):
    """Environment-owned bootstrap settings plus the YAML application contract."""

    model_config = SettingsConfigDict(
        env_prefix="NA_SSO_", env_file=".config/.env", extra="ignore"
    )

    secret_key: str = "change-me"
    admin_username: str = "admin"
    admin_bootstrap_password: str = "change-me"
    root_recovery_code: SecretStr | None = None
    database_path: str = "data/na-sso.db"
    session_cookie_secure: bool = False
    retry_scan_seconds: float = 5.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    config_file: str | None = None

    # Legacy settings remain readable until registry-driven connectors land.
    opnsense_enabled: bool = False
    opnsense_base_url: str = ""
    opnsense_api_key: str = ""
    opnsense_api_secret: str = ""
    opnsense_verify_tls: bool = True
    nexus_enabled: bool = False
    nexus_base_url: str = ""
    nexus_admin_user: str = ""
    nexus_admin_password: str = ""
    nexus_default_roles: str = "nx-anonymous"
    nextcloud_enabled: bool = False
    nextcloud_base_url: str = ""
    nextcloud_admin_user: str = ""
    nextcloud_admin_password: str = ""

    @property
    def file(self) -> FileConfig:
        return load_file_config(self.config_file) if self.config_file else FileConfig()


@lru_cache
def get_settings() -> Settings:
    return Settings()
