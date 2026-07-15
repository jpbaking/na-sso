from fastapi import APIRouter, Request
import csv
import io
import json
from datetime import datetime, timezone

from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.db import get_session
from na_sso.feedback import template_response
from na_sso.audit_query import AuditParams, query_audit, safe_detail
from na_sso.config import get_settings
from na_sso.models import AuditEvent, LifecycleOperation, OperationTargetAttempt
from na_sso.permissions import VIEW_AUDIT, permission_context

router = APIRouter()


def record_audit(
    db,
    actor: str,
    action: str,
    subject: str = "",
    detail: str = "",
    operation_id: str | None = None,
):
    db.add(
        AuditEvent(
            actor=actor,
            action=action,
            subject=subject,
            detail=detail,
            operation_id=operation_id,
        )
    )


def _export_rows(audit):
    for item in audit.items:
        event = item.event
        yield {
            "at_utc": event.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actor": event.actor,
            "action": event.action,
            "summary": item.summary,
            "subject": event.subject,
            "outcome": item.outcome,
            "operation_id": event.operation_id or "",
            "detail": safe_detail(event.detail),
        }


def _download_response(body: str, *, media_type: str, filename: str) -> Response:
    return Response(body, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/audit")
async def audit_page(request: Request):
    from na_sso.main import templates

    principal = permission_guard(request, VIEW_AUDIT)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    with get_session() as db:
        audit = query_audit(db, AuditParams.parse(request.query_params))
    return template_response(
        templates,
        request,
        "audit.html",
        {"admin": admin, "admin_area": True, "audit": audit,
         "permissions": permission_context(principal["role"])},
    )


@router.get("/audit/export.{format}")
async def audit_export(request: Request, format: str):
    principal = permission_guard(request, VIEW_AUDIT)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    if format not in {"csv", "json"}:
        return Response("Unsupported audit export format", status_code=404)
    params = AuditParams.parse(request.query_params)
    page_size = get_settings().file.audit_policy.export_page_size
    with get_session() as db:
        audit = query_audit(db, params, page_size=page_size)
        rows = list(_export_rows(audit))
        record_audit(
            db, admin, "audit.exported", params.actor or "filtered audit",
            f"format={format}; page={audit.params.page}; rows={len(rows)}",
        )
        db.commit()
    if format == "json":
        body = json.dumps({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "page": audit.params.page,
            "per_page": audit.params.per_page,
            "total": audit.total,
            "pages": audit.pages,
            "events": rows,
        }, indent=2)
        return _download_response(
            body, media_type="application/json", filename=f"na-sso-audit-{audit.params.page}.json"
        )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else [
        "at_utc", "actor", "action", "summary", "subject", "outcome", "operation_id", "detail"
    ])
    writer.writeheader()
    writer.writerows(rows)
    return _download_response(
        output.getvalue(), media_type="text/csv", filename=f"na-sso-audit-{audit.params.page}.csv"
    )


@router.get("/audit/operations/{operation_id}")
async def operation_detail(request: Request, operation_id: str):
    from na_sso.main import templates

    principal = permission_guard(request, VIEW_AUDIT)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    with get_session() as db:
        operation = db.get(LifecycleOperation, operation_id)
        if not operation:
            return RedirectResponse("/audit", status_code=303)
        parent = db.get(LifecycleOperation, operation.parent_id) if operation.parent_id else None
        children = db.query(LifecycleOperation).filter_by(parent_id=operation.id).order_by(
            LifecycleOperation.created_at, LifecycleOperation.id
        ).all()
        attempts = db.query(OperationTargetAttempt).filter_by(
            operation_id=operation.id
        ).order_by(OperationTargetAttempt.started_at, OperationTargetAttempt.id).all()
        events = db.query(AuditEvent).filter_by(operation_id=operation.id).order_by(
            AuditEvent.at, AuditEvent.id
        ).all()
    return template_response(templates, request, "operation_detail.html", {
        "admin": admin, "admin_area": True, "operation": operation,
        "permissions": permission_context(principal["role"]),
        "parent": parent, "children": children,
        "attempts": attempts, "events": events,
    })
