from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

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

    @model_validator(mode="after")
    def validate_algorithms(self) -> "SshKeyPolicy":
        if not self.allowed_algorithms or len(set(self.allowed_algorithms)) != len(self.allowed_algorithms):
            raise ValueError("allowed_algorithms must contain unique values")
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
        sources = sum(bool(item) for item in (self.management_password, self.management_private_key,
                                              self.management_private_key_file))
        if sources > 1:
            raise ValueError("only one SSH management authentication source is allowed")
        return self


Target = Annotated[
    OpnsenseTarget | NexusTarget | NextcloudTarget | SshTarget,
    Field(discriminator="type"),
]


class FileConfig(StrictModel):
    version: Literal[1] = 1
    password_policy: PasswordPolicy = PasswordPolicy()
    ssh_key_policy: SshKeyPolicy = SshKeyPolicy()
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
    database_path: str = "data/na-sso.db"
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
