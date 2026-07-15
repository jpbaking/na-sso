import httpx

from na_sso.config import OpnsenseTarget, Settings
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    InspectionCapabilities,
    ReconciliationReport,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)


class OPNsenseConnector(Connector):
    """OPNsense core auth/user API (api/auth/user/*), key+secret basic auth."""

    capabilities = IdentityCapabilities(email=True, display_name=True)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True
    )

    def __init__(self, settings: Settings | OpnsenseTarget):
        if isinstance(settings, OpnsenseTarget):
            self.target_id, self.target_type, self.display_name = settings.id, settings.type, settings.display_name
            self._base = settings.base_url.rstrip("/")
            self._auth = (settings.api_key.get_secret_value(), settings.api_secret.get_secret_value())
            self._verify = settings.verify_tls
            self._groups = settings.default_groups
        else:
            self.target_id = self.target_type = "opnsense"
            self.display_name = "OPNsense"
            self._base = settings.opnsense_base_url.rstrip("/")
            self._auth = (settings.opnsense_api_key, settings.opnsense_api_secret)
            self._verify = settings.opnsense_verify_tls
            self._groups = []

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api",
            auth=self._auth,
            verify=self._verify,
            timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        r = await client.post("/auth/user/search", json={"searchPhrase": username})
        r.raise_for_status()
        for row in r.json().get("rows", []):
            if row.get("name") == username:
                return row
        return None

    async def _find_uuid(self, client: httpx.AsyncClient, username: str) -> str | None:
        row = await self._find_user(client, username)
        return row.get("uuid") if row else None

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        return await self.inspect_user_for_assignment(user, frozenset(self._groups))

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.post("/auth/user/search", json={"rowCount": -1})
                response.raise_for_status()
            accounts = tuple(RemoteAccount(
                username=str(row.get("name", "")),
                display_name=str(row.get("descr", "")),
                email=str(row.get("email", "")),
                status="disabled" if str(row.get("disabled", "0")).lower() in {"1", "true", "yes"} else "active",
            ) for row in response.json().get("rows", []) if row.get("name"))
            return AccountDiscovery(True, accounts)
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="OPNsense account discovery failed.")

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        try:
            async with self._client() as client:
                row = await self._find_user(client, user.username)
            actual_memberships: frozenset[str] | None = None
            if row is not None:
                raw_memberships = row.get("group_memberships")
                if isinstance(raw_memberships, str):
                    actual_memberships = frozenset(item.strip() for item in raw_memberships.split(",") if item.strip())
                elif isinstance(raw_memberships, list):
                    actual_memberships = frozenset(str(item) for item in raw_memberships)
                disabled = row.get("disabled")
                status = None if disabled is None else ("disabled" if str(disabled).lower() in {"1", "true", "yes"} else "active")
            else:
                status = None
            snapshot = RemoteIdentitySnapshot(
                present=row is not None,
                username=str(row.get("name")) if row else None,
                display_name=str(row.get("descr")) if row and row.get("descr") is not None else None,
                email=str(row.get("email")) if row and row.get("email") is not None else None,
                status=status,
                memberships=actual_memberships,
            )
            return compare_snapshot(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                snapshot=snapshot,
                required_memberships=memberships,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable_report(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                detail="OPNsense identity read failed.",
                required_memberships=memberships,
            )

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        return await self.ensure_user_for_assignment(user, password, frozenset(self._groups))

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        try:
            async with self._client() as client:
                uuid = await self._find_uuid(client, user.username)
                payload: dict = {
                    "user": {
                        "name": user.username,
                        "descr": user.display_name or user.username,
                        "email": user.email,
                        "disabled": "1" if user.status == "disabled" else "0",
                        "group_memberships": ",".join(sorted(memberships)),
                    }
                }
                if password is not None:
                    payload["user"]["password"] = password
                if uuid:
                    r = await client.post(f"/auth/user/set/{uuid}", json=payload)
                else:
                    r = await client.post("/auth/user/add", json=payload)
                r.raise_for_status()
                body = r.json()
                if body.get("result") not in ("saved", "ok"):
                    return SyncResult(False, f"opnsense rejected save: {body}")
                return SyncResult(True, "saved")
        except httpx.HTTPError as e:
            return SyncResult(False, f"opnsense http error: {e}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        return await self.ensure_user(user, None)

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        return await self.ensure_user_for_assignment(user, None, memberships)

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                uuid = await self._find_uuid(client, user.username)
                if not uuid:
                    return SyncResult(True, "already absent")
                r = await client.post(f"/auth/user/del/{uuid}", json={})
                r.raise_for_status()
                if r.json().get("result") != "deleted":
                    return SyncResult(False, f"opnsense rejected delete: {r.text}")
                return SyncResult(True, "deleted")
        except httpx.HTTPError as e:
            return SyncResult(False, f"opnsense http error: {e}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                r = await client.post("/auth/user/search", json={"rowCount": 1})
                r.raise_for_status()
                return SyncResult(True, "reachable")
        except httpx.HTTPError as e:
            return SyncResult(False, f"opnsense unreachable: {e}")
