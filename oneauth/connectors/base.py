from abc import ABC, abstractmethod
from dataclasses import dataclass
import re

from oneauth.models import ManagedUser


@dataclass
class SyncResult:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class IdentityCapabilities:
    email: bool = False
    email_required: bool = False
    display_name: bool = False
    display_name_required: bool = False
    password: bool = True
    public_key: bool = False


@dataclass(frozen=True)
class IdentityValidation:
    ok: bool
    detail: str = ""


class Connector(ABC):
    """One external credential target. All calls are idempotent."""

    target_id: str
    target_type: str
    display_name: str
    capabilities = IdentityCapabilities()

    @property
    def name(self) -> str:  # compatibility alias; persistence migrates to target_id
        return getattr(self, "target_id", getattr(self, "_name", self.__class__.__name__.lower()))

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        self.target_id = value
        self.target_type = value
        self.display_name = value

    def validate_identity(self, user: ManagedUser) -> IdentityValidation:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,63}", user.username):
            return IdentityValidation(False, f"{self.display_name} rejects the username")
        if self.capabilities.email_required and not user.email:
            return IdentityValidation(False, f"{self.display_name} requires email")
        if self.capabilities.display_name_required and not user.display_name:
            return IdentityValidation(False, f"{self.display_name} requires display name")
        return IdentityValidation(True)

    @abstractmethod
    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        """Create or update the user; set password when one is provided."""

    @abstractmethod
    async def disable_user(self, user: ManagedUser) -> SyncResult: ...

    @abstractmethod
    async def delete_user(self, user: ManagedUser) -> SyncResult: ...

    @abstractmethod
    async def probe(self) -> SyncResult:
        """Cheap reachability/auth check."""


def get_connectors() -> list[Connector]:
    """Enabled connectors, in propagation order."""
    from oneauth.config import get_settings

    s = get_settings()
    if s.config_file:
        factories = {}
        from oneauth.connectors.nextcloud import NextcloudConnector
        from oneauth.connectors.nexus import NexusConnector
        from oneauth.connectors.opnsense import OPNsenseConnector
        from oneauth.connectors.ssh import SSHConnector
        factories.update(opnsense=OPNsenseConnector, nexus=NexusConnector,
                         nextcloud=NextcloudConnector, ssh=SSHConnector)
        from oneauth.target_credentials import credential_payload
        connectors = []
        for target in s.file.targets:
            if not target.enabled:
                continue
            payload = credential_payload(target.id)
            if payload is None:
                continue
            updates = {}
            if target.type == "opnsense":
                updates = {"api_key": payload.get("api_key"), "api_secret": payload.get("api_secret")}
            elif target.type in {"nexus", "nextcloud"}:
                updates = {"admin_user": payload.get("admin_user"), "admin_password": payload.get("admin_password")}
            elif target.type == "ssh":
                updates = {"management_user": payload.get("management_user"),
                           "management_password": payload.get("management_password"),
                           "management_private_key": payload.get("management_private_key")}
            hydrated = target.__class__.model_validate({**target.model_dump(), **updates})
            connectors.append(factories[target.type](hydrated))
        return connectors
    out: list[Connector] = []
    if s.opnsense_enabled:
        from oneauth.connectors.opnsense import OPNsenseConnector
        out.append(OPNsenseConnector(s))
    if s.nexus_enabled:
        from oneauth.connectors.nexus import NexusConnector
        out.append(NexusConnector(s))
    if s.nextcloud_enabled:
        from oneauth.connectors.nextcloud import NextcloudConnector
        out.append(NextcloudConnector(s))
    return out


def build_unverified_connector(target_id: str) -> Connector:
    """Build one configured connector solely for its explicit test probe."""
    from oneauth.target_credentials import credential_payload, target_definitions
    target = next((item for item in target_definitions() if item.id == target_id), None)
    payload = credential_payload(target_id, verified_only=False)
    if target is None or payload is None:
        raise ValueError("target credentials are incomplete")
    updates = {}
    if target.type == "opnsense": updates = {"api_key": payload.get("api_key"), "api_secret": payload.get("api_secret")}
    elif target.type in {"nexus", "nextcloud"}: updates = {"admin_user": payload.get("admin_user"), "admin_password": payload.get("admin_password")}
    elif target.type == "ssh": updates = {"management_user": payload.get("management_user"), "management_password": payload.get("management_password"), "management_private_key": payload.get("management_private_key")}
    from oneauth.connectors.nextcloud import NextcloudConnector
    from oneauth.connectors.nexus import NexusConnector
    from oneauth.connectors.opnsense import OPNsenseConnector
    from oneauth.connectors.ssh import SSHConnector
    hydrated = target.__class__.model_validate({**target.model_dump(), **updates})
    return {"opnsense": OPNsenseConnector, "nexus": NexusConnector,
            "nextcloud": NextcloudConnector, "ssh": SSHConnector}[target.type](hydrated)


def validate_for_targets(user: ManagedUser, connectors: list[Connector]) -> IdentityValidation:
    for connector in connectors:
        result = connector.validate_identity(user)
        if not result.ok:
            return result
    return IdentityValidation(True)
