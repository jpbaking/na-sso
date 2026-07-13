from abc import ABC, abstractmethod
from dataclasses import dataclass

from oneauth.models import ManagedUser


@dataclass
class SyncResult:
    ok: bool
    detail: str = ""


class Connector(ABC):
    """One external credential target. All calls are idempotent."""

    name: str

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
