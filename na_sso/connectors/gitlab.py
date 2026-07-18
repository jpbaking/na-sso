import httpx

from na_sso.config import GitlabTarget
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import InspectionCapabilities, ReconciliationReport, RemoteIdentitySnapshot, compare_snapshot, unavailable_report


class GitlabConnector(Connector):
    """GitLab Self-Managed administrator Users API connector."""

    capabilities = IdentityCapabilities(email=True, email_required=True, display_name=True, display_name_required=True)
    inspection_capabilities = InspectionCapabilities(display_name=True, email=True, status=True)

    def __init__(self, target: GitlabTarget):
        self.target_id, self.target_type, self.display_name = target.id, target.type, target.display_name
        self._base = target.base_url.rstrip("/")
        self._token = target.api_token.get_secret_value()
        self._verify = target.verify_tls

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api/v4",
            headers={"PRIVATE-TOKEN": self._token, "Accept": "application/json"},
            verify=self._verify,
            timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        response = await client.get("/users", params={"username": username})
        response.raise_for_status()
        return next((item for item in response.json() if str(item.get("username", "")).lower() == username.lower()), None)

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=str(existing.get("username")) if existing else None,
                display_name=str(existing.get("name")) if existing else None,
                email=str(existing.get("email")) if existing and existing.get("email") is not None else None,
                status="active" if existing and existing.get("state") == "active" else "disabled" if existing else None,
            )
            return compare_snapshot(
                target_id=self.target_id, target_name=self.display_name, user=user,
                capabilities=self.inspection_capabilities, snapshot=snapshot,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable_report(
                target_id=self.target_id, target_name=self.display_name, user=user,
                capabilities=self.inspection_capabilities, detail="GitLab identity read failed.",
            )

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.get("/users", params={"per_page": 100, "exclude_internal": "true", "without_project_bots": "true"})
                response.raise_for_status()
            return AccountDiscovery(True, tuple(
                RemoteAccount(
                    username=str(item.get("username", "")), display_name=str(item.get("name", "")),
                    email=str(item.get("email", "")),
                    status="active" if item.get("state") == "active" else "disabled",
                )
                for item in response.json() if item.get("username")
            ))
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="GitLab account discovery failed.")

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                if existing is None:
                    if password is None:
                        return SyncResult(False, "GitLab requires a password for a new user")
                    response = await client.post("/users", json={
                        "username": user.username, "name": user.display_name,
                        "email": user.email, "password": password, "skip_confirmation": True,
                    })
                    response.raise_for_status()
                    existing = response.json()
                else:
                    payload = {"name": user.display_name, "email": user.email, "skip_reconfirmation": True}
                    if password is not None:
                        payload["password"] = password
                    response = await client.put(f"/users/{existing['id']}", json=payload)
                    response.raise_for_status()
                if user.status != "disabled" and existing.get("state") != "active":
                    response = await client.post(f"/users/{existing['id']}/unblock")
                    response.raise_for_status()
                if user.status == "disabled":
                    return await self._block(client, existing)
            return SyncResult(True, "saved")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"GitLab HTTP error: {error}")

    async def _block(self, client: httpx.AsyncClient, existing: dict) -> SyncResult:
        response = await client.post(f"/users/{existing['id']}/block")
        response.raise_for_status()
        return SyncResult(True, "disabled")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                return SyncResult(True, "already absent") if existing is None else await self._block(client, existing)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"GitLab HTTP error: {error}")

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                if existing is None:
                    return SyncResult(True, "already absent")
                response = await client.delete(f"/users/{existing['id']}")
                response.raise_for_status()
            return SyncResult(True, "deleted")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return SyncResult(False, f"GitLab HTTP error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get("/user")
                response.raise_for_status()
                if not response.json().get("is_admin"):
                    return SyncResult(False, "GitLab credential requires administrator access")
            return SyncResult(True, "reachable")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"GitLab unreachable: {error}")
