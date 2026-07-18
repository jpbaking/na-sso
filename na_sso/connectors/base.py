from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
import re

from na_sso.models import ManagedUser
from na_sso.reconciliation import InspectionCapabilities, ReconciliationReport, unavailable_report


CONNECTOR_CONTRACT_VERSION = "1.0"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15
DEFAULT_OPERATION_TIMEOUT_SECONDS = 20


class ConnectorErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    REMOTE_REJECTED = "remote_rejected"
    INTERNAL = "internal"


@dataclass
class SyncResult:
    ok: bool
    detail: str = ""
    error_kind: ConnectorErrorKind | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.ok or self.error_kind is not None:
            return
        lowered = self.detail.lower()
        if "timeout" in lowered or "timed out" in lowered:
            self.error_kind, self.retryable = ConnectorErrorKind.TIMEOUT, True
        elif any(item in lowered for item in ("unauthorized", "forbidden", "authentication", "credential")):
            self.error_kind = ConnectorErrorKind.AUTHENTICATION
        elif any(item in lowered for item in ("unreachable", "connection", "network", "http error", "ssh error")):
            self.error_kind, self.retryable = ConnectorErrorKind.UNAVAILABLE, True
        elif any(item in lowered for item in ("requires", "cannot safely", "rejects the username")):
            self.error_kind = ConnectorErrorKind.VALIDATION
        elif "not found" in lowered:
            self.error_kind = ConnectorErrorKind.NOT_FOUND
        else:
            self.error_kind = ConnectorErrorKind.REMOTE_REJECTED


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


@dataclass(frozen=True)
class RemoteAccount:
    """Sanitised local account metadata; never include credentials or key material."""

    username: str
    display_name: str = ""
    email: str = ""
    status: str = "unknown"
    uid: int | None = None


