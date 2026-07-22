from __future__ import annotations

import base64
import hashlib
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


TARGET_LABELS = {
    "opnsense": "OPNsense",
    "nexus": "Nexus Repository",
    "nextcloud": "Nextcloud",
    "jenkins": "Jenkins",
    "gitlab": "GitLab",
    "gitea": "Gitea",
    "immich": "Immich",
    "npm": "Nginx Proxy Manager",
}


def _npm_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initial_npm_users() -> dict[int, dict[str, Any]]:
    email = os.getenv("MOCK_NPM_USERNAME", "admin@example.test").strip().lower()
    timestamp = _npm_timestamp()
    return {
        1: {
            "id": 1,
            "created_on": timestamp,
            "modified_on": timestamp,
            "is_disabled": False,
            "is_deleted": False,
            "email": email,
            "name": "Administrator",
            "nickname": "Admin",
            "roles": ["admin"],
            "password": os.getenv("MOCK_NPM_PASSWORD", "demo-password"),
        }
    }


OPNSENSE_CA_REF = "6a5fdc1533f7f"
OPNSENSE_SERVER_CERT_REF = "6a5fdc35d15a1"
OPNSENSE_SERVER_UUID = "1c030500-62d0-4b62-b3d2-d6a953bad087"


def _pem_private_key(key: Any) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _pem_certificate(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _certificate_record(
    *,
    refid: str,
    descr: str,
    caref: str,
    cert_type: str,
    commonname: str,
    certificate: x509.Certificate,
    private_key: Any,
) -> dict[str, Any]:
    return {
        "uuid": str(uuid4()),
        "refid": refid,
        "descr": descr,
        "caref": caref,
        "cert_type": cert_type,
        "commonname": commonname,
        "crt_payload": _pem_certificate(certificate),
        "prv_payload": _pem_private_key(private_key),
        "valid_from": certificate.not_valid_before_utc.isoformat(),
        "valid_to": certificate.not_valid_after_utc.isoformat(),
    }


def _seed_opnsense_openvpn(mock_state: MockState) -> None:
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "NL"),
            x509.NameAttribute(NameOID.COMMON_NAME, "na-sso demo VPN CA"),
        ]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    mock_state.opnsense_cas[OPNSENSE_CA_REF] = {
        "refid": OPNSENSE_CA_REF,
        "descr": "na-sso demo VPN CA",
        "certificate": ca_cert,
        "private_key": ca_key,
        "crt_payload": _pem_certificate(ca_cert),
    }

    server_key = ec.generate_private_key(ec.SECP256R1())
    server_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "NL"),
            x509.NameAttribute(NameOID.COMMON_NAME, "na-sso-demo-vpn-server"),
        ]
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    mock_state.opnsense_certs[OPNSENSE_SERVER_CERT_REF] = _certificate_record(
        refid=OPNSENSE_SERVER_CERT_REF,
        descr="na-sso demo VPN server",
        caref=OPNSENSE_CA_REF,
        cert_type="server_cert",
        commonname="na-sso-demo-vpn-server",
        certificate=server_cert,
        private_key=server_key,
    )
    mock_state.opnsense_openvpn_servers[OPNSENSE_SERVER_UUID] = {
        "uuid": OPNSENSE_SERVER_UUID,
        "vpnid": "1",
        "description": "na-sso demo VPN",
        "enabled": "1",
        "role": "server",
        "authmode": "Local Database",
        "proto": "udp",
        "port": "1194",
        "caref": OPNSENSE_CA_REF,
        "certref": OPNSENSE_SERVER_CERT_REF,
        "hostname": "vpn.demo.lan",
    }


@dataclass
class MockState:
    opnsense: dict[str, dict[str, Any]] = field(default_factory=dict)
    opnsense_cas: dict[str, dict[str, Any]] = field(default_factory=dict)
    opnsense_certs: dict[str, dict[str, Any]] = field(default_factory=dict)
    opnsense_crls: dict[str, dict[str, Any]] = field(default_factory=dict)
    opnsense_openvpn_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    nexus: dict[str, dict[str, Any]] = field(default_factory=dict)
    nextcloud: dict[str, dict[str, Any]] = field(default_factory=dict)
    jenkins: dict[str, dict[str, Any]] = field(default_factory=dict)
    gitlab: dict[str, dict[str, Any]] = field(default_factory=dict)
    gitea: dict[str, dict[str, Any]] = field(default_factory=dict)
    immich: dict[str, dict[str, Any]] = field(default_factory=dict)
    npm: dict[int, dict[str, Any]] = field(default_factory=_initial_npm_users)
    npm_tokens: dict[str, int] = field(default_factory=dict)
    fail_next: set[str] = field(default_factory=set)
    available: dict[str, bool] = field(
        default_factory=lambda: {target: True for target in TARGET_LABELS}
    )

    def __post_init__(self) -> None:
        _seed_opnsense_openvpn(self)

    def reset(self) -> None:
        self.opnsense.clear()
        self.opnsense_cas.clear()
        self.opnsense_certs.clear()
        self.opnsense_crls.clear()
        self.opnsense_openvpn_servers.clear()
        self.nexus.clear()
        self.nextcloud.clear()
        self.jenkins.clear()
        self.gitlab.clear()
        self.gitea.clear()
        self.immich.clear()
        self.npm = _initial_npm_users()
        self.npm_tokens.clear()
        self.fail_next.clear()
        self.available = {target: True for target in TARGET_LABELS}
        _seed_opnsense_openvpn(self)


state = MockState()
app = FastAPI(title="NA-SSO mock targets", docs_url=None, redoc_url=None)
static_root = Path(__file__).resolve().parents[1] / "static"
app.mount("/design", StaticFiles(directory=static_root / "design"), name="mock-design")
app.mount("/static", StaticFiles(directory=static_root), name="mock-static")


class NpmAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message


class OPNsenseAPIError(Exception):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload


@app.exception_handler(NpmAPIError)
async def npm_api_error(_request: Request, exc: NpmAPIError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": exc.status_code, "message": exc.message}},
        status_code=exc.status_code,
    )


@app.exception_handler(OPNsenseAPIError)
async def opnsense_api_error(_request: Request, exc: OPNsenseAPIError) -> JSONResponse:
    return JSONResponse(exc.payload, status_code=exc.status_code)


def _credentials(prefix: str, username_default: str, password_default: str) -> tuple[str, str]:
    return (
        os.getenv(f"MOCK_{prefix}_USERNAME", username_default),
        os.getenv(f"MOCK_{prefix}_PASSWORD", password_default),
    )


def _require_basic(request: Request, credentials: tuple[str, str]) -> None:
    auth = request.headers.get("authorization", "")
    expected = "Basic " + base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
    if auth != expected:
        raise HTTPException(status_code=401, detail="invalid demo credentials")


