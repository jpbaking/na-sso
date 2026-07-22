from urllib.parse import quote

import httpx

from na_sso.config import JenkinsTarget
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import InspectionCapabilities, ReconciliationReport, RemoteIdentitySnapshot, compare_snapshot, unavailable_report


class JenkinsConnector(Connector):
    """Jenkins built-in local security-realm connector."""

    capabilities = IdentityCapabilities(password=True)
    inspection_capabilities = InspectionCapabilities(display_name=True)
    disable_supported = False

    def __init__(self, target: JenkinsTarget):
        self.target_id, self.target_type, self.display_name = target.id, target.type, target.display_name
        self._base = target.base_url.rstrip("/")
        self._auth = (target.admin_user, target.api_token.get_secret_value())
        self._verify = target.verify_tls

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, auth=self._auth, verify=self._verify,
                                 timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS, follow_redirects=False)

    async def _crumb(self, client: httpx.AsyncClient) -> dict[str, str]:
        response = await client.get("/crumbIssuer/api/json")
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        payload = response.json()
        return {str(payload["crumbRequestField"]): str(payload["crumb"])}

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        response = await client.get(f"/user/{quote(username, safe='')}/api/json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None, username=str(existing.get("id")) if existing else None,
                display_name=str(existing.get("fullName", "")) if existing else None,
            )
            return compare_snapshot(target_id=self.target_id, target_name=self.display_name, user=user,
                                    capabilities=self.inspection_capabilities, snapshot=snapshot)
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable_report(target_id=self.target_id, target_name=self.display_name, user=user,
                                      capabilities=self.inspection_capabilities, detail="Jenkins identity read failed.")

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.get("/asynchPeople/api/json", params={"tree": "users[user[id,fullName]]"})
                response.raise_for_status()
            accounts = []
            for item in response.json().get("users", []):
                remote = item.get("user", item)
                if remote.get("id"):
                    accounts.append(RemoteAccount(username=str(remote["id"]), display_name=str(remote.get("fullName", "")), status="active"))
            return AccountDiscovery(True, tuple(accounts))
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="Jenkins account discovery failed.")

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                if existing is not None:
                    return SyncResult(True, "already present")
                if password is None:
                    return SyncResult(False, "Jenkins requires a password for a new local-realm user")
                headers = await self._crumb(client)
                response = await client.post("/securityRealm/createAccountByAdmin", headers=headers, data={
                    "username": user.username, "password1": password, "password2": password,
                    "fullname": user.display_name or user.username, "email": user.email or "",
                })
                if response.status_code >= 400:
                    response.raise_for_status()
                # Jenkins reports signup errors as a 200 form page, so confirm
                # the account actually exists before claiming success.
                if await self._find_user(client, user.username) is None:
                    return SyncResult(False, "Jenkins security realm rejected the account creation")
            return SyncResult(True, "created")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"Jenkins HTTP error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        return SyncResult(False, "Jenkins core cannot safely disable a local account; use delete or a realm-specific authorization policy")

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                if await self._find_user(client, user.username) is None:
                    return SyncResult(True, "already absent")
                response = await client.post(f"/user/{quote(user.username, safe='')}/doDelete", headers=await self._crumb(client))
                if response.status_code >= 400:
                    response.raise_for_status()
            return SyncResult(True, "deleted")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"Jenkins HTTP error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get("/api/json")
                response.raise_for_status()
            return SyncResult(True, "reachable")
        except httpx.HTTPError as error:
            return SyncResult(False, f"Jenkins unreachable: {error}")
