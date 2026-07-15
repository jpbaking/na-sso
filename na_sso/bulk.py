"""CSV and JSON bulk onboarding/offboarding with durable previews and results."""

from __future__ import annotations

import csv
import io
import json
import re
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, Security, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from na_sso.auth import permission_guard
from na_sso.api_contract import api_error, api_guard, api_response
from na_sso.audit import record_audit
from na_sso.connectors import get_connectors, validate_for_targets
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.lifecycle import LifecycleCommand, OperationStatus
from na_sso.models import BulkWorkflow, BulkWorkflowRow, LifecycleOperation, ManagedUser, utcnow
from na_sso.notifications import enqueue_notification
from na_sso.operations import OperationConflict, request_operation
from na_sso.permissions import MANAGE_USERS, permission_context
from na_sso.security import decrypt_secret, encrypt_secret, generate_password, hash_password
from na_sso.sync import sync_user
from na_sso.users import USERNAME_RE, _set_pending


router = APIRouter()
_api_bearer = HTTPBearer(auto_error=False)
MAX_BULK_ROWS = 1000
MAX_CSV_BYTES = 1024 * 1024
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class BulkApiRow(BaseModel):
    username: str = Field(max_length=64)
    action: str
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=254)
    target_ids: list[str] = []


class BulkApiPreviewRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    rows: list[BulkApiRow] = Field(min_length=1, max_length=MAX_BULK_ROWS)


class BulkApiExecuteRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_USERS)


def _target_ids(value: object) -> list[str]:
    if isinstance(value, list):
        values = [str(item).strip() for item in value]
    else:
        values = [item.strip() for item in re.split(r"[|;,]", str(value or ""))]
    return list(dict.fromkeys(item for item in values if item))


def _row_dict(value: BulkApiRow | dict) -> dict:
    return value.model_dump() if isinstance(value, BulkApiRow) else dict(value)


