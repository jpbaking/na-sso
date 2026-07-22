import asyncio
import base64
import binascii
import logging
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from na_sso.config import OpnsenseTarget, Settings
from na_sso.connectors.base import (
    AccountDiscovery,
    Connector,
    ConnectorErrorKind,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ExportedConfig,
    IdentityCapabilities,
    OpenVpnAuthPosture,
    OpenVpnDiscovery,
    OpenVpnServer,
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


logger = logging.getLogger(__name__)


class OPNsenseConnector(Connector):
    """OPNsense core auth/user API (api/auth/user/*), key+secret basic auth."""

    capabilities = IdentityCapabilities(email=True, display_name=True)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True
    )

    def __init__(self, settings: Settings | OpnsenseTarget):
        if isinstance(settings, OpnsenseTarget):
            self.target_id, self.target_type, self.display_name = settings.id, settings.type, settings.display_name
            self._base = settings.base_url.rstrip("/")
            self._auth = (settings.api_key.get_secret_value(), settings.api_secret.get_secret_value())
            self._verify = settings.verify_tls
            self._groups = settings.default_groups
        else:
            self.target_id = self.target_type = "opnsense"
            self.display_name = "OPNsense"
            self._base = settings.opnsense_base_url.rstrip("/")
            self._auth = (settings.opnsense_api_key, settings.opnsense_api_secret)
            self._verify = settings.opnsense_verify_tls
            self._groups = []
        self._openvpn_client_certificates: dict[str, tuple[str, str]] = {}
        self._openvpn_certificate_lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._base}/api",
            auth=self._auth,
            verify=self._verify,
            timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    async def _find_user(self, client: httpx.AsyncClient, username: str) -> dict | None:
        r = await client.post("/auth/user/search", json={"searchPhrase": username})
        r.raise_for_status()
        for row in r.json().get("rows", []):
            if row.get("name") == username:
                return row
        return None

    async def _find_uuid(self, client: httpx.AsyncClient, username: str) -> str | None:
        row = await self._find_user(client, username)
        return row.get("uuid") if row else None

    @staticmethod
    def _selected_option(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return ""
        if "selected" in value:
            selected = str(value.get("selected", "")).strip().lower()
            if selected not in {"", "0", "false", "no"}:
                return str(value.get("value", "")).strip()
        for key, option in value.items():
            if not isinstance(option, dict):
                continue
            selected = str(option.get("selected", "")).strip().lower()
            if selected not in {"", "0", "false", "no"}:
                return str(key).strip()
        return ""

    @staticmethod
    def _legacy_auth_posture(mode: str) -> OpenVpnAuthPosture:
        normalised = mode.strip().lower()
        if "tls_user" in normalised:
            return OpenVpnAuthPosture.CERT_AND_PASSWORD
        if normalised.endswith("user") or normalised in {"user", "password"}:
            return OpenVpnAuthPosture.PASSWORD_ONLY
        return OpenVpnAuthPosture.CERT_ONLY

    @classmethod
    def _instance_auth_posture(
        cls, mode: str, authmode: Any, verify_client_cert: Any
    ) -> OpenVpnAuthPosture:
        auth_value = cls._selected_option(authmode).lower()
        verify_value = cls._selected_option(verify_client_cert).lower()
        password_required = auth_value not in {"", "none", "disabled"}
        cert_required = verify_value not in {"", "0", "no", "none", "disabled"}
        if not auth_value:
            password_required = "user" in mode.lower()
        if not verify_value:
            cert_required = "tls" in mode.lower()
        if password_required and cert_required:
            return OpenVpnAuthPosture.CERT_AND_PASSWORD
        if password_required:
            return OpenVpnAuthPosture.PASSWORD_ONLY
        return OpenVpnAuthPosture.CERT_ONLY

    @staticmethod
    def _openvpn_failure(action: str, error: Exception) -> SyncResult:
        if isinstance(error, httpx.TimeoutException):
            return SyncResult(
                False,
                f"OPNsense OpenVPN {action} timed out",
                ConnectorErrorKind.TIMEOUT,
                True,
            )
        if isinstance(error, httpx.RequestError):
            return SyncResult(
                False,
                f"OPNsense OpenVPN {action} is unavailable",
                ConnectorErrorKind.UNAVAILABLE,
                True,
            )
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 403:
                return SyncResult(
                    False,
                    "OPNsense OpenVPN access requires privilege "
                    "VPN: OpenVPN: Client Export",
                    ConnectorErrorKind.AUTHENTICATION,
                )
            remote_message = ""
            try:
                payload = error.response.json()
                if isinstance(payload, dict):
                    remote_message = str(
                        payload.get("errorMessage") or payload.get("message") or ""
                    )
            except (TypeError, ValueError):
                pass
            if (
                status == 500
                and remote_message == "Certificate does not belong to server CA"
            ):
                return SyncResult(
                    False,
                    "OPNsense OpenVPN client certificate does not belong to the server CA",
                    ConnectorErrorKind.VALIDATION,
                )
            if status == 401:
                kind, retryable = ConnectorErrorKind.AUTHENTICATION, False
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
                f"OPNsense OpenVPN {action} failed with HTTP {status}",
                kind,
                retryable,
            )
        return SyncResult(
            False,
            f"OPNsense OpenVPN {action} received an invalid response",
            ConnectorErrorKind.REMOTE_REJECTED,
        )

    async def discover_openvpn(self) -> OpenVpnDiscovery | SyncResult:
        try:
            async with self._client() as client:
                providers_response = await client.get("/openvpn/export/providers")
                providers_response.raise_for_status()
                templates_response = await client.get("/openvpn/export/templates")
                templates_response.raise_for_status()
                providers_payload = providers_response.json()
                templates_payload = templates_response.json()

                if isinstance(providers_payload, dict):
                    provider_items = providers_payload.items()
                elif isinstance(providers_payload, list):
                    provider_items = enumerate(providers_payload)
                else:
                    raise ValueError("OpenVPN providers response has an invalid shape")

                servers = []
                for provider_key, raw_provider in provider_items:
                    if not isinstance(raw_provider, dict):
                        continue
                    provider = raw_provider
                    vpnid = str(provider.get("vpnid") or provider_key).strip()
                    if not vpnid:
                        continue
                    mode = self._selected_option(provider.get("mode", ""))
                    caref = self._selected_option(provider.get("caref", ""))
                    posture = self._legacy_auth_posture(mode)
                    if mode == "server_tls_user" or caref:
                        instance_response = await client.get(
                            f"/openvpn/instances/get/{vpnid}"
                        )
                        instance_response.raise_for_status()
                        instance_payload = instance_response.json()
                        if not isinstance(instance_payload, dict):
                            raise ValueError("OpenVPN instance response has an invalid shape")
                        instance = instance_payload.get("instance", {})
                        if not isinstance(instance, dict):
                            raise ValueError("OpenVPN instance response has an invalid shape")
                        caref = caref or self._selected_option(instance.get("ca", ""))
                        posture = self._instance_auth_posture(
                            mode,
                            instance.get("authmode", ""),
                            instance.get("verify_client_cert", ""),
                        )
                    servers.append(
                        OpenVpnServer(
                            vpnid=vpnid,
                            name=str(provider.get("name", "")).strip(),
                            caref=caref,
                            auth_posture=posture,
                        )
                    )

            if isinstance(templates_payload, dict):
                templates = tuple(str(key) for key in templates_payload)
            elif isinstance(templates_payload, list):
                templates = tuple(
                    str(item.get("key", item.get("name", "")))
                    if isinstance(item, dict)
                    else str(item)
                    for item in templates_payload
                )
                templates = tuple(item for item in templates if item)
            else:
                raise ValueError("OpenVPN templates response has an invalid shape")
            return OpenVpnDiscovery(tuple(servers), templates)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._openvpn_failure("discovery", error)

    @staticmethod
    async def _find_client_certificate(
        client: httpx.AsyncClient,
        username: str,
        caref: str,
        *,
        excluded_refids: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        response = await client.get("/trust/cert/search")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError("certificate search response has an invalid shape")
        return next(
            (
                row
                for row in payload["rows"]
                if isinstance(row, dict)
                and str(row.get("commonname", "")) == username
                and str(row.get("caref", "")) == caref
                and str(row.get("cert_type", ""))
                in {"usr_cert", "combined_server_client"}
                and str(row.get("refid", ""))
                and str(row.get("refid", "")) not in excluded_refids
            ),
            None,
        )

    @staticmethod
    def _revoked_refids_by_reason(crl: dict[str, Any]) -> dict[int, set[str]]:
        revoked_by_reason: dict[int, set[str]] = {}
        for reason in range(11):
            options = crl.get(f"revoked_reason_{reason}", {})
            if not isinstance(options, dict):
                raise ValueError("CRL response has an invalid reason shape")
            revoked_by_reason[reason] = {
                str(refid)
                for refid, option in options.items()
                if isinstance(option, dict)
                and str(option.get("selected", "")) == "1"
            }
        return revoked_by_reason

    @staticmethod
    async def _get_crl(
        client: httpx.AsyncClient, caref: str
    ) -> dict[str, Any]:
        response = await client.get(f"/trust/crl/get/{caref}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("crl"), dict):
            raise ValueError("CRL response has an invalid shape")
        return payload["crl"]

    async def ensure_client_certificate(
        self, username: str, *, caref: str
    ) -> str | SyncResult:
        try:
            async with self._openvpn_certificate_lock:
                async with self._client() as client:
                    crl = await self._get_crl(client, caref)
                    revoked_refids = frozenset().union(
                        *self._revoked_refids_by_reason(crl).values()
                    )
                    certificate = await self._find_client_certificate(
                        client,
                        username,
                        caref,
                        excluded_refids=revoked_refids,
                    )
                    if certificate is None:
                        response = await client.post(
                            "/trust/cert/add",
                            json={
                                "cert": {
                                    "action": "internal",
                                    "caref": caref,
                                    "cert_type": "usr_cert",
                                    "commonname": username,
                                    "descr": f"na-sso {username} {self.target_id}",
                                    "key_type": "2048",
                                    "digest": "sha256",
                                    "lifetime": 397,
                                    "private_key_location": "firewall",
                                    "country": "NL",
                                }
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict) or payload.get("result") not in {
                            "saved",
                            "ok",
                        }:
                            raise ValueError("certificate creation was rejected")
                        certificate = await self._find_client_certificate(
                            client,
                            username,
                            caref,
                            excluded_refids=revoked_refids,
                        )
                    if certificate is None:
                        raise ValueError("created certificate was not found")
                    refid = str(certificate["refid"])
                    self._openvpn_client_certificates[refid] = (username, caref)
                    return refid
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._openvpn_failure("client certificate provisioning", error)

    async def _add_client_certificate_to_crl(
        self,
        client: httpx.AsyncClient,
        *,
        certificate: dict[str, Any],
        caref: str,
    ) -> SyncResult:
        try:
            crl = await self._get_crl(client, caref)
            revoked_by_reason = self._revoked_refids_by_reason(crl)
            revoked_by_reason[0].add(str(certificate["refid"]))
            descr = self._selected_option(crl.get("descr", ""))
            lifetime = str(crl.get("lifetime") or "9999").strip() or "9999"
            response = await client.post(
                f"/trust/crl/set/{caref}",
                json={
                    "crl": {
                        "crlmethod": "internal",
                        "descr": descr or f"na-sso {self.target_id}",
                        "lifetime": lifetime,
                        **{
                            f"revoked_reason_{reason}": ",".join(
                                sorted(revoked_by_reason[reason])
                            )
                            for reason in range(11)
                        },
                    }
                },
            )
            response.raise_for_status()
            if response.content:
                result = response.json()
                if isinstance(result, dict) and result.get("result") in {
                    "failed",
                    "error",
                }:
                    raise ValueError("CRL update was rejected")
            return SyncResult(True, "CRL updated")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._openvpn_failure("certificate revocation list update", error)

    @staticmethod
    def _certificate_delete_failure(
        username: str, failure: SyncResult
    ) -> SyncResult:
        return SyncResult(
            False,
            f"OPNsense OpenVPN client certificate for {username} "
            f"could not be deleted: {failure.detail}",
            failure.error_kind,
            failure.retryable,
        )

    def _record_certificate_revocation(
        self, username: str, *, crl_updated: bool
    ) -> None:
        from na_sso.audit import record_audit
        from na_sso.db import get_session

        try:
            with get_session() as db:
                record_audit(
                    db,
                    "system",
                    "openvpn.certificate_revoked",
                    username,
                    f"target={self.target_id}; "
                    f"crl={'updated' if crl_updated else 'update_failed'}",
                )
                db.commit()
        except (OSError, SQLAlchemyError):
            logger.exception(
                "Could not record OpenVPN certificate revocation audit for %s on %s",
                username,
                self.target_id,
            )

    async def revoke_client_certificate(
        self, username: str, *, caref: str
    ) -> SyncResult:
        try:
            async with self._openvpn_certificate_lock:
                async with self._client() as client:
                    certificate = await self._find_client_certificate(
                        client, username, caref
                    )
                    if certificate is None:
                        return SyncResult(True, "already absent")

                    crl_result = await self._add_client_certificate_to_crl(
                        client, certificate=certificate, caref=caref
                    )
                    if crl_result.ok:
                        self._openvpn_client_certificates.pop(
                            str(certificate["refid"]), None
                        )
                        self._record_certificate_revocation(
                            username, crl_updated=True
                        )
                        return SyncResult(True, "revoked via CRL")

                    logger.warning(
                        "%s for certificate %s on target %s; falling back to delete",
                        crl_result.detail,
                        username,
                        self.target_id,
                    )

                    try:
                        certificate_uuid = str(certificate["uuid"])
                        response = await client.post(
                            f"/trust/cert/del/{certificate_uuid}", json={}
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict) or payload.get("result") != "deleted":
                            raise ValueError("certificate deletion was rejected")
                    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                        failure = self._openvpn_failure(
                            "client certificate deletion", error
                        )
                        return self._certificate_delete_failure(username, failure)

                    self._openvpn_client_certificates.pop(
                        str(certificate["refid"]), None
                    )
                    self._record_certificate_revocation(
                        username, crl_updated=False
                    )
                    return SyncResult(
                        True,
                        "CRL unavailable; revoked by deletion; "
                        "already-distributed profiles may remain valid",
                    )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            failure = self._openvpn_failure("client certificate deletion", error)
            return self._certificate_delete_failure(username, failure)

    @staticmethod
    def _openvpn_export_body(template: str, hostname: str) -> dict[str, Any]:
        return {"openvpn_export": {"template": template, "hostname": hostname}}

    async def export_config(
        self,
        vpnid: str,
        *,
        template: str,
        hostname: str,
        username: str | None = None,
        certref: str | None = None,
    ) -> ExportedConfig | SyncResult:
        if certref is not None:
            trusted = self._openvpn_client_certificates.get(certref)
            if username is None or trusted is None or trusted[0] != username:
                return SyncResult(
                    False,
                    "OPNsense OpenVPN export requires a connector-issued client certificate",
                    ConnectorErrorKind.VALIDATION,
                )
            path = f"/openvpn/export/download/{vpnid}/{certref}"
        else:
            path = f"/openvpn/export/download/{vpnid}"
        try:
            async with self._client() as client:
                response = await client.post(
                    path,
                    json=self._openvpn_export_body(template, hostname),
                )
                response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("result") not in {"ok", "saved"}:
                raise ValueError("OpenVPN export was rejected")
            filename = payload.get("filename")
            content = payload.get("content")
            if not isinstance(filename, str) or not isinstance(content, str):
                raise ValueError("OpenVPN export response has an invalid shape")
            return ExportedConfig(
                filename=filename,
                content=base64.b64decode(content, validate=True),
            )
        except (binascii.Error, httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._openvpn_failure("client export", error)

    async def validate_openvpn_export(
        self, vpnid: str, *, template: str, hostname: str
    ) -> SyncResult:
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/openvpn/export/validate_presets/{vpnid}",
                    json=self._openvpn_export_body(template, hostname),
                )
                response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("result") == "failed":
                return SyncResult(
                    False,
                    "OPNsense OpenVPN export preset is invalid",
                    ConnectorErrorKind.VALIDATION,
                )
            return SyncResult(True, "valid")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._openvpn_failure("preset validation", error)

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        return await self.inspect_user_for_assignment(user, frozenset(self._groups))

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with self._client() as client:
                response = await client.post("/auth/user/search", json={"rowCount": -1})
                response.raise_for_status()
            accounts = tuple(RemoteAccount(
                username=str(row.get("name", "")),
                display_name=str(row.get("descr", "")),
                email=str(row.get("email", "")),
                status="disabled" if str(row.get("disabled", "0")).lower() in {"1", "true", "yes"} else "active",
            ) for row in response.json().get("rows", []) if row.get("name"))
            return AccountDiscovery(True, accounts)
        except (httpx.HTTPError, ValueError, TypeError):
            return AccountDiscovery(True, detail="OPNsense account discovery failed.")

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        try:
            async with self._client() as client:
                row = await self._find_user(client, user.username)
            actual_memberships: frozenset[str] | None = None
            if row is not None:
                raw_memberships = row.get("group_memberships")
                if isinstance(raw_memberships, str):
                    actual_memberships = frozenset(item.strip() for item in raw_memberships.split(",") if item.strip())
                elif isinstance(raw_memberships, list):
                    actual_memberships = frozenset(str(item) for item in raw_memberships)
                disabled = row.get("disabled")
                status = None if disabled is None else ("disabled" if str(disabled).lower() in {"1", "true", "yes"} else "active")
            else:
                status = None
            snapshot = RemoteIdentitySnapshot(
                present=row is not None,
                username=str(row.get("name")) if row else None,
                display_name=str(row.get("descr")) if row and row.get("descr") is not None else None,
                email=str(row.get("email")) if row and row.get("email") is not None else None,
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
                detail="OPNsense identity read failed.",
                required_memberships=memberships,
            )

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        return await self.ensure_user_for_assignment(user, password, frozenset(self._groups))

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        try:
            async with self._client() as client:
                uuid = await self._find_uuid(client, user.username)
                payload: dict = {
                    "user": {
                        "name": user.username,
                        "descr": user.display_name or user.username,
                        "email": user.email,
                        "disabled": "1" if user.status == "disabled" else "0",
                        "group_memberships": ",".join(sorted(memberships)),
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
        identity_result = await self.ensure_user(user, None)
        return await self._offboard_openvpn(user, identity_result)

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        identity_result = await self.ensure_user_for_assignment(
            user, None, memberships
        )
        return await self._offboard_openvpn(user, identity_result)

    def _configured_openvpn_vpnid(self) -> str | None:
        from na_sso.db import get_session
        from na_sso.models import TargetOpenvpnConfig

        try:
            with get_session() as db:
                config = db.query(TargetOpenvpnConfig).filter_by(
                    target_id=self.target_id,
                    enabled=True,
                ).one_or_none()
                if config is None or config.verified_at is None:
                    return None
                return config.vpnid
        except (OSError, SQLAlchemyError):
            logger.exception(
                "Could not read OpenVPN offboarding configuration for target %s",
                self.target_id,
            )
            return None

    async def _offboard_openvpn(
        self, user: ManagedUser, identity_result: SyncResult
    ) -> SyncResult:
        vpnid = self._configured_openvpn_vpnid()
        if vpnid is None:
            return identity_result

        discovery = await self.discover_openvpn()
        if not isinstance(discovery, OpenVpnDiscovery):
            revocation_result = SyncResult(
                False,
                f"OPNsense OpenVPN client certificate for {user.username} "
                f"could not be revoked: {discovery.detail}",
                discovery.error_kind,
                discovery.retryable,
            )
        else:
            server = next(
                (item for item in discovery.servers if item.vpnid == vpnid), None
            )
            if server is None or not server.caref:
                revocation_result = SyncResult(
                    False,
                    f"OPNsense OpenVPN client certificate for {user.username} "
                    "could not be revoked: configured server CA was not found",
                    ConnectorErrorKind.VALIDATION,
                )
            else:
                revocation_result = await self.revoke_client_certificate(
                    user.username, caref=server.caref
                )

        if not revocation_result.ok:
            if identity_result.ok:
                return revocation_result
            return SyncResult(
                False,
                f"{identity_result.detail}; {revocation_result.detail}",
                identity_result.error_kind or revocation_result.error_kind,
                identity_result.retryable or revocation_result.retryable,
            )
        if identity_result.ok and revocation_result.detail.startswith(
            "CRL unavailable"
        ):
            return SyncResult(
                True,
                f"{identity_result.detail}; {revocation_result.detail}",
            )
        return identity_result

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with self._client() as client:
                uuid = await self._find_uuid(client, user.username)
                if not uuid:
                    identity_result = SyncResult(True, "already absent")
                else:
                    r = await client.post(f"/auth/user/del/{uuid}", json={})
                    r.raise_for_status()
                    if r.json().get("result") != "deleted":
                        identity_result = SyncResult(
                            False, f"opnsense rejected delete: {r.text}"
                        )
                    else:
                        identity_result = SyncResult(True, "deleted")
        except httpx.HTTPError as e:
            identity_result = SyncResult(False, f"opnsense http error: {e}")
        return await self._offboard_openvpn(user, identity_result)

    async def probe(self) -> SyncResult:
        try:
            async with self._client() as client:
                r = await client.post("/auth/user/search", json={"rowCount": 1})
                r.raise_for_status()
                return SyncResult(True, "reachable")
        except httpx.HTTPError as e:
            return SyncResult(False, f"opnsense unreachable: {e}")
