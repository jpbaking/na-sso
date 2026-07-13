from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, read from environment (compose passes .config/.env)."""

    model_config = SettingsConfigDict(
        env_prefix="ONEAUTH_", env_file=".config/.env", extra="ignore"
    )

    # core
    secret_key: str = "change-me"  # session signing + Fernet key derivation
    admin_username: str = "admin"
    admin_bootstrap_password: str = "change-me"
    database_path: str = "data/oneauth.db"
    retry_scan_seconds: float = 5.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0

    # OPNsense
    opnsense_enabled: bool = False
    opnsense_base_url: str = ""
    opnsense_api_key: str = ""
    opnsense_api_secret: str = ""
    opnsense_verify_tls: bool = True

    # Nexus Repository
    nexus_enabled: bool = False
    nexus_base_url: str = ""
    nexus_admin_user: str = ""
    nexus_admin_password: str = ""
    nexus_default_roles: str = "nx-anonymous"  # comma-separated

    # Nextcloud
    nextcloud_enabled: bool = False
    nextcloud_base_url: str = ""
    nextcloud_admin_user: str = ""
    nextcloud_admin_password: str = ""  # app password recommended


@lru_cache
def get_settings() -> Settings:
    return Settings()
