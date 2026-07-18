from urllib.parse import quote

import httpx

from na_sso.config import GiteaTarget
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import InspectionCapabilities, ReconciliationReport, RemoteIdentitySnapshot, compare_snapshot, unavailable_report


class GiteaConnector(Connector):
    """Gitea administrator Users API connector."""

    capabilities = IdentityCapabilities(email=True, email_required=True, display_name=True)
    inspection_capabilities = InspectionCapabilities(display_name=True, email=True, status=True)

    def __init__(self, target: GiteaTarget):
        self.target_id, self.target_type, self.display_name = target.id, target.type, target.display_name
        self._base = target.base_url.rstrip("/")
        self._token = target.api_token.get_secret_value()
        self._verify = target.verify_tls

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api/v1", headers={"Authorization": f"token {self._token}", "Accept": "application/json"},
            verify=self._verify, timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    # Gitea caps page size at its MAX_RESPONSE_ITEMS (50 by default); page until
    # a short page so accounts beyond the first page are still seen.
    async def _users(self, client: httpx.AsyncClient, *, limit: int = 50, max_pages: int = 20) -> list[dict]:
        users: list[dict] = []
        for page in range(1, max_pages + 1):
            response = await client.get("/admin/users", params={"page": page, "limit": limit})
            response.raise_for_status()
            batch = response.json()
            users.extend(batch)
            if len(batch) < limit:
                break
        return users

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        return next((item for item in await self._users(client)
                     if str(item.get("login", "")).lower() == username.lower()), None)

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=str(existing.get("login")) if existing else None,
                display_name=str(existing.get("full_name", "")) if existing else None,
                email=str(existing.get("email", "")) if existing else None,
                status="disabled" if existing and existing.get("prohibit_login") else "active" if existing else None,
            )
            return compare_snapshot(target_id=self.target_id, target_name=self.display_name, user=user,
                                    capabilities=self.inspection_capabilities, snapshot=snapshot)
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable_report(target_id=self.target_id, target_name=self.display_name, user=user,
                                      capabilities=self.inspection_capabilities, detail="Gitea identity read failed.")

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                users = await self._users(client)
            return AccountDiscovery(True, tuple(
                RemoteAccount(username=str(item.get("login", "")), display_name=str(item.get("full_name", "")),
                              email=str(item.get("email", "")), status="disabled" if item.get("prohibit_login") else "active")
                for item in users if item.get("login")
            ))
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="Gitea account discovery failed.")

    async def _update(self, client: httpx.AsyncClient, user: ManagedUser, *, password: str | None = None, disabled: bool = False) -> None:
        payload = {
            "login_name": user.username, "source_id": 0, "email": user.email,
            "full_name": user.display_name or user.username, "active": not disabled,
            "prohibit_login": disabled, "must_change_password": False,
        }
        if password is not None:
            payload["password"] = password
        response = await client.patch(f"/admin/users/{quote(user.username, safe='')}", json=payload)
        response.raise_for_status()

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                if existing is None:
                    if password is None:
                        return SyncResult(False, "Gitea requires a password for a new user")
                    response = await client.post("/admin/users", json={
                        "username": user.username, "email": user.email, "full_name": user.display_name or user.username,
                        "password": password, "must_change_password": False, "send_notify": False,
                    })
                    response.raise_for_status()
                    if user.status == "disabled":
                        await self._update(client, user, disabled=True)
                else:
                    await self._update(client, user, password=password, disabled=user.status == "disabled")
            return SyncResult(True, "saved")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"Gitea HTTP error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                if await self._find_user(client, user.username) is None:
                    return SyncResult(True, "already absent")
                await self._update(client, user, disabled=True)
            return SyncResult(True, "disabled")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"Gitea HTTP error: {error}")

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.delete(f"/admin/users/{quote(user.username, safe='')}")
                if response.status_code == 404:
                    return SyncResult(True, "already absent")
                response.raise_for_status()
            return SyncResult(True, "deleted")
        except httpx.HTTPError as error:
            return SyncResult(False, f"Gitea HTTP error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get("/admin/users", params={"limit": 1})
                response.raise_for_status()
            return SyncResult(True, "reachable")
        except httpx.HTTPError as error:
            return SyncResult(False, f"Gitea unreachable: {error}")
