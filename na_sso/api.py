"""Version 1 automation API over the same permissions and operation records as the UI."""

from __future__ import annotations

from dataclasses import asdict
from math import ceil

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from na_sso import __version__
from na_sso.api_contract import (
    api_error,
    api_guard,
    api_response,
    begin_idempotent_request,
    finish_idempotent_request,
    page_meta,
    principal_has_permission,
)
from na_sso.audit import record_audit
from na_sso.audit_query import AuditParams, query_audit, safe_detail
from na_sso.config import get_settings
from na_sso.connectors import get_connectors
from na_sso.db import get_session
from na_sso.inventory import InventoryParams, query_inventory
from na_sso.lifecycle import LifecycleCommand, OperationStatus
from na_sso.models import (
    LifecycleOperation,
    ManagedUser,
    ReconciliationRun,
    utcnow,
)
from na_sso.permissions import MANAGE_SECURITY, MANAGE_TARGETS, MANAGE_USERS, VIEW_AUDIT
from na_sso.reconcile import (
    ReconciliationApprovalError,
    approve_reconciliation,
    create_reconciliation_preview,
    execute_reconciliation_repair,
)
from na_sso.target_credentials import readiness_map, sanitise_probe_detail, target_definitions


_bearer = HTTPBearer(auto_error=False, description="Expiring NA-SSO service-account credential")


async def _documented_auth(
    _credential: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """OpenAPI security declaration; api_guard performs the actual shared check."""


router = APIRouter(
    prefix="/api/v1", tags=["automation-v1"],
    dependencies=[Depends(_documented_auth)],
)


class ReconciliationPreviewInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    user_id: int | None = Field(default=None, ge=1)
    target_id: str | None = Field(default=None, max_length=64)


class ReconciliationApprovalInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    approval_token: str = Field(min_length=8, max_length=128)
    confirm_destructive: bool = False


class TargetProbeInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _user_payload(user: ManagedUser, summary=None, *, detail: bool = False) -> dict:
    payload = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "desired_action": user.desired_action,
        "password_decision": user.password_decision_kind or None,
        "password_expires_at": _iso(user.password_expires_at),
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }
    if summary is not None:
        payload["summary"] = {
            "lifecycle": summary.lifecycle,
            "healthy_targets": summary.healthy_targets,
            "assigned_targets": summary.assigned_targets,
            "issue_count": summary.issue_count,
        }
    if detail:
        payload["targets"] = [{
            "target_id": state.target,
            "target_type": state.target_type,
            "assigned": state.assigned,
            "retired": state.retired,
            "state": state.state,
            "attempt_count": state.attempt_count,
            "next_retry_at": _iso(state.next_retry_at),
            "operation_id": state.operation_id,
            "updated_at": _iso(state.updated_at),
        } for state in sorted(user.sync_states, key=lambda item: item.target)]
    return payload


def _operation_payload(operation: LifecycleOperation, *, detail: bool = False) -> dict:
    payload = {
        "id": operation.id,
        "parent_id": operation.parent_id,
        "user_id": operation.user_id,
        "command": operation.command,
        "status": operation.status,
        "actor": operation.actor,
        "subject": operation.subject,
        "requested_target": operation.requested_target,
        "total_targets": operation.total_targets,
        "completed_targets": operation.completed_targets,
        "failed_targets": operation.failed_targets,
        "created_at": _iso(operation.created_at),
        "started_at": _iso(operation.started_at),
        "completed_at": _iso(operation.completed_at),
    }
    if detail:
        payload["detail"] = safe_detail(operation.detail)
        payload["attempts"] = [{
            "id": item.id,
            "target_id": item.target,
            "target_type": item.target_type,
            "attempt_number": item.attempt_number,
            "status": item.status,
            "result_state": item.result_state,
            "detail": safe_detail(item.detail),
            "started_at": _iso(item.started_at),
            "completed_at": _iso(item.completed_at),
        } for item in sorted(operation.attempts, key=lambda value: (value.started_at, value.id))]
    return payload