@dataclass(frozen=True)
class AccountDiscovery:
    supported: bool
    accounts: tuple[RemoteAccount, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class DryRunResult:
    supported: bool
    actions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ConnectorContract:
    version: str
    connector_type: str
    inspect: bool
    account_discovery: bool
    dry_run: bool
    exact_memberships: bool
    public_key_last_used: bool
    connect_timeout_seconds: int
    operation_timeout_seconds: int
    error_kinds: tuple[str, ...]


class Connector(ABC):
    """One external credential target. All calls are idempotent."""

    target_id: str
    target_type: str
    display_name: str
    capabilities = IdentityCapabilities()
    inspection_capabilities = InspectionCapabilities()
    connect_timeout_seconds = DEFAULT_CONNECT_TIMEOUT_SECONDS
    operation_timeout_seconds = DEFAULT_OPERATION_TIMEOUT_SECONDS
    public_key_last_used_supported = False

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

    @property
    def default_memberships(self) -> frozenset[str]:
        return frozenset(getattr(self, "_groups", getattr(self, "_roles", ())))

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

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        """Read desired-versus-actual user state without remote mutation."""
        return unavailable_report(
            target_id=self.target_id,
            target_name=self.display_name,
            user=user,
            capabilities=self.inspection_capabilities,
            detail="Connector inspection is not implemented.",
        )

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        """Inspect with resolved profile/exception memberships; legacy fakes may ignore them."""
        return await self.inspect_user(user)

    async def discover_accounts(self) -> AccountDiscovery:
        """List sanitised target-local accounts without mutation."""
        return AccountDiscovery(False, detail="Connector account discovery is not implemented.")

    def contract_metadata(self) -> ConnectorContract:
        inspect = type(self).inspect_user is not Connector.inspect_user
        discovery = type(self).discover_accounts is not Connector.discover_accounts
        return ConnectorContract(
            version=CONNECTOR_CONTRACT_VERSION,
            connector_type=self.target_type,
            inspect=inspect,
            account_discovery=discovery,
            dry_run=inspect,
            exact_memberships=self.inspection_capabilities.memberships_exact,
            public_key_last_used=self.public_key_last_used_supported,
            connect_timeout_seconds=self.connect_timeout_seconds,
            operation_timeout_seconds=self.operation_timeout_seconds,
            error_kinds=tuple(item.value for item in ConnectorErrorKind),
        )

    async def plan_user(
        self, user: ManagedUser, memberships: frozenset[str] = frozenset()
    ) -> DryRunResult:
        """Build a read-only plan from inspection; never invokes a mutating method."""
        if type(self).inspect_user is Connector.inspect_user:
            return DryRunResult(False, detail="Connector dry-run planning is unsupported.")
        report = await self.inspect_user_for_assignment(user, memberships)
        actions = tuple(
            f"set {field.field.value}"
            for field in report.fields if field.state.value == "drift"
        )
        blockers = tuple(
            f"cannot observe {field.field.value}"
            for field in report.fields if field.state.value == "unknown"
        )
        return DryRunResult(True, actions, blockers, report.detail)

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        """Ensure with resolved profile/exception memberships; legacy fakes may ignore them."""
        return await self.ensure_user(user, password)

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        return await self.disable_user(user)


def get_connectors() -> list[Connector]:
    """Enabled connectors, in propagation order."""
    from na_sso.config import get_settings

    s = get_settings()
    if s.config_file:
        from na_sso.connectors.gitea import GiteaConnector
        from na_sso.connectors.gitlab import GitlabConnector
        from na_sso.connectors.immich import ImmichConnector
        from na_sso.connectors.jenkins import JenkinsConnector
        from na_sso.connectors.nextcloud import NextcloudConnector
        from na_sso.connectors.nexus import NexusConnector
        from na_sso.connectors.opnsense import OPNsenseConnector
        from na_sso.connectors.ssh import SSHConnector
        factories = {
            "opnsense": OPNsenseConnector, "nexus": NexusConnector,
            "nextcloud": NextcloudConnector, "ssh": SSHConnector,
            "gitlab": GitlabConnector, "gitea": GiteaConnector,
            "immich": ImmichConnector, "jenkins": JenkinsConnector,
        }
        from na_sso.target_credentials import credential_payload
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
            elif target.type in {"gitlab", "gitea", "immich"}:
                updates = {"api_token": payload.get("api_token")}
            elif target.type == "jenkins":
                updates = {"admin_user": payload.get("admin_user"), "api_token": payload.get("api_token")}
            elif target.type == "ssh":
                updates = {"management_user": payload.get("management_user"),
                           "management_password": payload.get("management_password"),
                           "management_private_key": payload.get("management_private_key")}
            hydrated = target.__class__.model_validate({**target.model_dump(), **updates})
            connectors.append(factories[target.type](hydrated))
        return connectors
    out: list[Connector] = []
    if s.opnsense_enabled:
        from na_sso.connectors.opnsense import OPNsenseConnector
        out.append(OPNsenseConnector(s))
    if s.nexus_enabled:
        from na_sso.connectors.nexus import NexusConnector
        out.append(NexusConnector(s))
    if s.nextcloud_enabled:
        from na_sso.connectors.nextcloud import NextcloudConnector
        out.append(NextcloudConnector(s))
    return out


def build_unverified_connector(target_id: str) -> Connector:
    """Build one configured connector solely for its explicit test probe."""
    from na_sso.target_credentials import credential_payload, target_definitions
    target = next((item for item in target_definitions() if item.id == target_id), None)
    payload = credential_payload(target_id, verified_only=False)
    if target is None or payload is None:
        raise ValueError("target credentials are incomplete")
    updates = {}
    if target.type == "opnsense":
        updates = {
            "api_key": payload.get("api_key"),
            "api_secret": payload.get("api_secret"),
        }
    elif target.type in {"nexus", "nextcloud"}:
        updates = {
            "admin_user": payload.get("admin_user"),
            "admin_password": payload.get("admin_password"),
        }
    elif target.type in {"gitlab", "gitea", "immich"}:
        updates = {"api_token": payload.get("api_token")}
    elif target.type == "jenkins":
        updates = {
            "admin_user": payload.get("admin_user"),
            "api_token": payload.get("api_token"),
        }
    elif target.type == "ssh":
        updates = {
            "management_user": payload.get("management_user"),
            "management_password": payload.get("management_password"),
            "management_private_key": payload.get("management_private_key"),
        }
    from na_sso.connectors.gitea import GiteaConnector
    from na_sso.connectors.gitlab import GitlabConnector
    from na_sso.connectors.immich import ImmichConnector
    from na_sso.connectors.jenkins import JenkinsConnector
    from na_sso.connectors.nextcloud import NextcloudConnector
    from na_sso.connectors.nexus import NexusConnector
    from na_sso.connectors.opnsense import OPNsenseConnector
    from na_sso.connectors.ssh import SSHConnector
    hydrated = target.__class__.model_validate({**target.model_dump(), **updates})
    return {
        "opnsense": OPNsenseConnector, "nexus": NexusConnector,
        "nextcloud": NextcloudConnector, "ssh": SSHConnector,
        "gitlab": GitlabConnector, "gitea": GiteaConnector,
        "immich": ImmichConnector, "jenkins": JenkinsConnector,
    }[target.type](hydrated)


def validate_for_targets(user: ManagedUser, connectors: list[Connector]) -> IdentityValidation:
    for connector in connectors:
        result = connector.validate_identity(user)
        if not result.ok:
            return result
    return IdentityValidation(True)
