from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from na_sso.auth import current_admin
from na_sso.db import get_session
from na_sso.models import AuditEvent

router = APIRouter()


def record_audit(db, actor: str, action: str, subject: str = "", detail: str = ""):
    db.add(
        AuditEvent(actor=actor, action=action, subject=subject, detail=detail)
    )


@router.get("/audit")
async def audit_page(request: Request):
    from na_sso.main import templates

    admin = current_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=303)
    with get_session() as db:
        events = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(500).all()
    return templates.TemplateResponse(
        request, "audit.html", {"admin": admin, "events": events}
    )