def _reconciliation_payload(run: ReconciliationRun, *, detail: bool = False) -> dict:
    payload = {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "actor": run.actor,
        "scope": {"user_id": run.scope_user_id, "target_id": run.scope_target_id},
        "total_targets": run.total_targets,
        "drifted_targets": run.drifted_targets,
        "unknown_targets": run.unknown_targets,
        "destructive_targets": run.destructive_targets,
        "attempt_count": run.attempt_count,
        "next_attempt_at": _iso(run.next_attempt_at),
        "operation_id": run.operation_id,
        "detail": safe_detail(run.detail),
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
    }
    if run.status == "previewed":
        payload["approval_token"] = run.approval_token
    if detail:
        payload["findings"] = [{
            "id": item.id,
            "user_id": item.user_id,
            "username": item.username,
            "target_id": item.target_id,
            "target_name": item.target_name,
            "field": item.field,
            "state": item.state,
            "desired": item.desired,
            "actual": item.actual,
            "detail": safe_detail(item.detail),
            "repair_status": item.repair_status,
            "operation_id": item.operation_id,
        } for item in sorted(
            run.findings, key=lambda value: (value.username, value.target_name, value.id)
        )]
    return payload


def _positive(value: str | None, default: int, maximum: int) -> int:
    try:
        return min(max(1, int(value or default)), maximum)
    except (TypeError, ValueError):
        return default


@router.get("")
async def api_index(request: Request):
    principal = api_guard(request)
    if isinstance(principal, JSONResponse):
        return principal
    permissions = {
        "users": principal_has_permission(principal, MANAGE_USERS),
        "targets": principal_has_permission(principal, MANAGE_TARGETS),
        "audit": principal_has_permission(principal, VIEW_AUDIT),
        "roles": principal_has_permission(principal, MANAGE_SECURITY),
    }
    return api_response(request, {
        "product": "NA-SSO",
        "product_version": __version__,
        "api_version": "v1",
        "principal": {
            "username": principal["username"],
            "type": principal.get("principal_type", "user"),
            "role": principal["role"],
        },
        "capabilities": permissions,
        "resources": {
            "users": "/api/v1/users" if permissions["users"] else None,
            "bulk": "/api/v1/bulk/preview" if permissions["users"] else None,
            "reconciliation": "/api/v1/reconciliation" if permissions["users"] else None,
            "targets": "/api/v1/targets" if permissions["targets"] else None,
            "operations": "/api/v1/operations" if permissions["audit"] else None,
            "audit": "/api/v1/audit" if permissions["audit"] else None,
        },
    })


@router.get("/users")
async def users_list(request: Request):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    params = InventoryParams.parse(request.query_params)
    maximum = min(100, get_settings().file.automation_api_policy.max_page_size)
    if params.per_page > maximum:
        params = InventoryParams(**{**params.__dict__, "per_page": maximum})
    with get_session() as db:
        inventory = query_inventory(db, params)
        data = [_user_payload(item.user, item.summary) for item in inventory.items]
    return api_response(
        request, data,
        meta=page_meta(
            page=inventory.params.page, per_page=inventory.params.per_page,
            total=inventory.total, pages=inventory.pages,
        ),
    )


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user is None:
            return api_error(request, 404, "not_found", "The managed user was not found.")
        user.sync_states
        payload = _user_payload(user, detail=True)
    return api_response(request, payload)


@router.get("/targets")
async def targets_list(request: Request):
    principal = api_guard(request, MANAGE_TARGETS)
    if isinstance(principal, JSONResponse):
        return principal
    readiness = readiness_map()
    connectors = {item.target_id: item for item in get_connectors()}
    data = []
    for target in target_definitions():
        state = readiness[target.id]
        connector = connectors.get(target.id)
        data.append({
            "id": target.id,
            "type": target.type,
            "display_name": target.display_name,
            "enabled": target.enabled,
            "configured": state.configured,
            "verified": state.verified,
            "reachable": state.reachable,
            "failure_kind": state.failure_kind or None,
            "probe_detail": sanitise_probe_detail(state.detail),
            "revision": state.revision,
            "last_checked_at": _iso(state.last_checked_at),
            "last_success_at": _iso(state.last_success_at),
            "next_probe_at": _iso(state.next_probe_at),
            "identity_capabilities": asdict(connector.capabilities) if connector else None,
            "inspection_capabilities": asdict(connector.inspection_capabilities) if connector else None,
            "connector_contract": asdict(connector.contract_metadata()) if connector else None,
        })
    return api_response(request, data, meta={"total": len(data)})


