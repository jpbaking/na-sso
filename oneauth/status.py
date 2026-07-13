from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from oneauth.auth import current_admin
from oneauth.connectors import get_connectors
from oneauth.db import get_session
from oneauth.models import ManagedUser

router = APIRouter()


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