def _require_opnsense_openvpn(request: Request) -> None:
    forbidden_credentials = _credentials(
        "OPNSENSE_FORBIDDEN", "forbidden-key", "forbidden-secret"
    )
    forbidden_auth = "Basic " + base64.b64encode(
        f"{forbidden_credentials[0]}:{forbidden_credentials[1]}".encode()
    ).decode()
    if request.headers.get("authorization", "") == forbidden_auth:
        raise OPNsenseAPIError(403, {"status": 403, "message": "Forbidden"})
    _require_basic(request, _credentials("OPNSENSE", "demo-key", "demo-secret"))
    for injection_name in ("opnsense-forbidden", "opnsense_forbidden"):
        if injection_name in state.fail_next:
            state.fail_next.remove(injection_name)
            raise OPNsenseAPIError(403, {"status": 403, "message": "Forbidden"})
    _maybe_fail("opnsense")


def _require_header(request: Request, name: str, expected: str) -> None:
    if request.headers.get(name, "") != expected:
        raise HTTPException(status_code=401, detail="invalid demo credentials")


def _maybe_fail(target: str) -> None:
    if target in state.fail_next:
        state.fail_next.remove(target)
        if target == "npm":
            raise NpmAPIError(503, "injected npm failure")
        raise HTTPException(status_code=503, detail=f"injected {target} failure")
    if not state.available[target]:
        if target == "npm":
            raise NpmAPIError(503, "npm is toggled unavailable")
        raise HTTPException(status_code=503, detail=f"{target} is toggled unavailable")


def _ocs(code: int = 100, message: str = "OK", data: Any = None) -> dict[str, Any]:
    return {
        "ocs": {
            "meta": {"status": "ok" if code == 100 else "failure", "statuscode": code, "message": message},
            "data": {} if data is None else data,
        }
    }


def _npm_avatar(email: str) -> str:
    digest = hashlib.md5(email.strip().lower().encode(), usedforsecurity=False).hexdigest()
    return f"https://www.gravatar.com/avatar/{digest}?d=mm"


def _npm_public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "created_on": user["created_on"],
        "modified_on": user["modified_on"],
        "is_disabled": bool(user["is_disabled"]),
        "email": user["email"],
        "name": user["name"],
        "nickname": user["nickname"],
        "avatar": _npm_avatar(user["email"]),
        "roles": list(user["roles"]),
    }


