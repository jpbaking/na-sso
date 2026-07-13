import httpx

from oneauth.config import OpnsenseTarget, Settings
from oneauth.connectors.base import Connector, IdentityCapabilities, SyncResult
from oneauth.models import ManagedUser


class OPNsenseConnector(Connector):
    """OPNsense core auth/user API (api/auth/user/*), key+secret basic auth."""

    capabilities = IdentityCapabilities(email=True, display_name=True)

    def __init__(self, settings: Settings | OpnsenseTarget):
        if isinstance(settings, OpnsenseTarget):
            self.target_id, self.target_type, self.display_name = settings.id, settings.type, settings.display_name
            self._base = settings.base_url.rstrip("/")
            self._auth = (settings.api_key.get_secret_value(), settings.api_secret.get_secret_value())
            self._verify = settings.verify_tls
            self._groups = settings.default_groups
        else:
            self.target_id = self.target_type = "opnsense"; self.display_name = "OPNsense"
            self._base = settings.opnsense_base_url.rstrip("/")
            self._auth = (settings.opnsense_api_key, settings.opnsense_api_secret)
            self._verify = settings.opnsense_verify_tls
            self._groups = []

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api",
            auth=self._auth,
            verify=self._verify,
            timeout=15,
        )

    async def _find_uuid(self, client: httpx.AsyncClient, username: str) -> str | None:
        r = await client.post("/auth/user/search", json={"searchPhrase": username})
        r.raise_for_status()
        for row in r.json().get("rows", []):
            if row.get("name") == username:
                return row.get("uuid")
        return None

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        try:
            async with self._client() as client:
                uuid = await self._find_uuid(client, user.username)
                payload: dict = {
                    "user": {
                        "name": user.username,
                        "descr": user.display_name or user.username,
                        "email": user.email,
                        "disabled": "1" if user.status == "disabled" else "0",
                        "group_memberships": ",".join(self._groups),
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
