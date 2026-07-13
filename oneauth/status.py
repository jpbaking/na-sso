import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from oneauth.auth import current_admin
from oneauth.connectors import get_connectors
from oneauth.db import get_session
from oneauth.models import ManagedUser

router = APIRouter()


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
    probes = []
    for connector in get_connectors():
        result = await connector.probe()
        probes.append({"name": connector.name, "ok": result.ok, "detail": result.detail})
    with get_session() as db:
        users = db.query(ManagedUser).order_by(ManagedUser.username).all()
        for user in users:
            user.sync_states
    return templates.TemplateResponse(
        request,
        "status.html",
        {"admin": admin, "probes": probes, "users": users},
    )
