import httpx

from na_sso.config import ImmichTarget
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import InspectionCapabilities, ReconciliationReport, RemoteIdentitySnapshot, compare_snapshot, unavailable_report


class ImmichConnector(Connector):
    """Immich stable administrator Users API connector."""

    capabilities = IdentityCapabilities(email=True, email_required=True, display_name=True, display_name_required=True)
    inspection_capabilities = InspectionCapabilities(display_name=True, email=True, status=True)

    def __init__(self, target: ImmichTarget):
        self.target_id, self.target_type, self.display_name = target.id, target.type, target.display_name
        self._base = target.base_url.rstrip("/")
        self._token = target.api_token.get_secret_value()
        self._verify = target.verify_tls

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api", headers={"x-api-key": self._token, "Accept": "application/json"},
            verify=self._verify, timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    async def _users(self, client: httpx.AsyncClient, *, with_deleted: bool = False) -> list[dict]:
        response = await client.get("/admin/users", params={"withDeleted": str(with_deleted).lower()})
        response.raise_for_status()
        return response.json()

    async def _find_user(self, client: httpx.AsyncClient, email: str, *, with_deleted: bool = True) -> dict | None:
        return next((item for item in await self._users(client, with_deleted=with_deleted)
                     if str(item.get("email", "")).lower() == email.lower()), None)

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.email or "")
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=user.username if existing else None,
                display_name=str(existing.get("name")) if existing else None,
                email=str(existing.get("email")) if existing else None,
                status="active" if existing and existing.get("status") == "active" else "disabled" if existing else None,
            )
            return compare_snapshot(target_id=self.target_id, target_name=self.display_name, user=user,
                                    capabilities=self.inspection_capabilities, snapshot=snapshot)
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable_report(target_id=self.target_id, target_name=self.display_name, user=user,
                                      capabilities=self.inspection_capabilities, detail="Immich identity read failed.")

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                users = await self._users(client)
            return AccountDiscovery(True, tuple(
                RemoteAccount(username=str(item.get("email", "")), display_name=str(item.get("name", "")),
                              email=str(item.get("email", "")), status=str(item.get("status", "unknown")))
                for item in users if item.get("email")
            ))
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="Immich account discovery failed.")

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.email or "")
                if existing is None:
                    if password is None:
                        return SyncResult(False, "Immich requires a password for a new user")
                    response = await client.post("/admin/users", json={
                        "email": user.email, "name": user.display_name,
                        "password": password, "notify": False, "shouldChangePassword": False,
                    })
                    response.raise_for_status()
                    existing = response.json()
                elif existing.get("status") != "active" and user.status != "disabled":
                    response = await client.post(f"/admin/users/{existing['id']}/restore")
                    response.raise_for_status()
                if user.status == "disabled":
                    response = await client.request("DELETE", f"/admin/users/{existing['id']}", json={"force": False})
                    response.raise_for_status()
                    return SyncResult(True, "disabled")
                payload = {"email": user.email, "name": user.display_name, "shouldChangePassword": False}
                if password is not None:
                    payload["password"] = password
                response = await client.put(f"/admin/users/{existing['id']}", json=payload)
                response.raise_for_status()
            return SyncResult(True, "saved")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"Immich HTTP error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.email or "")
                if existing is None or existing.get("status") != "active":
                    return SyncResult(True, "already disabled or absent")
                response = await client.request("DELETE", f"/admin/users/{existing['id']}", json={"force": False})
                response.raise_for_status()
            return SyncResult(True, "disabled")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"Immich HTTP error: {error}")

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.email or "")
                if existing is None:
                    return SyncResult(True, "already absent")
                response = await client.request("DELETE", f"/admin/users/{existing['id']}", json={"force": True})
                response.raise_for_status()
            return SyncResult(True, "deleted")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"Immich HTTP error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                await self._users(client)
            return SyncResult(True, "reachable")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"Immich unreachable: {error}")