def preview_bulk_workflow(
    *, actor: str, source: str, idempotency_key: str, rows: list[BulkApiRow | dict]
) -> BulkWorkflow:
    if not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise ValueError("idempotency key must be 8–128 safe characters")
    if not rows or len(rows) > MAX_BULK_ROWS:
        raise ValueError(f"bulk input must contain 1–{MAX_BULK_ROWS} rows")
    connectors = {item.target_id: item for item in get_connectors()}
    with get_session() as db:
        existing_workflow = db.query(BulkWorkflow).filter_by(
            actor=actor, idempotency_key=idempotency_key
        ).one_or_none()
        if existing_workflow:
            existing_workflow.rows
            return existing_workflow
        usernames = [str(_row_dict(row).get("username", "")).strip().lower() for row in rows]
        existing_users = {
            user.username: user
            for user in db.query(ManagedUser).filter(ManagedUser.username.in_(usernames)).all()
        }
        workflow = BulkWorkflow(
            actor=actor, source=source, idempotency_key=idempotency_key,
            row_count=len(rows),
        )
        db.add(workflow)
        db.flush()
        seen: set[str] = set()
        valid_count = 0
        for number, source_row in enumerate(rows, 1):
            raw = _row_dict(source_row)
            username = str(raw.get("username", "")).strip().lower()
            action = str(raw.get("action", "")).strip().lower()
            target_ids = _target_ids(raw.get("target_ids", raw.get("targets", "")))
            existing = existing_users.get(username)
            display_name = str(raw.get("display_name", "")).strip()
            email = str(raw.get("email", "")).strip()
            errors: list[str] = []
            if username in seen:
                errors.append("duplicate username in this import")
            seen.add(username)
            if not USERNAME_RE.fullmatch(username):
                errors.append("invalid managed username")
            if action not in {"onboard", "offboard"}:
                errors.append("action must be onboard or offboard")
            unknown_targets = [target for target in target_ids if target not in connectors]
            if unknown_targets:
                errors.append("unknown target(s): " + ", ".join(unknown_targets))
            if action == "onboard" and not target_ids:
                errors.append("onboarding requires at least one target")
            if action == "offboard" and existing is None:
                errors.append("offboarding requires an existing account")
            if existing and (existing.is_root or existing.role != "user" or existing.desired_action == "delete"):
                errors.append("account is protected or unavailable")
            if len(display_name) > 128 or len(email) > 254 or any(ch in email for ch in "\r\n"):
                errors.append("profile fields exceed safe bounds")
            if action == "onboard" and not errors:
                proposed = ManagedUser(
                    username=username,
                    display_name=display_name or (existing.display_name if existing else ""),
                    email=email or (existing.email if existing else ""),
                )
                validation = validate_for_targets(
                    proposed, [connectors[target] for target in target_ids]
                )
                if not validation.ok:
                    errors.append(validation.detail)
                display_name, email = proposed.display_name, proposed.email
            if action == "offboard" and existing and not target_ids:
                target_ids = [
                    state.target for state in existing.sync_states
                    if state.assigned and not state.retired
                ]
                if not target_ids:
                    errors.append("account has no assigned targets")
            elif action == "offboard" and existing and target_ids:
                assigned = {
                    state.target for state in existing.sync_states
                    if state.assigned and not state.retired
                }
                not_assigned = [target for target in target_ids if target not in assigned]
                if not_assigned:
                    errors.append("target(s) not assigned: " + ", ".join(not_assigned))
            status = "invalid" if errors else "valid"
            valid_count += status == "valid"
            db.add(BulkWorkflowRow(
                workflow_id=workflow.id,
                row_number=number,
                action=action,
                username=username,
                display_name=display_name,
                email=email,
                target_ids=json.dumps(target_ids),
                user_id=existing.id if existing else None,
                validation_status=status,
                result_status="invalid" if errors else "pending",
                detail="; ".join(errors),
            ))
        workflow.valid_count = valid_count
        workflow.failed_count = len(rows) - valid_count
        workflow.detail = f"{valid_count} valid row(s); {len(rows) - valid_count} invalid row(s). No changes made."
        record_audit(
            db, actor, "bulk.import_previewed", f"bulk-import:{workflow.id}",
            f"source={source}; rows={len(rows)}; valid={valid_count}",
        )
        db.commit()
        workflow.rows
        return workflow


