from urllib.parse import quote, urlencode

import httpx

from na_sso.config import NextcloudTarget, Settings
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, IdentityCapabilities, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    InspectionCapabilities,
    ReconciliationReport,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)


class NextcloudConnector(Connector):
    """Nextcloud OCS User Provisioning API connector."""

    capabilities = IdentityCapabilities(email=True, display_name=True)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True,
        memberships_exact=False,
    )

    def __init__(self, settings: Settings | NextcloudTarget):
        if isinstance(settings, NextcloudTarget):
            self.target_id, self.target_type, self.display_name = settings.id, settings.type, settings.display_name
            self._base = settings.base_url.rstrip("/")
            self._auth = (settings.admin_user, settings.admin_password.get_secret_value())
            self._verify = settings.verify_tls
            self._groups = settings.default_groups
        else:
            self.target_id = self.target_type = "nextcloud"
            self.display_name = "Nextcloud"
            self._base = settings.nextcloud_base_url.rstrip("/")
            self._auth = (settings.nextcloud_admin_user, settings.nextcloud_admin_password)
            self._verify = True
            self._groups = []

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/ocs/v1.php/cloud",
            auth=self._auth,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            params={"format": "json"},
            verify=self._verify,
            timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
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

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        return await self.inspect_user_for_assignment(user, frozenset(self._groups))

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.get("/users")
                code, _ = self._ocs(response)
            if code != 100:
                return AccountDiscovery(True, detail="Nextcloud account discovery was rejected.")
            users = response.json().get("ocs", {}).get("data", {}).get("users", [])
            return AccountDiscovery(True, tuple(
                RemoteAccount(username=str(username)) for username in users if username
            ))
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="Nextcloud account discovery failed.")

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        try:
            async with self._client() as client:
                response = await client.get(self._path(user.username))
                code, _ = self._ocs(response)
                if code == 100:
                    existing = response.json().get("ocs", {}).get("data", {})
                elif code in {404, 998}:
                    existing = None
                else:
                    return unavailable_report(
                        target_id=self.target_id,
                        target_name=self.display_name,
                        user=user,
                        capabilities=self.inspection_capabilities,
                        detail="Nextcloud identity read was rejected.",
                        required_memberships=memberships,
                    )
                actual_memberships = None
                if existing is not None:
                    group_response = await client.get(f"{self._path(user.username)}/groups")
                    group_code, _ = self._ocs(group_response)
                    if group_code == 100:
                        raw_groups = group_response.json().get("ocs", {}).get("data", {}).get("groups")
                        if isinstance(raw_groups, list):
                            actual_memberships = frozenset(str(group) for group in raw_groups)
            enabled = existing.get("enabled") if existing is not None else None
            if enabled is None:
                status = None
            elif isinstance(enabled, str):
                status = "active" if enabled.lower() in {"1", "true", "yes", "enabled"} else "disabled"
            else:
                status = "active" if bool(enabled) else "disabled"
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=str(existing.get("id")) if existing and existing.get("id") is not None else None,
                display_name=str(existing.get("displayname")) if existing and existing.get("displayname") is not None else None,
                email=str(existing.get("email")) if existing and existing.get("email") is not None else None,
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
                detail="Nextcloud identity read failed.",
                required_memberships=memberships,
            )

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        return await self.ensure_user_for_assignment(user, password, frozenset(self._groups))

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.get(self._path(user.username))
                code, _ = self._ocs(response)
                if code != 100:
                    if password is None:
                        return SyncResult(False, "nextcloud requires a password for a new user")
                    response = await client.post(
                        "/users",
                        content=urlencode(
                            {
                                "userid": user.username,
                                "password": password,
                                "displayName": user.display_name or user.username,
                                "email": user.email,
                                "groups[]": sorted(memberships),
                            },
                            doseq=True,
                        ),
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
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

                if memberships:
                    response = await client.get(f"{self._path(user.username)}/groups")
                    response.raise_for_status()
                    body = response.json().get("ocs", {})
                    code = int(body.get("meta", {}).get("statuscode", 0))
                    if code != 100:
                        return SyncResult(False, "nextcloud could not read group memberships")
                    current = set(body.get("data", {}).get("groups", []))
                    for group in sorted(memberships):
                        if group in current:
                            continue
                        response = await client.post(
                            f"{self._path(user.username)}/groups",
                            data={"groupid": group},
                        )
                        code, message = self._ocs(response)
                        if code != 100:
                            return SyncResult(
                                False,
                                f"nextcloud rejected group {group}: {code} {message}",
                            )

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

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        return await self.ensure_user_for_assignment(user, None, memberships)

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
