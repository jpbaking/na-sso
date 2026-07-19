from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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


@dataclass
class MockState:
    opnsense: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    def reset(self) -> None:
        self.opnsense.clear()
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


state = MockState()
app = FastAPI(title="NA-SSO mock targets", docs_url=None, redoc_url=None)
static_root = Path(__file__).resolve().parents[1] / "static"
app.mount("/design", StaticFiles(directory=static_root / "design"), name="mock-design")
app.mount("/static", StaticFiles(directory=static_root), name="mock-static")


class NpmAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message


@app.exception_handler(NpmAPIError)
async def npm_api_error(_request: Request, exc: NpmAPIError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": exc.status_code, "message": exc.message}},
        status_code=exc.status_code,
    )


def _credentials(prefix: str, username_default: str, password_default: str) -> tuple[str, str]:
    return (
        os.getenv(f"MOCK_{prefix}_USERNAME", username_default),
        os.getenv(f"MOCK_{prefix}_PASSWORD", password_default),
    )


def _require_basic(request: Request, credentials: tuple[str, str]) -> None:
    auth = request.headers.get("authorization", "")
    import base64

    expected = "Basic " + base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
    if auth != expected:
        raise HTTPException(status_code=401, detail="invalid demo credentials")


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
    if target not in state.available:
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
