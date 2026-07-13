import httpx

from oneauth.config import Settings
from oneauth.connectors.base import Connector, SyncResult
from oneauth.models import ManagedUser


class NexusConnector(Connector):
    """Nexus Repository local-user API, authenticated with HTTP Basic auth."""

    name = "nexus"

    def __init__(self, settings: Settings):
        self._base = settings.nexus_base_url.rstrip("/")
        self._auth = (settings.nexus_admin_user, settings.nexus_admin_password)
        self._roles = [
            role.strip() for role in settings.nexus_default_roles.split(",") if role.strip()
        ]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/service/rest/v1",
            auth=self._auth,
            timeout=15,
        )

    def _user_payload(self, user: ManagedUser) -> dict:
        names = (user.display_name or user.username).split(maxsplit=1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else names[0]
        return {
            "userId": user.username,
            "firstName": first_name,
            "lastName": last_name,
            "emailAddress": user.email or f"{user.username}@invalid.local",
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

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                existing = await self._find_user(client, user.username)
                payload = self._user_payload(user)
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
