import httpx

from na_sso.config import NexusTarget, Settings
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    InspectionCapabilities,
    ReconciliationReport,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)


class NexusConnector(Connector):
    """Nexus Repository local-user API, authenticated with HTTP Basic auth."""

    capabilities = IdentityCapabilities(email=True, email_required=True, display_name=True, display_name_required=True)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True
    )

    def __init__(self, settings: Settings | NexusTarget):
        if isinstance(settings, NexusTarget):
            self.target_id, self.target_type, self.display_name = settings.id, settings.type, settings.display_name
            self._base = settings.base_url.rstrip("/")
            self._auth = (settings.admin_user, settings.admin_password.get_secret_value())
            self._roles = settings.default_roles
            self._verify = settings.verify_tls
        else:
            self.target_id = self.target_type = "nexus"
            self.display_name = "Nexus Repository"
            self._base = settings.nexus_base_url.rstrip("/")
            self._auth = (settings.nexus_admin_user, settings.nexus_admin_password)
            self._roles = [role.strip() for role in settings.nexus_default_roles.split(",") if role.strip()]
            self._verify = True

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/service/rest/v1",
            auth=self._auth,
            verify=self._verify,
            timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    def _user_payload(self, user: ManagedUser) -> dict:
        names = (user.display_name or user.username).split(maxsplit=1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else ""
        return {
            "userId": user.username,
            "firstName": first_name,
            "lastName": last_name,
            "emailAddress": user.email,
            "source": "default",
            "status": "disabled" if user.status == "disabled" else "active",
            "readOnly": False,
            "roles": self._roles,
            "externalRoles": [],
        }

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        response = await client.get(
            "/security/users", params={"userId": username, "source": "default"}
        )
        response.raise_for_status()
        return next(
            (item for item in response.json() if item.get("userId") == username), None
        )

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        return await self.inspect_user_for_assignment(user, frozenset(self._roles))

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.get("/security/users", params={"source": "default"})
                response.raise_for_status()
            accounts = tuple(RemoteAccount(
                username=str(item.get("userId", "")),
                display_name=" ".join(part for part in (
                    str(item.get("firstName", "")).strip(), str(item.get("lastName", "")).strip()
                ) if part),
                email=str(item.get("emailAddress", "")),
                status=str(item.get("status", "unknown")).lower(),
            ) for item in response.json() if item.get("userId"))
            return AccountDiscovery(True, accounts)
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="Nexus account discovery failed.")

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
            display_name = None
            if existing is not None:
                display_name = " ".join(
                    item for item in (str(existing.get("firstName", "")).strip(), str(existing.get("lastName", "")).strip()) if item
                )
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=str(existing.get("userId")) if existing else None,
                display_name=display_name,
                email=str(existing.get("emailAddress")) if existing and existing.get("emailAddress") is not None else None,
                status=str(existing.get("status")).lower() if existing and existing.get("status") is not None else None,
                memberships=frozenset(str(role) for role in existing.get("roles", [])) if existing and isinstance(existing.get("roles"), list) else None,
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
                detail="Nexus identity read failed.",
                required_memberships=memberships,
            )

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        return await self.ensure_user_for_assignment(user, password, frozenset(self._roles))

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                payload = self._user_payload(user)
                payload["roles"] = sorted(memberships)
                if existing:
                    response = await client.put(
                        f"/security/users/{user.username}", json=payload
                    )
                else:
                    if password is None:
                        return SyncResult(False, "nexus requires a password for a new user")
                    create_payload = dict(payload)
                    create_payload.pop("source")
                    create_payload.pop("readOnly")
                    create_payload.pop("externalRoles")
                    create_payload["password"] = password
                    response = await client.post("/security/users", json=create_payload)
                response.raise_for_status()
                if existing and password is not None:
                    response = await client.put(
                        f"/security/users/{user.username}/change-password",
                        content=password,
                        headers={"Content-Type": "text/plain"},
                    )
                    response.raise_for_status()
                return SyncResult(True, "saved")
        except httpx.HTTPError as error:
            return SyncResult(False, f"nexus http error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        return await self.ensure_user(user, None)

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        return await self.ensure_user_for_assignment(user, None, memberships)

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.delete(f"/security/users/{user.username}")
                if response.status_code == 404:
                    return SyncResult(True, "already absent")
                response.raise_for_status()
                return SyncResult(True, "deleted")
        except httpx.HTTPError as error:
            return SyncResult(False, f"nexus http error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get(
                    "/security/users", params={"userId": self._auth[0], "source": "default"}
                )
                response.raise_for_status()
                return SyncResult(True, "reachable")
        except httpx.HTTPError as error:
            return SyncResult(False, f"nexus unreachable: {error}")