@router.post("/targets/{target_id}/probe")
async def target_probe(request: Request, target_id: str, payload: TargetProbeInput):
    principal = api_guard(request, MANAGE_TARGETS)
    if isinstance(principal, JSONResponse):
        return principal
    record, replay = begin_idempotent_request(
        request, actor=principal["username"], idempotency_key=payload.idempotency_key,
        payload={"target_id": target_id},
    )
    if replay:
        return replay
    readiness = readiness_map().get(target_id)
    if readiness is None:
        response = api_error(request, 404, "not_found", "The target was not found.")
        return finish_idempotent_request(record.id, response)
    if not readiness.configured:
        response = api_error(
            request, 409, "credentials_required",
            "Save complete management credentials before testing this target.",
        )
        return finish_idempotent_request(record.id, response)
    from na_sso.connectors.base import SyncResult, build_unverified_connector
    from na_sso.target_credentials import record_probe
    try:
        result = await build_unverified_connector(target_id).probe()
    except ValueError as error:
        result = SyncResult(False, str(error))
    record_probe(target_id, result.ok, result.detail)
    safe = sanitise_probe_detail(result.detail)
    with get_session() as db:
        operation = LifecycleOperation(
            command=LifecycleCommand.TARGET_PROBE.value,
            status=(OperationStatus.SUCCEEDED if result.ok else OperationStatus.FAILED).value,
            actor=principal["username"], subject=f"target:{target_id}",
            requested_target=target_id, total_targets=1,
            completed_targets=1 if result.ok else 0,
            failed_targets=0 if result.ok else 1,
            detail=safe, started_at=utcnow(), completed_at=utcnow(),
        )
        db.add(operation)
        db.flush()
        record_audit(
            db, principal["username"], "target.probe", target_id,
            f"{'verified' if result.ok else 'failed'} — {safe}", operation.id,
        )
        db.commit()
        operation_id = operation.id
    response = api_response(
        request,
        {"target_id": target_id, "reachable": result.ok, "detail": safe,
         "operation_id": operation_id},
        status_code=200 if result.ok else 502,
    )
    return finish_idempotent_request(record.id, response, operation_id=operation_id)


@router.get("/operations")
async def operations_list(request: Request):
    principal = api_guard(request, VIEW_AUDIT)
    if isinstance(principal, JSONResponse):
        return principal
    maximum = get_settings().file.automation_api_policy.max_page_size
    page = _positive(request.query_params.get("page"), 1, 1_000_000)
    per_page = _positive(request.query_params.get("per_page"), 50, maximum)
    status = request.query_params.get("status", "")[:32]
    command = request.query_params.get("command", "")[:32]
    subject = request.query_params.get("subject", "")[:128]
    with get_session() as db:
        query = db.query(LifecycleOperation)
        if status:
            query = query.filter_by(status=status)
        if command:
            query = query.filter_by(command=command)
        if subject:
            query = query.filter(LifecycleOperation.subject.ilike(f"%{subject}%"))
        total = query.count()
        pages = max(1, ceil(total / per_page))
        page = min(page, pages)
        rows = query.order_by(
            LifecycleOperation.created_at.desc(), LifecycleOperation.id.desc()
        ).offset((page - 1) * per_page).limit(per_page).all()
        data = [_operation_payload(item) for item in rows]
    return api_response(
        request, data, meta=page_meta(page=page, per_page=per_page, total=total, pages=pages)
    )


@router.get("/operations/{operation_id}")
async def operation_detail(request: Request, operation_id: str):
    principal = api_guard(request, VIEW_AUDIT)
    if isinstance(principal, JSONResponse):
        return principal
    with get_session() as db:
        operation = db.get(LifecycleOperation, operation_id)
        if operation is None:
            return api_error(request, 404, "not_found", "The operation was not found.")
        operation.attempts
        payload = _operation_payload(operation, detail=True)
        payload["children"] = [item.id for item in db.query(LifecycleOperation).filter_by(
            parent_id=operation.id
        ).order_by(LifecycleOperation.created_at, LifecycleOperation.id).all()]
    return api_response(request, payload)


