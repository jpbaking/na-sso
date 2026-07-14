import asyncio
import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from oneauth.auth import current_admin
from oneauth.connectors import get_connectors
from oneauth.config import get_settings
from oneauth.db import get_session
from oneauth.models import ManagedUser
from oneauth.audit import record_audit
from oneauth.target_credentials import readiness_map, record_probe, save_credentials, target_definitions

router = APIRouter()


def _configuration_status(*, configured: bool, verified: bool, detail: str) -> str:
    if verified:
        return "fully configured"
    if not configured:
        return "configuration required"
    normalised = detail.lower()
    if any(marker in normalised for marker in (
        "401", "403", "auth", "unauthor", "forbidden", "permission denied",
    )):
        return "auth failed"
    if any(marker in normalised for marker in (
        "unreachable", "connection", "connect", "timeout", "timed out",
        "name or service", "network",
    )):
        return "Unreachable"
    return "verification failed"


def sync_snapshot() -> dict:
    with get_session() as db:
        users = db.query(ManagedUser).order_by(ManagedUser.username).all()
        return {
            "users": [
                {
                    "id": user.id,
                    "desired_action": user.desired_action,
                    "deleted": user.deleted_at is not None,
                    "states": {
                        state.target: {
                            "state": state.state,
                            "assigned": state.assigned,
                            "retired": state.retired,
                            "detail": state.detail,
                            "attempt_count": state.attempt_count,
                            "next_retry_at": state.next_retry_at.isoformat() if state.next_retry_at else None,
                        }
                        for state in user.sync_states
                    },
                }
                for user in users
            ]
        }


@router.get("/events/sync")
async def sync_events(request: Request, once: bool = False):
    if not current_admin(request):
        return StreamingResponse(iter(["event: unauthorized\ndata: {}\n\n"]), status_code=401, media_type="text/event-stream")

    async def events():
        previous = None
        ticks = 0
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(sync_snapshot(), separators=(",", ":"))
            if payload != previous:
                yield f"event: sync\ndata: {payload}\n\n"
                previous = payload
                if once:
                    break
            elif ticks % 15 == 0:
                yield ": keepalive\n\n"
            ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/status")
async def status_page(request: Request):
    from oneauth.main import templates

    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    readiness = readiness_map()
    definitions = target_definitions()
    probes = [{"id": target.id, "name": target.display_name, "type": target.type,
               "ok": readiness[target.id].verified, "detail": readiness[target.id].detail,
               "configured": readiness[target.id].configured,
               "configuration_status": _configuration_status(
                   configured=readiness[target.id].configured,
                   verified=readiness[target.id].verified,
                   detail=readiness[target.id].detail,
               )} for target in definitions]
    if not get_settings().config_file:
        probes = []
        for connector in get_connectors():
            result = await connector.probe()
            probes.append({"id": connector.target_id, "name": connector.display_name,
                           "type": connector.target_type, "ok": result.ok,
                           "detail": result.detail, "configured": True,
                           "configuration_status": _configuration_status(
                               configured=True, verified=result.ok, detail=result.detail
                           )})
    return templates.TemplateResponse(
        request,
        "status.html",
        {"admin": admin, "probes": probes},
    )


@router.post("/targets/{target_id}/credentials")
async def configure_target(request: Request, target_id: str,
                           auth_mode: str = Form("password"),
                           admin_user: str = Form(""), password: str = Form(""),
                           api_key: str = Form(""), api_secret: str = Form(""),
                           private_key: UploadFile | None = File(default=None)):
    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    target = next((item for item in target_definitions() if item.id == target_id), None)
    if target is None:
        return RedirectResponse("/status", status_code=303)
    payload: dict[str, str]
    if target.type == "opnsense":
        payload = {"api_key": api_key.strip(), "api_secret": api_secret}
    elif target.type in {"nexus", "nextcloud"}:
        payload = {"admin_user": admin_user.strip(), "admin_password": password}
    else:
        uploaded = (await private_key.read()).decode("utf-8") if private_key and private_key.filename else ""
        payload = {"management_user": admin_user.strip(),
                   "management_password": password if auth_mode == "password" else "",
                   "management_private_key": uploaded if auth_mode == "private_key" else ""}
    try:
        save_credentials(target_id, auth_mode, payload)
    except (UnicodeDecodeError, ValueError):
        return RedirectResponse("/status", status_code=303)
    from oneauth.connectors.base import build_unverified_connector
    try:
        result = await build_unverified_connector(target_id).probe()
        record_probe(target_id, result.ok, result.detail)
    except ValueError as error:
        result = type("Result", (), {"ok": False, "detail": str(error)})()
    with get_session() as db:
        record_audit(db, admin, "target.credentials.updated", target_id,
                     f"{target.type} {auth_mode}; "
                     f"{'verified' if result.ok else 'failed'} — {result.detail}")
        db.commit()
    return RedirectResponse("/status", status_code=303)


@router.post("/targets/{target_id}/probe")
async def probe_target(request: Request, target_id: str):
    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    from oneauth.connectors.base import build_unverified_connector
    try:
        result = await build_unverified_connector(target_id).probe()
        record_probe(target_id, result.ok, result.detail)
    except ValueError as error:
        result = type("Result", (), {"ok": False, "detail": str(error)})()
    with get_session() as db:
        record_audit(db, admin, "target.probe", target_id,
                     f"{'verified' if result.ok else 'failed'} — {result.detail}")
        db.commit()
    return RedirectResponse("/status", status_code=303)