def _row_targets(row: BulkWorkflowRow) -> list[str]:
    try:
        value = json.loads(row.target_ids)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def prepare_bulk_execution(workflow_id: str, *, actor: str, idempotency_key: str) -> BulkWorkflow:
    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        if workflow is None or workflow.actor != actor:
            raise ValueError("bulk workflow not found")
        if workflow.idempotency_key != idempotency_key:
            raise ValueError("idempotency key does not match the saved preview")
        if workflow.status != "previewed":
            workflow.rows
            return workflow
        parent = LifecycleOperation(
            command=LifecycleCommand.BULK.value,
            status=OperationStatus.RUNNING.value,
            actor=actor,
            subject=f"bulk-import:{workflow.id}",
            total_targets=workflow.row_count,
            started_at=utcnow(),
        )
        db.add(parent)
        db.flush()
        workflow.operation_id = parent.id
        workflow.status = "running"
        rows = db.query(BulkWorkflowRow).filter_by(workflow_id=workflow.id).order_by(
            BulkWorkflowRow.row_number
        ).all()
        for row in rows:
            if row.validation_status != "valid":
                continue
            targets = _row_targets(row)
            user = db.query(ManagedUser).filter_by(username=row.username).one_or_none()
            if user and (user.is_root or user.role != "user" or user.desired_action == "delete"):
                row.result_status = "failed"
                row.detail = "account became protected or unavailable after preview"
                continue
            try:
                if row.action == "onboard":
                    new_account = user is None
                    if new_account:
                        temporary_password = generate_password()
                        while temporary_password[0] in "=+-@":
                            temporary_password = generate_password()
                        user = ManagedUser(
                            username=row.username,
                            display_name=row.display_name,
                            email=row.email,
                            password_hash=hash_password(temporary_password),
                            password_decision_required=True,
                            password_decision_kind="initial",
                            status="active",
                            desired_action="ensure",
                        )
                        db.add(user)
                        db.flush()
                        row.user_id = user.id
                        row.encrypted_temporary_password = encrypt_secret(temporary_password)
                    else:
                        user.display_name = row.display_name
                        user.email = row.email
                    assigned = {
                        state.target for state in user.sync_states
                        if state.assigned and not state.retired
                    }
                    assigned.update(targets)
                    _set_pending(
                        db, user, None, assigned,
                        require_password_change=new_account,
                        assignment_actor=actor,
                    )
                    operation = request_operation(
                        db, user,
                        LifecycleCommand.CREATE if new_account else LifecycleCommand.UPDATE,
                        actor,
                    )
                else:
                    if user is None:
                        raise ValueError("account no longer exists")
                    assigned = {
                        state.target for state in user.sync_states
                        if state.assigned and not state.retired
                    }
                    assigned.difference_update(targets)
                    operation = request_operation(db, user, LifecycleCommand.UPDATE, actor)
                    _set_pending(db, user, None, assigned, assignment_actor=actor)
                operation.parent_id = parent.id
                row.user_id = user.id
                row.operation_id = operation.id
                row.result_status = "queued"
                row.detail = "accepted for correlated execution"
                record_audit(
                    db, actor, f"bulk.import_{row.action}", row.username,
                    f"targets={','.join(targets)}; row={row.row_number}", parent.id,
                )
            except (OperationConflict, ValueError) as error:
                row.result_status = "failed"
                row.detail = str(error)
        record_audit(
            db, actor, "bulk.import_approved", f"bulk-import:{workflow.id}",
            f"rows={workflow.row_count}; valid={workflow.valid_count}", parent.id,
        )
        db.commit()
        workflow.rows
        return workflow


async def run_bulk_workflow(workflow_id: str) -> None:
    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        actor = workflow.actor if workflow else "system"
        rows = db.query(BulkWorkflowRow).filter_by(
            workflow_id=workflow_id, result_status="queued"
        ).order_by(BulkWorkflowRow.row_number).all()
        queued = [(row.id, row.user_id, row.operation_id) for row in rows]
    for row_id, user_id, operation_id in queued:
        if user_id is not None and operation_id:
            await sync_user(user_id, actor=actor, operation_id=operation_id)
        with get_session() as db:
            row = db.get(BulkWorkflowRow, row_id)
            operation = db.get(LifecycleOperation, operation_id) if operation_id else None
            row.result_status = (
                "succeeded"
                if operation and operation.status == OperationStatus.SUCCEEDED.value
                else "failed"
            )
            row.detail = operation.detail if operation and operation.detail else row.detail
            db.commit()

    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        if workflow is None or workflow.status != "running":
            return
        rows = db.query(BulkWorkflowRow).filter_by(workflow_id=workflow.id).all()
        succeeded = sum(row.result_status == "succeeded" for row in rows)
        failed = len(rows) - succeeded
        workflow.succeeded_count = succeeded
        workflow.failed_count = failed
        workflow.status = (
            "completed" if failed == 0 else "partially_failed" if succeeded else "failed"
        )
        workflow.completed_at = utcnow()
        workflow.detail = f"{succeeded} row(s) succeeded; {failed} failed validation or execution."
        parent = db.get(LifecycleOperation, workflow.operation_id) if workflow.operation_id else None
        if parent:
            parent.completed_targets = succeeded
            parent.failed_targets = failed
            parent.status = (
                OperationStatus.SUCCEEDED.value if failed == 0
                else OperationStatus.PARTIALLY_FAILED.value if succeeded
                else OperationStatus.FAILED.value
            )
            parent.detail = workflow.detail
            parent.completed_at = workflow.completed_at
        record_audit(
            db, workflow.actor, "bulk.import_completed", f"bulk-import:{workflow.id}",
            workflow.detail, workflow.operation_id,
        )
        enqueue_notification(
            db, "approval.completed", actor=workflow.actor,
            subject=f"bulk-import:{workflow.id}", dedupe_key=f"bulk-import:{workflow.id}",
            operation_id=workflow.operation_id, outcome=workflow.status,
        )
        db.commit()


