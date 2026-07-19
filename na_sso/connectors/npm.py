from __future__ import annotations

from typing import Any

import httpx

from na_sso.config import NpmTarget
from na_sso.connectors.base import (
    AccountDiscovery,
    Connector,
    ConnectorErrorKind,
    IdentityCapabilities,
    RemoteAccount,
    SyncResult,
)
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    InspectionCapabilities,
    ReconciliationReport,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)


class NpmAuthenticationError(ValueError):
    pass


class NpmConnector(Connector):
    """Nginx Proxy Manager v2.15.1 administrator Users API connector."""

    capabilities = IdentityCapabilities(
        email=True,
        email_required=True,
        display_name=True,
        display_name_required=True,
    )
    inspection_capabilities = InspectionCapabilities(
        display_name=True,
        email=True,
        status=True,
        memberships_exact=False,
    )

    def __init__(self, target: NpmTarget):
        self.target_id = target.id
        self.target_type = target.type
        self.display_name = target.display_name
        self._base = target.base_url.rstrip("/")
        self._admin_user = target.admin_user or ""
        self._admin_password = (
            target.admin_password.get_secret_value() if target.admin_password else ""
        )
        self._verify = target.verify_tls

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api",
            headers={"Accept": "application/json"},
            verify=self._verify,
            timeout=httpx.Timeout(
                self.operation_timeout_seconds,
                connect=self.connect_timeout_seconds,
            ),
        )

    async def _authenticate(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/tokens",
            json={"identity": self._admin_user, "secret": self._admin_password},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("NPM token response was not an object")
        if payload.get("requires_2fa") or payload.get("challenge_token"):
            raise NpmAuthenticationError(
                "NPM management accounts requiring 2FA are unsupported"
            )
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("NPM token response did not contain a token")
        client.headers["Authorization"] = f"Bearer {token}"

    async def _users(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        response = await client.get("/users")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("NPM users response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    async def _find_user(
        self, client: httpx.AsyncClient, email: str
    ) -> dict[str, Any] | None:
        wanted = email.strip().lower()
        return next(
            (
                item
                for item in await self._users(client)
                if str(item.get("email", "")).strip().lower() == wanted
            ),
            None,
        )

    @staticmethod
    def _user_payload(user: ManagedUser, *, disabled: bool) -> dict[str, Any]:
        return {
            "name": user.display_name,
            "nickname": user.username,
            "email": user.email,
            "is_disabled": disabled,
        }

    def _failure(
        self,
        action: str,
        error: Exception,
        *,
        authenticating: bool = False,
    ) -> SyncResult:
        if isinstance(error, httpx.TimeoutException):
            return SyncResult(
                False,
                f"NPM {action} timed out",
                ConnectorErrorKind.TIMEOUT,
                True,
            )
        if isinstance(error, httpx.RequestError):
            return SyncResult(
                False,
                f"NPM {action} is unavailable",
                ConnectorErrorKind.UNAVAILABLE,
                True,
            )
        if isinstance(error, NpmAuthenticationError):
            return SyncResult(
                False,
                "NPM management authentication requires unsupported 2FA",
                ConnectorErrorKind.AUTHENTICATION,
            )
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status in {401, 403} or (authenticating and status == 400):
                kind, retryable = ConnectorErrorKind.AUTHENTICATION, False
            elif status == 400:
                kind, retryable = ConnectorErrorKind.VALIDATION, False
            elif status == 404:
                kind, retryable = ConnectorErrorKind.NOT_FOUND, False
            elif status == 409:
                kind, retryable = ConnectorErrorKind.CONFLICT, False
            elif status == 429:
                kind, retryable = ConnectorErrorKind.RATE_LIMITED, True
            elif status >= 500:
                kind, retryable = ConnectorErrorKind.UNAVAILABLE, True
            else:
                kind, retryable = ConnectorErrorKind.REMOTE_REJECTED, False
            return SyncResult(
                False,
                f"NPM {action} failed with HTTP {status}",
                kind,
                retryable,
            )
        return SyncResult(
            False,
            f"NPM {action} received an invalid response",
            ConnectorErrorKind.REMOTE_REJECTED,
        )

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        try:
            async with self._client() as client:
                await self._authenticate(client)
                existing = await self._find_user(client, user.email or "")
            snapshot = RemoteIdentitySnapshot(
                present=existing is not None,
                username=str(existing.get("nickname", "")) if existing else None,
                display_name=str(existing.get("name", "")) if existing else None,
                email=str(existing.get("email", "")) if existing else None,
                status=(
                    "disabled"
                    if existing and existing.get("is_disabled")
                    else "active" if existing else None
                ),
            )
            return compare_snapshot(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                snapshot=snapshot,
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return unavailable_report(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                detail="NPM identity read failed.",
            )

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                await self._authenticate(client)
                users = await self._users(client)
            return AccountDiscovery(
                True,
                tuple(
                    RemoteAccount(
                        username=str(item.get("email", "")),
                        display_name=str(item.get("name", "")),
                        email=str(item.get("email", "")),
                        status=(
                            "disabled" if item.get("is_disabled") else "active"
                        ),
                    )
                    for item in users
                    if item.get("email")
                ),
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return AccountDiscovery(True, detail="NPM account discovery failed.")

    async def ensure_user(
        self, user: ManagedUser, password: str | None
    ) -> SyncResult:
        if password is not None and not 8 <= len(password) <= 64:
            return SyncResult(
                False,
                "NPM requires passwords between 8 and 64 characters",
                ConnectorErrorKind.VALIDATION,
            )
        authenticating = True
        try:
            async with self._client() as client:
                await self._authenticate(client)
                authenticating = False
                existing = await self._find_user(client, user.email or "")
                disabled = user.status == "disabled"
                if existing is None:
                    if password is None:
                        return SyncResult(
                            False,
                            "NPM requires a password for a new user",
                            ConnectorErrorKind.VALIDATION,
                        )
                    payload = self._user_payload(user, disabled=disabled)
                    payload["roles"] = []
                    payload["auth"] = {"type": "password", "secret": password}
                    response = await client.post("/users", json=payload)
                    response.raise_for_status()
                    return SyncResult(True, "created")

                response = await client.put(
                    f"/users/{existing['id']}",
                    json=self._user_payload(
                        user,
                        disabled=disabled,
                    ),
                )
                response.raise_for_status()
                if password is not None:
                    response = await client.put(
                        f"/users/{existing['id']}/auth",
                        json={"type": "password", "secret": password},
                    )
                    response.raise_for_status()
            return SyncResult(True, "saved")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return self._failure(
                "authentication" if authenticating else "save",
                error,
                authenticating=authenticating,
            )

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        authenticating = True
        try:
            async with self._client() as client:
                await self._authenticate(client)
                authenticating = False
                existing = await self._find_user(client, user.email or "")
                if existing is None:
                    return SyncResult(True, "already absent")
                if existing.get("is_disabled"):
                    return SyncResult(True, "already disabled")
                response = await client.put(
                    f"/users/{existing['id']}",
                    json={
                        "name": existing.get("name", user.display_name),
                        "nickname": existing.get("nickname", user.username),
                        "email": existing.get("email", user.email),
                        "is_disabled": True,
                    },
                )
                response.raise_for_status()
            return SyncResult(True, "disabled")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return self._failure(
                "authentication" if authenticating else "disable",
                error,
                authenticating=authenticating,
            )

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        authenticating = True
        try:
            async with self._client() as client:
                await self._authenticate(client)
                authenticating = False
                existing = await self._find_user(client, user.email or "")
                if existing is None:
                    return SyncResult(True, "already absent")
                response = await client.delete(f"/users/{existing['id']}")
                if response.status_code == 404:
                    return SyncResult(True, "already absent")
                response.raise_for_status()
            return SyncResult(True, "deleted")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return self._failure(
                "authentication" if authenticating else "delete",
                error,
                authenticating=authenticating,
            )

    async def probe(self) -> SyncResult:
        authenticating = True
        try:
            async with self._client() as client:
                await self._authenticate(client)
                authenticating = False
                await self._users(client)
            return SyncResult(True, "reachable")
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            return self._failure(
                "authentication" if authenticating else "probe",
                error,
                authenticating=authenticating,
            )
