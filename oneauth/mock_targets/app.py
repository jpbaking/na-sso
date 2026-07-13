from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


@dataclass
class MockState:
    opnsense: dict[str, dict[str, Any]] = field(default_factory=dict)
    nexus: dict[str, dict[str, Any]] = field(default_factory=dict)
    nextcloud: dict[str, dict[str, Any]] = field(default_factory=dict)
    fail_next: set[str] = field(default_factory=set)
    available: dict[str, bool] = field(
        default_factory=lambda: {"opnsense": True, "nexus": True, "nextcloud": True}
    )

    def reset(self) -> None:
        self.opnsense.clear()
        self.nexus.clear()
        self.nextcloud.clear()
        self.fail_next.clear()
        self.available = {"opnsense": True, "nexus": True, "nextcloud": True}


state = MockState()
app = FastAPI(title="One Auth mock targets", docs_url=None, redoc_url=None)


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


def _maybe_fail(target: str) -> None:
    if target in state.fail_next:
        state.fail_next.remove(target)
        raise HTTPException(status_code=503, detail=f"injected {target} failure")
    if not state.available[target]:
        raise HTTPException(status_code=503, detail=f"{target} is toggled unavailable")


def _ocs(code: int = 100, message: str = "OK", data: Any = None) -> dict[str, Any]:
    return {
        "ocs": {
            "meta": {"status": "ok" if code == 100 else "failure", "statuscode": code, "message": message},
            "data": {} if data is None else data,
        }
    }


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def control_page() -> str:
    cards = "".join(
        f"""<article><h2>{target.title()}</h2>
        <p class=\"{'up' if available else 'down'}\">{'Reply success' if available else 'Reply failure'}</p>
        <form method=\"post\" action=\"/__mock__/availability/{target}\">
        <input type=\"hidden\" name=\"available\" value=\"{'false' if available else 'true'}\">
        <button type=\"submit\">Switch to {'failure' if available else 'success'}</button></form></article>"""
        for target, available in state.available.items()
    )
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">
    <title>One Auth mock controls</title><style>
    body{{font:16px system-ui;background:#101828;color:#f8fafc;margin:0;padding:3rem}}main{{max-width:900px;margin:auto}}
    section{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}}article{{background:#1d2939;padding:1.5rem;border-radius:12px}}
    .up{{color:#6ce9a6}}.down{{color:#f97066}}button{{padding:.7rem 1rem;border:0;border-radius:8px;cursor:pointer}}</style></head>
    <body><main><h1>Mock target controls</h1><p>Each switch applies to every API request for that target.</p><section>{cards}</section></main></body></html>"""


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
    if target not in {"opnsense", "nexus", "nextcloud"}:
        raise HTTPException(status_code=404, detail="unknown target")
    state.fail_next.add(target)
    return {"status": "armed", "target": target}


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