def _workflow_payload(workflow: BulkWorkflow) -> dict:
    return {
        "id": workflow.id,
        "idempotency_key": workflow.idempotency_key,
        "status": workflow.status,
        "row_count": workflow.row_count,
        "valid_count": workflow.valid_count,
        "succeeded_count": workflow.succeeded_count,
        "failed_count": workflow.failed_count,
        "operation_id": workflow.operation_id,
        "detail": workflow.detail,
        "rows": [{
            "row": row.row_number,
            "username": row.username,
            "action": row.action,
            "target_ids": _row_targets(row),
            "validation": row.validation_status,
            "result": row.result_status,
            "detail": row.detail,
            "operation_id": row.operation_id,
            "temporary_credential_available": bool(row.encrypted_temporary_password),
        } for row in sorted(workflow.rows, key=lambda item: item.row_number)],
    }


def _load_owned_workflow(workflow_id: str, actor: str) -> BulkWorkflow | None:
    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        if workflow is None or workflow.actor != actor:
            return None
        workflow.rows
        return workflow


def _parse_csv(upload: bytes) -> list[dict]:
    if len(upload) > MAX_CSV_BYTES:
        raise ValueError("CSV upload exceeds 1 MiB")
    try:
        text = upload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV must use UTF-8") from error
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    if not {"username", "action"} <= fields:
        raise ValueError("CSV requires username and action columns")
    rows = list(reader)
    if not rows or len(rows) > MAX_BULK_ROWS:
        raise ValueError(f"CSV must contain 1–{MAX_BULK_ROWS} data rows")
    return rows