@router.get("/reconciliation")
async def reconciliation_list(request: Request):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    maximum = get_settings().file.automation_api_policy.max_page_size
    page = _positive(request.query_params.get("page"), 1, 1_000_000)
    per_page = _positive(request.query_params.get("per_page"), 25, maximum)
    with get_session() as db:
        query = db.query(ReconciliationRun)
        total = query.count()
        pages = max(1, ceil(total / per_page))
        page = min(page, pages)
        rows = query.order_by(ReconciliationRun.created_at.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()
        data = [_reconciliation_payload(item) for item in rows]
    return api_response(
        request, data, meta=page_meta(page=page, per_page=per_page, total=total, pages=pages)
    )


@router.post("/reconciliation/preview")
async def reconciliation_preview(request: Request, payload: ReconciliationPreviewInput):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    request_payload = payload.model_dump()
    record, replay = begin_idempotent_request(
        request, actor=principal["username"], idempotency_key=payload.idempotency_key,
        payload=request_payload,
    )
    if replay:
        return replay
    target_id = payload.target_id.strip() if payload.target_id else None
    if target_id and target_id not in {item.target_id for item in get_connectors()}:
        response = api_error(request, 422, "unknown_target", "The requested target is unavailable.")
        return finish_idempotent_request(record.id, response)
    if payload.user_id:
        with get_session() as db:
            user = db.get(ManagedUser, payload.user_id)
            if user is None or user.role == "root":
                response = api_error(request, 422, "unknown_user", "The requested user is unavailable.")
                return finish_idempotent_request(record.id, response)
    run = await create_reconciliation_preview(
        actor=principal["username"], user_id=payload.user_id, target_id=target_id,
    )
    response = api_response(request, _reconciliation_payload(run, detail=True), status_code=201)
    return finish_idempotent_request(record.id, response)


@router.get("/reconciliation/{run_id}")
async def reconciliation_detail(request: Request, run_id: str):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None:
            return api_error(request, 404, "not_found", "The reconciliation run was not found.")
        run.findings
        payload = _reconciliation_payload(run, detail=True)
    return api_response(request, payload)


@router.post("/reconciliation/{run_id}/approve")
async def reconciliation_approve(
    request: Request, background_tasks: BackgroundTasks,
    run_id: str, payload: ReconciliationApprovalInput,
):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    record, replay = begin_idempotent_request(
        request, actor=principal["username"], idempotency_key=payload.idempotency_key,
        payload={"run_id": run_id, **payload.model_dump()},
    )
    if replay:
        return replay
    try:
        run, operation = approve_reconciliation(
            run_id, actor=principal["username"], approval_token=payload.approval_token,
            confirm_destructive=payload.confirm_destructive,
        )
    except ReconciliationApprovalError as error:
        status = 404 if error.code == "not_found" else 409 if error.code in {
            "approval_already_handled", "nothing_to_repair"
        } else 422
        response = api_error(request, status, error.code, str(error))
        return finish_idempotent_request(record.id, response)
    background_tasks.add_task(execute_reconciliation_repair, run_id, principal["username"])
    response = api_response(
        request,
        {"run": _reconciliation_payload(run), "operation_id": operation.id},
        status_code=202,
    )
    return finish_idempotent_request(record.id, response, operation_id=operation.id)


@router.get("/audit")
async def audit_list(request: Request):
    principal = api_guard(request, VIEW_AUDIT)
    if isinstance(principal, JSONResponse):
        return principal
    params = AuditParams.parse(request.query_params)
    maximum = get_settings().file.automation_api_policy.max_page_size
    if params.per_page > maximum:
        params = AuditParams(**{**params.__dict__, "per_page": maximum})
    with get_session() as db:
        audit = query_audit(db, params)
        data = [{
            "id": item.event.id,
            "at": _iso(item.event.at),
            "actor": item.event.actor,
            "action": item.event.action,
            "summary": item.summary,
            "subject": item.event.subject,
            "outcome": item.outcome,
            "operation_id": item.event.operation_id,
            "detail": safe_detail(item.event.detail),
        } for item in audit.items]
    return api_response(
        request, data,
        meta=page_meta(
            page=audit.params.page, per_page=audit.params.per_page,
            total=audit.total, pages=audit.pages,
        ),
    )
