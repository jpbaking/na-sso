from urllib.parse import quote

import httpx

from oneauth.config import Settings
from oneauth.connectors.base import Connector, SyncResult
from oneauth.models import ManagedUser


class NextcloudConnector(Connector):
    """Nextcloud OCS User Provisioning API connector."""

    name = "nextcloud"

    def __init__(self, settings: Settings):
        self._base = settings.nextcloud_base_url.rstrip("/")
        self._auth = (settings.nextcloud_admin_user, settings.nextcloud_admin_password)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/ocs/v1.php/cloud",
            auth=self._auth,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            params={"format": "json"},
            timeout=15,
        )

    @staticmethod
    def _ocs(response: httpx.Response) -> tuple[int, str]:
        response.raise_for_status()
        meta = response.json().get("ocs", {}).get("meta", {})
        return int(meta.get("statuscode", 0)), str(meta.get("message", ""))

    @staticmethod
    def _path(username: str) -> str:
        return f"/users/{quote(username, safe='')}"

    async def _edit(
        self, client: httpx.AsyncClient, username: str, key: str, value: str
    ) -> SyncResult:
        response = await client.put(
            self._path(username), data={"key": key, "value": value}
        )
        code, message = self._ocs(response)
        if code != 100:
            return SyncResult(False, f"nextcloud rejected {key}: {code} {message}")
        return SyncResult(True, "saved")

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get(self._path(user.username))
                code, _ = self._ocs(response)
                if code != 100:
                    if password is None:
                        return SyncResult(False, "nextcloud requires a password for a new user")
                    response = await client.post(
                        "/users",
                        data={
                            "userid": user.username,
                            "password": password,
                            "displayName": user.display_name or user.username,
                            "email": user.email,
                        },
                    )
                    code, message = self._ocs(response)
                    if code != 100:
                        return SyncResult(
                            False, f"nextcloud rejected create: {code} {message}"
                        )
                else:
                    for key, value in (
                        ("displayname", user.display_name or user.username),
                        ("email", user.email),
                    ):
                        result = await self._edit(client, user.username, key, value)
                        if not result.ok:
                            return result
                    if password is not None:
                        result = await self._edit(
                            client, user.username, "password", password
                        )
                        if not result.ok:
                            return result

                action = "disable" if user.status == "disabled" else "enable"
                response = await client.put(f"{self._path(user.username)}/{action}")
                code, message = self._ocs(response)
                if code != 100:
                    return SyncResult(
                        False, f"nextcloud rejected {action}: {code} {message}"
                    )
                return SyncResult(True, "saved")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"nextcloud error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        return await self.ensure_user(user, None)

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                lookup = await client.get(self._path(user.username))
                lookup_code, _ = self._ocs(lookup)
                if lookup_code != 100:
                    return SyncResult(True, "already absent")
                response = await client.delete(self._path(user.username))
                code, message = self._ocs(response)
                if code == 100:
                    return SyncResult(True, "deleted")
                return SyncResult(False, f"nextcloud rejected delete: {code} {message}")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"nextcloud error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get("/users", params={"search": self._auth[0]})
                code, message = self._ocs(response)
                if code == 100:
                    return SyncResult(True, "reachable")
                return SyncResult(False, f"nextcloud probe rejected: {code} {message}")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return SyncResult(False, f"nextcloud unreachable: {error}")