def _csv_response(rows: list[dict], filename: str) -> Response:
    def safe(value):
        text = "" if value is None else str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

    output = io.StringIO(newline="")
    fieldnames = list(rows[0]) if rows else ["row", "username", "status", "detail"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows({key: safe(value) for key, value in row.items()} for row in rows)
    return Response(output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/users/bulk/import")
async def bulk_import_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        workflows = db.query(BulkWorkflow).filter_by(actor=principal["username"]).order_by(
            BulkWorkflow.created_at.desc()
        ).limit(20).all()
    return template_response(templates, request, "bulk_import.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "workflows": workflows, "max_rows": MAX_BULK_ROWS,
    })


@router.post("/users/bulk/import/preview")
async def bulk_import_preview(
    request: Request,
    csv_file: UploadFile = File(...),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    try:
        rows = _parse_csv(await csv_file.read(MAX_CSV_BYTES + 1))
        workflow = preview_bulk_workflow(
            actor=principal["username"], source="csv",
            idempotency_key=str(uuid4()), rows=rows,
        )
    except ValueError as error:
        return redirect_with_feedback(
            "/users/bulk/import", title="Import rejected",
            message=str(error), level="danger",
        )
    return RedirectResponse(f"/users/bulk/import/{workflow.id}", status_code=303)


@router.get("/users/bulk/import/{workflow_id}")
async def bulk_import_detail(request: Request, workflow_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    workflow = _load_owned_workflow(workflow_id, principal["username"])
    if workflow is None:
        return RedirectResponse("/users/bulk/import", status_code=303)
    return template_response(templates, request, "bulk_import_detail.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "workflow": workflow,
    })


@router.post("/users/bulk/import/{workflow_id}/execute")
async def bulk_import_execute(
    request: Request, background_tasks: BackgroundTasks, workflow_id: str,
    idempotency_key: str = Form(...),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    try:
        workflow = prepare_bulk_execution(
            workflow_id, actor=principal["username"], idempotency_key=idempotency_key,
        )
    except ValueError as error:
        return redirect_with_feedback(
            f"/users/bulk/import/{workflow_id}", title="Bulk execution rejected",
            message=str(error), level="danger",
        )
    if workflow.status == "running":
        background_tasks.add_task(run_bulk_workflow, workflow.id)
    return redirect_with_feedback(
        f"/users/bulk/import/{workflow.id}", title="Bulk workflow accepted",
        message=f"Execution is idempotent. Correlation {(workflow.operation_id or workflow.id)[:8]}.",
        level="success",
    )


@router.get("/users/bulk/import/{workflow_id}/results.{format}")
async def bulk_import_results(request: Request, workflow_id: str, format: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    workflow = _load_owned_workflow(workflow_id, principal["username"])
    if workflow is None:
        return Response("Not found", status_code=404)
    payload = _workflow_payload(workflow)
    if format == "json":
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    if format != "csv":
        return Response("Unsupported result format", status_code=404)
    rows = [{
        "row": item["row"], "username": item["username"], "action": item["action"],
        "target_ids": "|".join(item["target_ids"]), "validation": item["validation"],
        "result": item["result"], "detail": item["detail"],
        "operation_id": item["operation_id"] or "",
    } for item in payload["rows"]]
    return _csv_response(rows, f"na-sso-bulk-{workflow.id[:8]}-results.csv")


@router.post("/users/bulk/import/{workflow_id}/credentials.csv")
async def bulk_import_credentials(request: Request, workflow_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        workflow = db.get(BulkWorkflow, workflow_id)
        if workflow is None or workflow.actor != principal["username"]:
            return Response("Not found", status_code=404)
        rows = db.query(BulkWorkflowRow).filter(
            BulkWorkflowRow.workflow_id == workflow.id,
            BulkWorkflowRow.encrypted_temporary_password.is_not(None),
        ).order_by(BulkWorkflowRow.row_number).all()
        credentials = [{
            "username": row.username,
            "temporary_password": decrypt_secret(row.encrypted_temporary_password),
            "instruction": "User must change this password before target provisioning",
        } for row in rows]
        for row in rows:
            row.encrypted_temporary_password = None
        if rows:
            record_audit(
                db, principal["username"], "bulk.credentials_downloaded",
                f"bulk-import:{workflow.id}", f"accounts={len(rows)}",
                workflow.operation_id,
            )
        db.commit()
    if not credentials:
        return Response("No one-time credentials remain", status_code=410)
    return _csv_response(credentials, f"na-sso-bulk-{workflow_id[:8]}-credentials-once.csv")


@router.post("/api/v1/bulk/preview", dependencies=[Security(_api_bearer)])
async def bulk_api_preview(request: Request, payload: BulkApiPreviewRequest):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        workflow = preview_bulk_workflow(
            actor=principal["username"], source="api",
            idempotency_key=payload.idempotency_key, rows=payload.rows,
        )
    except ValueError as error:
        return api_error(request, 422, "bulk_preview_rejected", str(error))
    return api_response(request, _workflow_payload(workflow), status_code=201)


@router.post("/api/v1/bulk/{workflow_id}/execute", dependencies=[Security(_api_bearer)])
async def bulk_api_execute(
    request: Request, background_tasks: BackgroundTasks,
    workflow_id: str, payload: BulkApiExecuteRequest,
):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        workflow = prepare_bulk_execution(
            workflow_id, actor=principal["username"],
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as error:
        return api_error(request, 422, "bulk_execution_rejected", str(error))
    if workflow.status == "running":
        background_tasks.add_task(run_bulk_workflow, workflow.id)
    return api_response(request, _workflow_payload(workflow), status_code=202)


@router.get("/api/v1/bulk/{workflow_id}", dependencies=[Security(_api_bearer)])
async def bulk_api_detail(request: Request, workflow_id: str):
    principal = api_guard(request, MANAGE_USERS)
    if isinstance(principal, JSONResponse):
        return principal
    workflow = _load_owned_workflow(workflow_id, principal["username"])
    if workflow is None:
        return api_error(request, 404, "not_found", "The bulk workflow was not found.")
    return api_response(request, _workflow_payload(workflow))