def _require_npm(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not token or token not in state.npm_tokens:
        raise NpmAPIError(401, "Invalid token")
    _maybe_fail("npm")


def _npm_user(user_id: int) -> dict[str, Any]:
    user = state.npm.get(user_id)
    if user is None or user["is_deleted"]:
        raise NpmAPIError(404, "Not Found")
    return user


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def control_page() -> str:
    cards = "".join(
        f"""<article class=\"card stack-2\"><div class=\"card-meta\">API availability</div>
        <h2 class=\"card-title\">{TARGET_LABELS[target]}</h2>
        <p><span class=\"badge {'badge-shipped' if available else 'badge-danger'}\">{'Reply success' if available else 'Reply failure'}</span></p>
        <form method=\"post\" action=\"/__mock__/availability/{target}\">
        <input type=\"hidden\" name=\"available\" value=\"{'false' if available else 'true'}\">
        <button class=\"btn btn-secondary btn-sm\" type=\"submit\">Switch to {'failure' if available else 'success'}</button></form></article>"""
        for target, available in state.available.items()
    )
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>NA-SSO mock controls</title>
    <link rel=\"icon\" href=\"/static/favicon.svg\" type=\"image/svg+xml\">
    <link rel=\"icon\" href=\"/static/favicon.ico\" sizes=\"32x32\">
    <link rel=\"apple-touch-icon\" href=\"/static/apple-touch-icon.png\">
    <link rel=\"stylesheet\" href=\"/design/styles.css\">
    <link rel=\"stylesheet\" href=\"/design/components.css\"></head>
    <body class=\"site-page\"><main class=\"section container stack-3\">
    <div class=\"section-head\"><div class=\"kicker\">// Disposable demo</div><h1 class=\"section-title\">Mock target controls</h1>
    <p class=\"lead\">Each switch applies to every API request for that target.</p></div>
    <section class=\"grid-cards\">{cards}</section></main></body></html>"""


@app.post("/__mock__/availability/{target}")
async def set_availability(target: str, available: bool = Form(...)) -> RedirectResponse:
    if target not in state.available:
        raise HTTPException(status_code=404, detail="unknown target")
    state.available[target] = available
    return RedirectResponse("/", status_code=303)


@app.post("/__mock__/reset")
async def reset() -> dict[str, str]:
    state.reset()
    return {"status": "reset"}


@app.post("/__mock__/fail/{target}")
async def fail_next(target: str) -> dict[str, str]:
    if target not in state.available and target not in {
        "opnsense-forbidden",
        "opnsense_forbidden",
    }:
        raise HTTPException(status_code=404, detail="unknown target")
    state.fail_next.add(target)
    return {"status": "armed", "target": target}


# Nginx Proxy Manager v2.15.1 Users API
@app.post("/api/tokens")
async def npm_token(request: Request) -> dict[str, str]:
    _maybe_fail("npm")
    payload = dict(await request.json())
    identity = str(payload.get("identity", "")).strip().lower()
    secret = str(payload.get("secret", ""))
    user = next(
        (
            item
            for item in state.npm.values()
            if item["email"].strip().lower() == identity
            and not item["is_disabled"]
            and not item["is_deleted"]
        ),
        None,
    )
    if user is None or user.get("password") != secret:
        raise NpmAPIError(400, "Invalid email or password")
    token = uuid4().hex
    state.npm_tokens[token] = user["id"]
    return {
        "token": token,
        "expires": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }


@app.get("/api/users")
async def npm_users(request: Request, query: str = "") -> list[dict[str, Any]]:
    _require_npm(request)
    lowered_query = query.lower()
    return [
        _npm_public_user(user)
        for user in state.npm.values()
        if not user["is_deleted"]
        and (
            not lowered_query
            or lowered_query in user["name"].lower()
            or lowered_query in user["email"].lower()
        )
    ]


@app.get("/api/users/{user_id}")
async def npm_get_user(user_id: int, request: Request) -> dict[str, Any]:
    _require_npm(request)
    return _npm_public_user(_npm_user(user_id))


@app.post("/api/users", status_code=201)
async def npm_add_user(request: Request) -> JSONResponse:
    _require_npm(request)
    payload = dict(await request.json())
    for field_name in ("name", "email", "nickname"):
        if not isinstance(payload.get(field_name), str) or not payload[field_name]:
            raise NpmAPIError(400, f"{field_name} is required")

    auth = payload.get("auth")
    password: str | None = None
    if auth is not None:
        if not isinstance(auth, dict) or auth.get("type") != "password":
            raise NpmAPIError(400, "Invalid authentication type")
        if not isinstance(auth.get("secret"), str):
            raise NpmAPIError(400, "secret is required")
        password = auth["secret"]

    user_id = max(state.npm, default=0) + 1
    timestamp = _npm_timestamp()
    user = {
        "id": user_id,
        "created_on": timestamp,
        "modified_on": timestamp,
        "is_disabled": bool(payload.get("is_disabled", False)),
        "is_deleted": False,
        "email": payload["email"],
        "name": payload["name"],
        "nickname": payload["nickname"],
        "roles": list(payload.get("roles", [])),
        "password": password,
    }
    state.npm[user_id] = user
    return JSONResponse(_npm_public_user(user), status_code=201)


@app.put("/api/users/{user_id}")
async def npm_set_user(user_id: int, request: Request) -> dict[str, Any]:
    _require_npm(request)
    user = _npm_user(user_id)
    payload = dict(await request.json())
    if "email" in payload:
        email = str(payload["email"])
        duplicate = next(
            (
                item
                for item in state.npm.values()
                if item["id"] != user_id
                and not item["is_deleted"]
                and item["email"].lower() == email.lower()
            ),
            None,
        )
        if duplicate is not None:
            raise NpmAPIError(400, f"Email address already in use - {email}")
    for field_name in ("email", "name", "nickname", "is_disabled"):
        if field_name in payload:
            user[field_name] = (
                bool(payload[field_name])
                if field_name == "is_disabled"
                else str(payload[field_name])
            )
    user["modified_on"] = _npm_timestamp()
    return _npm_public_user(user)


@app.delete("/api/users/{user_id}")
async def npm_delete_user(user_id: int, request: Request) -> dict[str, Any]:
    _require_npm(request)
    user = _npm_user(user_id)
    user["is_deleted"] = True
    user["modified_on"] = _npm_timestamp()
    return _npm_public_user(user)


@app.put("/api/users/{user_id}/auth")
async def npm_set_password(user_id: int, request: Request) -> bool:
    _require_npm(request)
    user = _npm_user(user_id)
    payload = dict(await request.json())
    secret = payload.get("secret")
    if (
        payload.get("type") != "password"
        or not isinstance(secret, str)
        or not 8 <= len(secret) <= 64
    ):
        raise NpmAPIError(400, "Password must be between 8 and 64 characters")
    user["password"] = secret
    return True


def _option_field(
    values: list[tuple[str, str]],
    selected: str,
    groups: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, label in values:
        item: dict[str, Any] = {"selected": int(key == selected), "value": label}
        if groups is not None and key in groups:
            item["optgroup"] = groups[key]
        result[key] = item
    return result


def _option_list(values: list[str], selected: int) -> list[dict[str, Any]]:
    return [
        {"selected": int(index == selected), "value": value}
        for index, value in enumerate(values)
    ]


def _openvpn_instance(server: dict[str, Any]) -> dict[str, Any]:
    auth_values = [
        ("", "OpenVPN default"),
        ("BLAKE2b512", "BLAKE2b512 (512-bit)"),
        ("BLAKE2s256", "BLAKE2s256 (256-bit)"),
        ("MD4", "MD4 (128-bit)"),
        ("MD5", "MD5 (128-bit)"),
        ("MD5-SHA1", "MD5-SHA1 (288-bit)"),
        ("RIPEMD160", "RIPEMD160 (160-bit)"),
        ("SHA1", "SHA1 (160-bit)"),
        ("SHA224", "SHA224 (224-bit)"),
        ("SHA256", "SHA256 (256-bit)"),
        ("SHA3-224", "SHA3-224 (224-bit)"),
        ("SHA3-256", "SHA3-256 (256-bit)"),
        ("SHA3-384", "SHA3-384 (384-bit)"),
        ("SHA3-512", "SHA3-512 (512-bit)"),
        ("SHA384", "SHA384 (384-bit)"),
        ("SHA512", "SHA512 (512-bit)"),
        ("SHA512-224", "SHA512-224 (224-bit)"),
        ("SHA512-256", "SHA512-256 (256-bit)"),
        ("SHAKE128", "SHAKE128 (128-bit)"),
        ("SHAKE256", "SHAKE256 (256-bit)"),
        ("none", "None (No Authentication)"),
        ("whirlpool", "whirlpool (512-bit)"),
    ]
    cipher_names = [
        "AES-128-CBC",
        "AES-128-CFB",
        "AES-128-CFB1",
        "AES-128-CFB8",
        "AES-128-GCM",
        "AES-128-OFB",
        "AES-192-CBC",
        "AES-192-CFB",
        "AES-192-CFB1",
        "AES-192-CFB8",
        "AES-192-GCM",
        "AES-192-OFB",
        "AES-256-CBC",
        "AES-256-CFB",
        "AES-256-CFB1",
        "AES-256-CFB8",
        "AES-256-GCM",
        "AES-256-OFB",
        "CHACHA20-POLY1305",
    ]
    cipher_groups = {
        name: "Recommended" if name.endswith("-GCM") or name == "CHACHA20-POLY1305" else "Legacy"
        for name in cipher_names
    }
    cipher_values = [(name, name) for name in cipher_names]
    ca = state.opnsense_cas[server["caref"]]
    server_cert = state.opnsense_certs[server["certref"]]
    blank = {"": {"selected": 1, "value": ""}}
    return {
        "auth": _option_field(auth_values, ""),
        "auth-gen-token": "",
        "auth-gen-token-renewal": "",
        "auth-gen-token-secret": "",
        "authmode": _option_field([("Local Database", "Local Database")], server["authmode"]),
        "bridge_gateway": "",
        "bridge_pool": "",
        "ca": _option_field(
            [("", " - Use from certificate"), (ca["refid"], ca["descr"])],
            ca["refid"],
        ),
        "carp_depend_on": _option_field([("", "None")], ""),
        "cert": _option_field(
            [
                ("", "None"),
                ("6a5b3e90e61c2", "Web GUI TLS certificate"),
                (server_cert["refid"], server_cert["descr"]),
            ],
            server_cert["refid"],
        ),
        "cert_depth": _option_field(
            [
                ("", "Do Not Check"),
                ("1", "One (Client+Server)"),
                ("2", "Two (Client+Intermediate+Server)"),
                ("3", "Three (Client+2xIntermediate+Server)"),
                ("4", "Four (Client+3xIntermediate+Server)"),
                ("5", "Five (Client+4xIntermediate+Server)"),
            ],
            "",
        ),
        "compress_migrate": "0",
        "crl": _option_field([("", "None")], ""),
        "data-ciphers": _option_field(cipher_values, "", cipher_groups),
        "data-ciphers-fallback": _option_field(
            [("", "None"), *cipher_values], "", cipher_groups
        ),
        "description": server["description"],
        "dev_type": _option_field([("ovpn", "DCO"), ("tap", "TAP"), ("tun", "TUN")], "tun"),
        "dns_domain": deepcopy(blank),
        "dns_domain_search": deepcopy(blank),
        "dns_servers": deepcopy(blank),
        "enabled": server["enabled"],
        "fragment": "",
        "http-proxy": "",
        "ifconfig-pool-persist": "0",
        "keepalive_interval": "",
        "keepalive_timeout": "",
        "local": "",
        "local_group": _option_field([("", "None"), ("1999", "admins")], ""),
        "maxclients": "",
        "mssfix": "0",
        "nopool": "0",
        "ntp_servers": deepcopy(blank),
        "password": "",
        "port": server["port"],
        "port-share": "",
        "proto": _option_field(
            [
                ("tcp", "TCP"),
                ("tcp4", "TCP (IPv4)"),
                ("tcp6", "TCP (IPv6)"),
                ("udp", "UDP"),
                ("udp4", "UDP (IPv4)"),
                ("udp6", "UDP (IPv6)"),
            ],
            server["proto"],
        ),
        "provision_exclusive": "0",
        "push_excluded_routes": deepcopy(blank),
        "push_inactive": "",
        "push_route": deepcopy(blank),
        "redirect_gateway": _option_field(
            [
                ("!ipv4", "not ipv4 (default)"),
                ("autolocal", "autolocal"),
                ("block-local", "block-local"),
                ("bypass-dhcp", "bypass-dhcp"),
                ("bypass-dns", "bypass-dns"),
                ("def1", "default"),
                ("ipv6", "ipv6 (default)"),
                ("local", "local"),
            ],
            "",
        ),
        "register_dns": "0",
        "remote": deepcopy(blank),
        "remote_cert_tls": "0",
        "reneg-sec": "",
        "role": _option_field([("client", "Client"), ("server", "Server")], server["role"]),
        "route": deepcopy(blank),
        "route_metric": "",
        "server": "10.19.47.0/24",
        "server_ipv6": "",
        "strictusercn": _option_list(["No", "Yes", "Yes (case insensitive)"], 0),
        "tls_key": _option_field([("", "None")], ""),
        "topology": _option_field([("net30", "net30"), ("p2p", "p2p"), ("subnet", "subnet")], "subnet"),
        "tun_mtu": "",
        "use_ocsp": "0",
        "username": "",
        "username_as_common_name": "0",
        "various_flags": _option_field(
            [(name, name) for name in [
                "block-ipv6", "client-to-client", "duplicate-cn", "explicit-exit-notify",
                "fast-io", "float", "passtos", "persist-remote-ip", "remote-random",
                "route-noexec", "route-nopull",
            ]],
            "",
        ),
        "various_push_flags": _option_field(
            [(name, name) for name in [
                "block-ipv6", "block-outside-dns", "explicit-exit-notify", "register-dns",
            ]],
            "",
        ),
        "verb": _option_list(
            [
                "0 (No output except fatal errors.)",
                "1 (Normal)",
                "2 (Normal)",
                "3 (Normal)",
                "4 (Normal)",
                "5 (log packets)",
                "6 (debug)",
                "7 (debug)",
                "8 (debug)",
                "9 (debug)",
                "10 (debug)",
                "11 (debug)",
            ],
            3,
        ),
        "verify-x509-name": "",
        "verify_client_cert": _option_field([("none", "none"), ("require", "require")], "require"),
        "vpnid": server["vpnid"],
    }


def _new_certificate_key(key_type: str) -> Any:
    if key_type in {"prime256v1", "secp384r1", "secp521r1"}:
        curve = {
            "prime256v1": ec.SECP256R1(),
            "secp384r1": ec.SECP384R1(),
            "secp521r1": ec.SECP521R1(),
        }[key_type]
        return ec.generate_private_key(curve)
    match = re.search(r"(\d+)$", key_type)
    key_size = int(match.group(1)) if match else 2048
    return rsa.generate_private_key(public_exponent=65537, key_size=max(1024, key_size))


def _certificate_subject(cert_data: dict[str, Any]) -> x509.Name:
    attributes: list[x509.NameAttribute] = []
    for field_name, oid in (
        ("country", NameOID.COUNTRY_NAME),
        ("state", NameOID.STATE_OR_PROVINCE_NAME),
        ("city", NameOID.LOCALITY_NAME),
        ("organization", NameOID.ORGANIZATION_NAME),
        ("organizationalunit", NameOID.ORGANIZATIONAL_UNIT_NAME),
        ("email", NameOID.EMAIL_ADDRESS),
        ("commonname", NameOID.COMMON_NAME),
    ):
        value = str(cert_data.get(field_name, "")).strip()
        if value:
            attributes.append(x509.NameAttribute(oid, value))
    return x509.Name(attributes)


def _certificate_search_row(cert: dict[str, Any]) -> dict[str, Any]:
    return {
        key: cert[key]
        for key in (
            "uuid",
            "refid",
            "descr",
            "caref",
            "cert_type",
            "commonname",
            "valid_from",
            "valid_to",
        )
    }


# OPNsense Trust and OpenVPN APIs
@app.post("/api/trust/cert/add")
async def opnsense_cert_add(request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    cert_data = dict((await request.json()).get("cert", {}))
    caref = str(cert_data.get("caref", ""))
    commonname = str(cert_data.get("commonname", "")).strip()
    descr = str(cert_data.get("descr", "")).strip()
    if caref not in state.opnsense_cas or not commonname or not descr:
        return {"result": "failed"}

    ca = state.opnsense_cas[caref]
    key = _new_certificate_key(str(cert_data.get("key_type", "2048")))
    now = datetime.now(timezone.utc)
    cert_type = str(cert_data.get("cert_type", "usr_cert"))
    usages = []
    if cert_type in {"usr_cert", "combined_server_client"}:
        usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    if cert_type in {"server_cert", "combined_server_client"}:
        usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_certificate_subject(cert_data))
        .issuer_name(ca["certificate"].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=int(cert_data.get("lifetime", 397))))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if usages:
        builder = builder.add_extension(x509.ExtendedKeyUsage(usages), critical=False)
    digest = {
        "sha1": hashes.SHA1(),
        "sha224": hashes.SHA224(),
        "sha256": hashes.SHA256(),
        "sha384": hashes.SHA384(),
        "sha512": hashes.SHA512(),
    }.get(str(cert_data.get("digest", "sha256")), hashes.SHA256())
    certificate = builder.sign(ca["private_key"], digest)
    refid = uuid4().hex[:13]
    record = _certificate_record(
        refid=refid,
        descr=descr,
        caref=caref,
        cert_type=cert_type,
        commonname=commonname,
        certificate=certificate,
        private_key=key,
    )
    state.opnsense_certs[refid] = record
    response: dict[str, Any] = {"result": "saved", "uuid": record["uuid"]}
    if cert_data.get("private_key_location") == "local":
        response["private_key"] = record["prv_payload"]
        record["prv_payload"] = ""
    return response


@app.post("/api/trust/cert/del/{cert_uuid}")
async def opnsense_cert_delete(cert_uuid: str, request: Request) -> dict[str, str]:
    _require_opnsense_openvpn(request)
    if "opnsense-cert-delete" in state.fail_next:
        state.fail_next.remove("opnsense-cert-delete")
        raise OPNsenseAPIError(500, {"message": "injected certificate delete failure"})
    refid = next(
        (
            key
            for key, cert in state.opnsense_certs.items()
            if cert["uuid"] == cert_uuid or key == cert_uuid
        ),
        None,
    )
    if refid is None:
        return {"result": "failed"}
    cert = state.opnsense_certs[refid]
    crl_state = state.opnsense_crls.get(cert["caref"])
    if crl_state and any(
        refid in revoked_for_reason
        for revoked_for_reason in crl_state["revoked"].values()
    ):
        raise OPNsenseAPIError(
            500,
            {"errorMessage": "Unexpected error, check log for details"},
        )
    del state.opnsense_certs[refid]
    return {"result": "deleted"}


@app.get("/api/trust/cert/search")
async def opnsense_cert_search(request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    carefs = {
        caref
        for value in request.query_params.getlist("carefs")
        for caref in value.split(",")
        if caref
    }
    users = {
        user
        for value in request.query_params.getlist("user")
        for user in value.split(",")
        if user
    }
    rows = [
        _certificate_search_row(cert)
        for cert in state.opnsense_certs.values()
        if (not carefs or cert["caref"] in carefs)
        and (not users or cert["commonname"] in users)
    ]
    return {"rows": rows, "rowCount": len(rows), "total": len(rows)}


def _opnsense_crl_state(caref: str) -> dict[str, Any]:
    return state.opnsense_crls.setdefault(
        caref,
        {
            "descr": f"na-sso CRL {caref}",
            "lifetime": "9999",
            "serial": "0",
            "revoked": {reason: {} for reason in range(11)},
        },
    )


@app.get("/api/trust/crl/get/{caref}")
async def opnsense_crl_get(caref: str, request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    crl_state = _opnsense_crl_state(caref)
    revoked = crl_state["revoked"]
    candidates = {
        cert["refid"]: cert["descr"]
        for cert in state.opnsense_certs.values()
        if cert["caref"] == caref
    }
    for revoked_for_reason in revoked.values():
        candidates.update(revoked_for_reason)
    return {
        "crl": {
            "crlmethod": "internal",
            "descr": crl_state["descr"],
            "lifetime": crl_state["lifetime"],
            "serial": crl_state["serial"],
            **{
                f"revoked_reason_{reason}": {
                    refid: {
                        "value": descr,
                        "selected": "1" if refid in revoked[reason] else "0",
                    }
                    for refid, descr in candidates.items()
                }
                for reason in range(11)
            },
        }
    }


@app.post("/api/trust/crl/set/{caref}")
async def opnsense_crl_set(caref: str, request: Request) -> dict[str, str]:
    _require_opnsense_openvpn(request)
    if "opnsense-crl-set" in state.fail_next:
        state.fail_next.remove("opnsense-crl-set")
        raise OPNsenseAPIError(500, {"message": "injected CRL update failure"})
    if caref not in state.opnsense_cas:
        return {"result": "failed"}
    body = await request.json()
    crl = body.get("crl") if isinstance(body, dict) else None
    if not isinstance(crl, dict) or crl.get("crlmethod") != "internal":
        return {"result": "failed"}
    lifetime = str(crl.get("lifetime", "")).strip()
    if not lifetime:
        raise OPNsenseAPIError(
            500,
            {"errorMessage": "Unexpected error, check log for details"},
        )

    previous = _opnsense_crl_state(caref)
    descriptions = {
        cert["refid"]: cert["descr"]
        for cert in state.opnsense_certs.values()
        if cert["caref"] == caref
    }
    for revoked_for_reason in previous["revoked"].values():
        descriptions.update(revoked_for_reason)
    rebuilt: dict[int, dict[str, str]] = {}
    for reason in range(11):
        raw_refids = crl.get(f"revoked_reason_{reason}", "")
        if not isinstance(raw_refids, str):
            return {"result": "failed"}
        refids = {
            item.strip() for item in raw_refids.split(",") if item.strip()
        }
        rebuilt[reason] = {
            refid: descriptions.get(refid, refid) for refid in refids
        }
    state.opnsense_crls[caref] = {
        "descr": str(crl.get("descr", "")),
        "lifetime": lifetime,
        "serial": str(crl.get("serial", previous["serial"])),
        "revoked": rebuilt,
    }
    return {"result": "saved"}


@app.get("/api/trust/ca/ca_list")
async def opnsense_ca_list(request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    rows = [
        {"caref": ca["refid"], "descr": ca["descr"]}
        for ca in state.opnsense_cas.values()
    ]
    return {"rows": rows, "count": len(rows)}


@app.get("/api/openvpn/export/providers")
async def opnsense_openvpn_providers(request: Request) -> Any:
    _require_opnsense_openvpn(request)
    providers = {
        server_uuid: {
            "auth_nocache": None,
            "cryptoapi": None,
            "hostname": None,
            "local_port": server["port"],
            "mode": "server_tls_user" if server["authmode"] else "",
            "name": f'{server["description"]} {server["proto"]}:{server["port"]}',
            "plain_config": None,
            "random_local_port": "1",
            "static_challenge": None,
            "template": None,
            "validate_server_cn": "1",
            "vpnid": server_uuid,
        }
        for server_uuid, server in state.opnsense_openvpn_servers.items()
        if server["enabled"] == "1" and server["role"] == "server"
    }
    return providers if providers else []


@app.get("/api/openvpn/export/templates")
async def opnsense_openvpn_templates(request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    common = [
        "plain_config",
        "p12_password",
        "random_local_port",
        "auth_nocache",
        "cryptoapi",
        "static_challenge",
    ]
    return {
        "ArchiveOpenVPN": {"name": "Archive", "supportedOptions": common},
        "PlainOpenVPN": {
            "name": "File Only",
            "supportedOptions": [item for item in common if item != "p12_password"],
        },
        "ViscosityVisz": {"name": "Viscosity (visz)", "supportedOptions": common},
    }


@app.post("/api/openvpn/export/validate_presets/{vpnid}")
async def opnsense_openvpn_validate_presets(
    vpnid: str, request: Request
) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    export_options = body.get("openvpn_export") if isinstance(body, dict) else None
    templates = {"ArchiveOpenVPN", "PlainOpenVPN", "ViscosityVisz"}
    if (
        vpnid not in state.opnsense_openvpn_servers
        or not isinstance(export_options, dict)
        or export_options.get("template") not in templates
        or not str(export_options.get("hostname", "")).strip()
    ):
        return {"result": "failed"}
    return {"result": "ok", "changed": False}


@app.get("/api/openvpn/export/accounts/{vpnid}")
async def opnsense_openvpn_accounts(vpnid: str, request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    result: dict[str, Any] = {
        "": {"description": "(none) Exclude certificate from export", "users": []}
    }
    server = state.opnsense_openvpn_servers.get(vpnid)
    if server is None:
        return result
    for cert in state.opnsense_certs.values():
        if cert["caref"] != server["caref"]:
            continue
        users: list[str] = []
        if (
            cert["cert_type"] in {"usr_cert", "combined_server_client"}
            and cert["commonname"] in state.opnsense
        ):
            users.append(cert["commonname"])
        result[cert["refid"]] = {"description": cert["descr"], "users": users}
    return result


@app.get("/api/openvpn/instances/get/{instance_uuid}")
async def opnsense_openvpn_instance(instance_uuid: str, request: Request) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    server = state.opnsense_openvpn_servers.get(instance_uuid)
    return {"instance": _openvpn_instance(server)} if server is not None else {"instance": {}}


def _openvpn_filename(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", value)).strip("_")


async def _opnsense_openvpn_download(
    vpnid: str, request: Request, certref: str | None = None
) -> dict[str, Any]:
    _require_opnsense_openvpn(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict) or "openvpn_export" not in body:
        return {"result": "failed"}
    server = state.opnsense_openvpn_servers.get(vpnid)
    if server is None:
        return {"result": "failed"}

    client_cert = None
    if certref:
        client_cert = state.opnsense_certs.get(certref)
        if client_cert is None:
            raise OPNsenseAPIError(
                500,
                {
                    "errorMessage": "Client certificate not found",
                    "errorTitle": "OpenVPN export",
                },
            )
        if (
            client_cert["caref"] != server["caref"]
            or client_cert["cert_type"] not in {"usr_cert", "combined_server_client"}
        ):
            raise OPNsenseAPIError(
                500,
                {
                    "errorMessage": "Certificate does not belong to server CA",
                    "errorTitle": "OpenVPN export",
                },
            )
        if not client_cert["prv_payload"]:
            raise OPNsenseAPIError(
                500,
                {
                    "errorMessage": "Client certificate not found",
                    "errorTitle": "OpenVPN export",
                },
            )

    export_options = body.get("openvpn_export")
    if not isinstance(export_options, dict):
        export_options = {}
    hostname = str(export_options.get("hostname") or server["hostname"])
    ca = state.opnsense_cas[server["caref"]]
    server_cert = state.opnsense_certs[server["certref"]]
    lines = [
        "dev tun",
        "persist-tun",
        "persist-key",
        "client",
        "resolv-retry infinite",
        f'remote {hostname}  {server["proto"]}',
        "lport 0",
        f'verify-x509-name "C=NL, CN={server_cert["commonname"]}" subject',
        "remote-cert-tls server",
        "auth-user-pass",
        "<ca>",
        ca["crt_payload"].strip(),
        "</ca>",
    ]
    filename = _openvpn_filename(server["description"])
    if client_cert is not None:
        lines.extend(
            [
                "<cert>",
                client_cert["crt_payload"].strip(),
                "</cert>",
                "<key>",
                client_cert["prv_payload"].strip(),
                "</key>",
            ]
        )
        filename += f'_{_openvpn_filename(client_cert["commonname"])}'
    content = "\n".join(lines).encode()
    return {
        "result": "ok",
        "changed": False,
        "filename": f"{filename}.ovpn",
        "filetype": "text/ovpn",
        "content": base64.b64encode(content).decode(),
    }


@app.post("/api/openvpn/export/download/{vpnid}")
@app.post("/api/openvpn/export/download/{vpnid}/", include_in_schema=False)
async def opnsense_openvpn_download_no_cert(vpnid: str, request: Request) -> dict[str, Any]:
    return await _opnsense_openvpn_download(vpnid, request)


@app.post("/api/openvpn/export/download/{vpnid}/{certref}")
async def opnsense_openvpn_download_with_cert(
    vpnid: str, certref: str, request: Request
) -> dict[str, Any]:
    return await _opnsense_openvpn_download(vpnid, request, certref)


# OPNsense Auth User API
@app.post("/api/auth/user/search")
async def opnsense_search(request: Request) -> dict[str, Any]:
    _require_basic(request, _credentials("OPNSENSE", "demo-key", "demo-secret"))
    _maybe_fail("opnsense")
    body = await request.json()
    phrase = str(body.get("searchPhrase", ""))
    rows = [deepcopy(user) for user in state.opnsense.values() if phrase in user["name"]]
    return {"rows": rows, "rowCount": len(rows), "total": len(rows)}


@app.post("/api/auth/user/add")
async def opnsense_add(request: Request) -> dict[str, str]:
    _require_basic(request, _credentials("OPNSENSE", "demo-key", "demo-secret"))
    _maybe_fail("opnsense")
    user = dict((await request.json()).get("user", {}))
    username = str(user.get("name", ""))
    if not username or username in state.opnsense:
        return {"result": "failed"}
    user["uuid"] = str(uuid4())
    state.opnsense[username] = user
    return {"result": "saved", "uuid": user["uuid"]}


@app.post("/api/auth/user/set/{user_uuid}")
async def opnsense_set(user_uuid: str, request: Request) -> dict[str, str]:
    _require_basic(request, _credentials("OPNSENSE", "demo-key", "demo-secret"))
    _maybe_fail("opnsense")
    existing = next((item for item in state.opnsense.values() if item["uuid"] == user_uuid), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="user not found")
    update = dict((await request.json()).get("user", {}))
    password = update.get("password", existing.get("password"))
    existing.update(update)
    if password is not None:
        existing["password"] = password
    return {"result": "saved"}


@app.post("/api/auth/user/del/{user_uuid}")
async def opnsense_delete(user_uuid: str, request: Request) -> dict[str, str]:
    _require_basic(request, _credentials("OPNSENSE", "demo-key", "demo-secret"))
    _maybe_fail("opnsense")
    username = next((name for name, item in state.opnsense.items() if item["uuid"] == user_uuid), None)
    if username is None:
        raise HTTPException(status_code=404, detail="user not found")
    del state.opnsense[username]
    return {"result": "deleted"}


# Nexus Repository Security API
@app.get("/service/rest/v1/security/users")
async def nexus_search(request: Request, userId: str = "", source: str = "default") -> list[dict[str, Any]]:
    _require_basic(request, _credentials("NEXUS", "admin", "demo-password"))
    _maybe_fail("nexus")
    return [deepcopy(user) for name, user in state.nexus.items() if source == "default" and userId in name]


@app.post("/service/rest/v1/security/users", status_code=204)
async def nexus_add(request: Request) -> Response:
    _require_basic(request, _credentials("NEXUS", "admin", "demo-password"))
    _maybe_fail("nexus")
    user = dict(await request.json())
    username = str(user.get("userId", ""))
    if not username or username in state.nexus:
        raise HTTPException(status_code=400, detail="user already exists")
    user.update({"source": "default", "readOnly": False, "externalRoles": []})
    state.nexus[username] = user
    return Response(status_code=204)


@app.put("/service/rest/v1/security/users/{username}", status_code=204)
async def nexus_set(username: str, request: Request) -> Response:
    _require_basic(request, _credentials("NEXUS", "admin", "demo-password"))
    _maybe_fail("nexus")
    if username not in state.nexus:
        raise HTTPException(status_code=404, detail="user not found")
    password = state.nexus[username].get("password")
    state.nexus[username] = dict(await request.json())
    if password is not None:
        state.nexus[username]["password"] = password
    return Response(status_code=204)


@app.put("/service/rest/v1/security/users/{username}/change-password", status_code=204)
async def nexus_password(username: str, request: Request) -> Response:
    _require_basic(request, _credentials("NEXUS", "admin", "demo-password"))
    _maybe_fail("nexus")
    if username not in state.nexus:
        raise HTTPException(status_code=404, detail="user not found")
    state.nexus[username]["password"] = (await request.body()).decode()
    return Response(status_code=204)


@app.delete("/service/rest/v1/security/users/{username}", status_code=204)
async def nexus_delete(username: str, request: Request) -> Response:
    _require_basic(request, _credentials("NEXUS", "admin", "demo-password"))
    _maybe_fail("nexus")
    if state.nexus.pop(username, None) is None:
        raise HTTPException(status_code=404, detail="user not found")
    return Response(status_code=204)


# Nextcloud OCS Provisioning API
@app.get("/ocs/v1.php/cloud/users")
async def nextcloud_search(request: Request, search: str = "") -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    return _ocs(data={"users": [name for name in state.nextcloud if search in name]})


@app.get("/ocs/v1.php/cloud/users/{username}")
async def nextcloud_get(username: str, request: Request) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if username not in state.nextcloud:
        return _ocs(404, "User does not exist")
    return _ocs(data=deepcopy(state.nextcloud[username]))


@app.post("/ocs/v1.php/cloud/users")
async def nextcloud_add(
    request: Request,
    userid: str = Form(...),
    password: str = Form(...),
    displayName: str = Form(""),
    email: str = Form(""),
    groups: list[str] = Form(default=[], alias="groups[]"),
) -> JSONResponse:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if userid in state.nextcloud:
        return JSONResponse(_ocs(102, "User already exists"))
    state.nextcloud[userid] = {
        "id": userid,
        "displayname": displayName or userid,
        "email": email,
        "password": password,
        "enabled": True,
        "groups": groups,
    }
    return JSONResponse(_ocs())


@app.get("/ocs/v1.php/cloud/users/{username}/groups")
async def nextcloud_groups(username: str, request: Request) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if username not in state.nextcloud:
        return _ocs(103, "User does not exist")
    return _ocs(data={"groups": list(state.nextcloud[username].get("groups", []))})


@app.post("/ocs/v1.php/cloud/users/{username}/groups")
async def nextcloud_add_group(username: str, request: Request, groupid: str = Form(...)) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if username not in state.nextcloud:
        return _ocs(103, "User does not exist")
    groups = state.nextcloud[username].setdefault("groups", [])
    if groupid not in groups:
        groups.append(groupid)
    return _ocs()


@app.put("/ocs/v1.php/cloud/users/{username}")
async def nextcloud_edit(
    username: str, request: Request, key: str = Form(...), value: str = Form(...)
) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if username not in state.nextcloud:
        return _ocs(404, "User does not exist")
    state.nextcloud[username][key] = value
    return _ocs()


@app.put("/ocs/v1.php/cloud/users/{username}/{action}")
async def nextcloud_status(username: str, action: str, request: Request) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if username not in state.nextcloud or action not in {"enable", "disable"}:
        return _ocs(404, "User does not exist")
    state.nextcloud[username]["enabled"] = action == "enable"
    return _ocs()


@app.delete("/ocs/v1.php/cloud/users/{username}")
async def nextcloud_delete(username: str, request: Request) -> dict[str, Any]:
    _require_basic(request, _credentials("NEXTCLOUD", "admin", "demo-password"))
    _maybe_fail("nextcloud")
    if state.nextcloud.pop(username, None) is None:
        return _ocs(404, "User does not exist")
    return _ocs()


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in user.items() if key != "password"}


# GitLab Self-Managed Users and moderation APIs
def _require_gitlab(request: Request) -> None:
    _require_header(request, "PRIVATE-TOKEN", os.getenv("MOCK_GITLAB_TOKEN", "demo-token"))
    _maybe_fail("gitlab")


@app.get("/api/v4/user")
async def gitlab_current_user(request: Request) -> dict[str, Any]:
    _require_gitlab(request)
    return {"id": 1, "username": "admin", "name": "Demo administrator", "is_admin": True, "state": "active"}


@app.get("/api/v4/users")
async def gitlab_users(request: Request, username: str = "") -> list[dict[str, Any]]:
    _require_gitlab(request)
    return [
        _public_user(user) for name, user in state.gitlab.items()
        if not username or name.lower() == username.lower()
    ]


@app.post("/api/v4/users", status_code=201)
async def gitlab_add(request: Request) -> JSONResponse:
    _require_gitlab(request)
    payload = dict(await request.json())
    username = str(payload.get("username", ""))
    if not username or username in state.gitlab:
        raise HTTPException(status_code=409, detail="user already exists")
    user = {
        "id": str(uuid4()), "username": username, "name": str(payload.get("name", username)),
        "email": str(payload.get("email", "")), "password": str(payload.get("password", "")),
        "state": "active", "is_admin": False,
    }
    state.gitlab[username] = user
    return JSONResponse(_public_user(user), status_code=201)


@app.put("/api/v4/users/{user_id}")
async def gitlab_set(user_id: str, request: Request) -> dict[str, Any]:
    _require_gitlab(request)
    user = next((item for item in state.gitlab.values() if item["id"] == user_id), None)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    payload = dict(await request.json())
    for key in ("name", "email", "password"):
        if key in payload:
            user[key] = payload[key]
    return _public_user(user)


@app.post("/api/v4/users/{user_id}/{action}", status_code=201)
async def gitlab_status(user_id: str, action: str, request: Request) -> JSONResponse:
    _require_gitlab(request)
    user = next((item for item in state.gitlab.values() if item["id"] == user_id), None)
    if user is None or action not in {"block", "unblock"}:
        raise HTTPException(status_code=404, detail="user not found")
    user["state"] = "blocked" if action == "block" else "active"
    return JSONResponse({"message": "Success"}, status_code=201)


@app.delete("/api/v4/users/{user_id}", status_code=204)
async def gitlab_delete(user_id: str, request: Request) -> Response:
    _require_gitlab(request)
    username = next((name for name, item in state.gitlab.items() if item["id"] == user_id), None)
    if username is None:
        raise HTTPException(status_code=404, detail="user not found")
    del state.gitlab[username]
    return Response(status_code=204)


# Gitea administrator Users API
def _require_gitea(request: Request) -> None:
    _require_header(request, "Authorization", f"token {os.getenv('MOCK_GITEA_TOKEN', 'demo-token')}")
    _maybe_fail("gitea")


@app.get("/api/v1/admin/users")
async def gitea_users(request: Request) -> list[dict[str, Any]]:
    _require_gitea(request)
    return [_public_user(user) for user in state.gitea.values()]


@app.post("/api/v1/admin/users", status_code=201)
async def gitea_add(request: Request) -> JSONResponse:
    _require_gitea(request)
    payload = dict(await request.json())
    username = str(payload.get("username", ""))
    if not username or username in state.gitea:
        raise HTTPException(status_code=422, detail="user already exists")
    user = {
        "id": str(uuid4()), "login": username, "login_name": username,
        "full_name": str(payload.get("full_name", username)), "email": str(payload.get("email", "")),
        "password": str(payload.get("password", "")), "active": True, "prohibit_login": False,
    }
    state.gitea[username] = user
    return JSONResponse(_public_user(user), status_code=201)


@app.patch("/api/v1/admin/users/{username}")
async def gitea_set(username: str, request: Request) -> dict[str, Any]:
    _require_gitea(request)
    user = state.gitea.get(username)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    payload = dict(await request.json())
    for key in ("login_name", "full_name", "email", "password", "active", "prohibit_login"):
        if key in payload:
            user[key] = payload[key]
    return _public_user(user)


@app.delete("/api/v1/admin/users/{username}", status_code=204)
async def gitea_delete(username: str, request: Request) -> Response:
    _require_gitea(request)
    if state.gitea.pop(username, None) is None:
        raise HTTPException(status_code=404, detail="user not found")
    return Response(status_code=204)


# Immich stable administrator Users API
def _require_immich(request: Request) -> None:
    _require_header(request, "x-api-key", os.getenv("MOCK_IMMICH_TOKEN", "demo-token"))
    _maybe_fail("immich")


@app.get("/api/admin/users")
async def immich_users(request: Request, withDeleted: bool = False) -> list[dict[str, Any]]:
    _require_immich(request)
    return [
        _public_user(user) for user in state.immich.values()
        if withDeleted or user.get("status") == "active"
    ]


@app.post("/api/admin/users", status_code=201)
async def immich_add(request: Request) -> JSONResponse:
    _require_immich(request)
    payload = dict(await request.json())
    email = str(payload.get("email", ""))
    if not email or email.lower() in state.immich:
        raise HTTPException(status_code=400, detail="user already exists")
    user = {
        "id": str(uuid4()), "email": email, "name": str(payload.get("name", email)),
        "password": str(payload.get("password", "")), "status": "active",
        "isAdmin": False, "deletedAt": None, "shouldChangePassword": bool(payload.get("shouldChangePassword", False)),
    }
    state.immich[email.lower()] = user
    return JSONResponse(_public_user(user), status_code=201)


@app.put("/api/admin/users/{user_id}")
async def immich_set(user_id: str, request: Request) -> dict[str, Any]:
    _require_immich(request)
    user = next((item for item in state.immich.values() if item["id"] == user_id), None)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    payload = dict(await request.json())
    for key in ("email", "name", "password", "shouldChangePassword"):
        if key in payload:
            user[key] = payload[key]
    return _public_user(user)


@app.post("/api/admin/users/{user_id}/restore")
async def immich_restore(user_id: str, request: Request) -> dict[str, Any]:
    _require_immich(request)
    user = next((item for item in state.immich.values() if item["id"] == user_id), None)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    user.update(status="active", deletedAt=None)
    return _public_user(user)


@app.delete("/api/admin/users/{user_id}")
async def immich_delete(user_id: str, request: Request) -> dict[str, Any]:
    _require_immich(request)
    key = next((email for email, item in state.immich.items() if item["id"] == user_id), None)
    if key is None:
        raise HTTPException(status_code=404, detail="user not found")
    payload = dict(await request.json())
    if payload.get("force"):
        return _public_user(state.immich.pop(key))
    state.immich[key].update(status="deleted", deletedAt="demo-soft-delete")
    return _public_user(state.immich[key])


# Jenkins built-in local security realm administrator actions
def _require_jenkins(request: Request) -> None:
    _require_basic(request, _credentials("JENKINS", "admin", "demo-token"))
    _maybe_fail("jenkins")


@app.get("/api/json")
async def jenkins_root(request: Request) -> dict[str, str]:
    _require_jenkins(request)
    return {"mode": "NORMAL", "nodeDescription": "NA-SSO demo Jenkins"}


@app.get("/crumbIssuer/api/json")
async def jenkins_crumb(request: Request) -> dict[str, str]:
    _require_jenkins(request)
    return {"crumbRequestField": "Jenkins-Crumb", "crumb": "demo-crumb"}


@app.get("/user/{username}/api/json")
async def jenkins_user(username: str, request: Request) -> dict[str, Any]:
    _require_jenkins(request)
    user = state.jenkins.get(username)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _public_user(user)


@app.get("/asynchPeople/api/json")
async def jenkins_users(request: Request) -> dict[str, list[dict[str, dict[str, Any]]]]:
    _require_jenkins(request)
    return {"users": [{"user": _public_user(user)} for user in state.jenkins.values()]}


@app.post("/securityRealm/createAccountByAdmin")
async def jenkins_add(
    request: Request, username: str = Form(...), password1: str = Form(...),
    password2: str = Form(...), fullname: str = Form(""), email: str = Form(""),
) -> RedirectResponse:
    _require_jenkins(request)
    if password1 != password2:
        raise HTTPException(status_code=400, detail="passwords do not match")
    if not username or username in state.jenkins:
        raise HTTPException(status_code=400, detail="user already exists")
    state.jenkins[username] = {
        "id": username, "fullName": fullname or username, "email": email, "password": password1,
    }
    return RedirectResponse("/", status_code=303)


@app.post("/user/{username}/doDelete")
async def jenkins_delete(username: str, request: Request) -> RedirectResponse:
    _require_jenkins(request)
    if state.jenkins.pop(username, None) is None:
        raise HTTPException(status_code=404, detail="user not found")
    return RedirectResponse("/", status_code=303)
