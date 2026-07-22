"""Persistent reconciliation previews, approvals, repairs, and scheduling."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.connectors import get_connectors
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.lifecycle import LifecycleCommand, OperationStatus, SyncStateValue
from na_sso.models import (
    LifecycleOperation,
    ManagedUser,
    ReconciliationFinding,
    ReconciliationRun,
    SyncState,
    as_utc,
    utcnow,
)
from na_sso.notifications import enqueue_notification
from na_sso.operations import OperationConflict, request_operation
from na_sso.permissions import MANAGE_USERS, permission_context
from na_sso.reconciliation import (
    DriftState,
    ReconciliationField,
    ReconciliationReport,
    ReconciliationStatus,
    mark_unsupported_operation,
)
from na_sso.sync import sync_user


router = APIRouter(prefix="/reconciliation")
_schedule_lock = asyncio.Lock()


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_USERS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _assigned_pairs(
    run: ReconciliationRun,
) -> list[tuple[ManagedUser, str, frozenset[str], str]]:
    limit = get_settings().file.reconciliation_policy.max_users_per_run
    connectors = {connector.target_id: connector for connector in get_connectors()}
    with get_session() as db:
        from na_sso.assignments import resolve_assignment_intents
        query = db.query(ManagedUser).filter(ManagedUser.role != "root")
        if run.scope_user_id is not None:
            query = query.filter(ManagedUser.id == run.scope_user_id)
        users = query.order_by(ManagedUser.username, ManagedUser.id).limit(limit + 1).all()
        if len(users) > limit:
            raise ValueError(f"preview exceeds the configured {limit}-user limit")
        pairs: list[tuple[ManagedUser, str, frozenset[str], str]] = []
        for user in users:
            intents = resolve_assignment_intents(db, user, connectors)
            states = db.query(SyncState).filter_by(user_id=user.id, assigned=True, retired=False).all()
            for state in states:
                if run.scope_target_id and state.target != run.scope_target_id:
                    continue
                connector = connectors.get(state.target)
                if connector:
                    pairs.append((
                        user, state.target,
                        intents.get(state.target, connector.default_memberships),
                        connector.lifecycle_operation_for(
                            user,
                            disable=state.state in {
                                SyncStateValue.PENDING_DISABLE.value,
                                SyncStateValue.PENDING_CHPW_DISABLE.value,
                                SyncStateValue.PENDING_EXPIRY_DISABLE.value,
                            },
                        ),
                    ))
        detached: set[int] = set()
        for user, _target, _memberships, _operation in pairs:
            if user.id not in detached:
                db.expunge(user)
                detached.add(user.id)
        return pairs


async def refresh_reconciliation(run_id: str) -> ReconciliationRun | None:
    """Refresh one run's read-only findings; scheduled retries reuse the run ID."""
    connectors = {connector.target_id: connector for connector in get_connectors()}
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None:
            return None
        run.status = "discovering"
        run.attempt_count += 1
        db.query(ReconciliationFinding).filter_by(run_id=run.id).delete()
        db.commit()

    try:
        pairs = _assigned_pairs(run)
    except ValueError as error:
        with get_session() as db:
            stored = db.get(ReconciliationRun, run_id)
            stored.status = "failed"
            stored.detail = str(error)
            stored.next_attempt_at = None
            db.commit()
            return stored

    reports: list[tuple[ManagedUser, str, ReconciliationReport]] = []
    semaphore = asyncio.Semaphore(10)

    async def inspect(
        user: ManagedUser,
        target_id: str,
        memberships: frozenset[str],
        operation: str,
    ) -> None:
        connector = connectors.get(target_id)
        if connector is None:
            return
        async with semaphore:
            report = await connector.inspect_user_for_assignment(user, memberships)
            if not connector.supports_operation(operation):
                report = mark_unsupported_operation(
                    report,
                    connector.unsupported_operation_detail(operation),
                )
            reports.append((
                user, target_id,
                report,
            ))

    await asyncio.gather(*(
        inspect(user, target_id, memberships, operation)
        for user, target_id, memberships, operation in pairs
    ))

    drifted = unknown = destructive = 0
    with get_session() as db:
        stored = db.get(ReconciliationRun, run_id)
        if stored is None:
            return None
        for user, target_id, report in reports:
            if report.status == ReconciliationStatus.DRIFTED:
                drifted += 1
            elif report.status == ReconciliationStatus.UNKNOWN:
                unknown += 1
            identity = report.field(ReconciliationField.IDENTITY)
            if identity.state == DriftState.DRIFT and identity.desired == "absent":
                destructive += 1
            for comparison in report.fields:
                db.add(ReconciliationFinding(
                    run_id=stored.id,
                    user_id=user.id,
                    username=user.username,
                    target_id=target_id,
                    target_name=report.target_name,
                    field=comparison.field.value,
                    state=comparison.state.value,
                    desired=comparison.desired,
                    actual=comparison.actual,
                    detail=comparison.detail,
                ))
        stored.total_targets = len(reports)
        stored.drifted_targets = drifted
        stored.unknown_targets = unknown
        stored.destructive_targets = destructive
        stored.detail = (
            f"Inspected {len(reports)} assigned account target(s); "
            f"{drifted} drifted and {unknown} could not be fully read."
        )
        policy = get_settings().file.reconciliation_policy
        if stored.source == "scheduled" and unknown:
            if stored.attempt_count >= policy.max_attempts:
                stored.status = "failed"
                stored.next_attempt_at = None
                stored.detail += " Scheduled discovery retry limit reached."
            else:
                delay = min(
                    policy.retry_base_seconds * (2 ** (stored.attempt_count - 1)),
                    policy.retry_max_seconds,
                )
                stored.status = "retrying"
                stored.next_attempt_at = _now() + timedelta(seconds=delay)
        else:
            stored.status = "previewed"
            stored.next_attempt_at = None
        record_audit(
            db,
            stored.actor,
            "reconcile.previewed",
            f"reconciliation:{stored.id}",
            f"source={stored.source}; targets={len(reports)}; drifted={drifted}; unknown={unknown}",
        )
        db.commit()
        stored.findings
        return stored


async def create_reconciliation_preview(
    *,
    actor: str,
    source: str = "manual",
    user_id: int | None = None,
    target_id: str | None = None,
) -> ReconciliationRun:
    with get_session() as db:
        run = ReconciliationRun(
            actor=actor,
            source=source,
            scope_user_id=user_id,
            scope_target_id=target_id or None,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    refreshed = await refresh_reconciliation(run_id)
    if refreshed is None:
        raise RuntimeError("reconciliation run disappeared")
    return refreshed


class ReconciliationApprovalError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def approve_reconciliation(
    run_id: str,
    *,
    actor: str,
    approval_token: str,
    confirm_destructive: bool = False,
) -> tuple[ReconciliationRun, LifecycleOperation]:
    """Apply the shared one-use approval contract without starting repair work."""
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None:
            raise ReconciliationApprovalError("not_found", "The reconciliation preview was not found.")
        if approval_token != run.approval_token:
            raise ReconciliationApprovalError(
                "invalid_approval", "The saved preview approval token is invalid."
            )
        if run.status != "previewed":
            raise ReconciliationApprovalError(
                "approval_already_handled",
                "This preview has already been approved or is no longer repairable.",
            )
        if run.drifted_targets == 0:
            raise ReconciliationApprovalError(
                "nothing_to_repair", "The preview contains no drifted target accounts."
            )
        if run.destructive_targets and not confirm_destructive:
            raise ReconciliationApprovalError(
                "destructive_confirmation_required",
                "Confirm remote account deletion before starting this repair.",
            )
        operation = LifecycleOperation(
            command=LifecycleCommand.RECONCILE.value,
            status=OperationStatus.QUEUED.value,
            actor=actor,
            subject=f"reconciliation:{run.id}",
            total_targets=run.drifted_targets,
        )
        db.add(operation)
        db.flush()
        run.operation_id = operation.id
        run.status = "approved"
        record_audit(
            db, actor, "reconcile.approved", f"reconciliation:{run.id}",
            f"targets={run.drifted_targets}; destructive={run.destructive_targets}",
            operation.id,
        )
        db.commit()
        run.findings
        db.expunge(operation)
        db.expunge(run)
        return run, operation


def _repair_groups(run_id: str) -> list[tuple[int, str]]:
    with get_session() as db:
        rows = db.query(ReconciliationFinding).filter_by(
            run_id=run_id, state=DriftState.DRIFT.value
        ).order_by(
            ReconciliationFinding.username, ReconciliationFinding.target_id
        ).all()
        return list(dict.fromkeys((row.user_id, row.target_id) for row in rows))


async def execute_reconciliation_repair(run_id: str, actor: str) -> None:
    connectors = {connector.target_id: connector for connector in get_connectors()}
    groups = _repair_groups(run_id)
    succeeded = failed = 0
    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None or run.status not in {"approved", "running"}:
            return
        run.status = "running"
        parent = db.get(LifecycleOperation, run.operation_id) if run.operation_id else None
        if parent:
            parent.status = OperationStatus.RUNNING.value
            parent.started_at = parent.started_at or utcnow()
        db.commit()

    for user_id, target_id in groups:
        with get_session() as db:
            run = db.get(ReconciliationRun, run_id)
            user = db.get(ManagedUser, user_id)
            findings = db.query(ReconciliationFinding).filter_by(
                run_id=run_id, user_id=user_id, target_id=target_id,
            ).all()
            drifts = [item for item in findings if item.state == DriftState.DRIFT.value]
            connector = connectors.get(target_id)
            if user is None or connector is None:
                for item in drifts:
                    item.repair_status = "failed"
                failed += 1
                db.commit()
                continue
            missing_identity = any(
                item.field == ReconciliationField.IDENTITY.value and item.actual == "absent"
                for item in drifts
            )
            if missing_identity and not user.pending_secret:
                for item in drifts:
                    item.repair_status = "blocked_credentials"
                failed += 1
                record_audit(
                    db, actor, "reconcile.repair_blocked", user.username,
                    f"{target_id}: remote identity creation requires a current credential",
                    run.operation_id,
                )
                db.commit()
                continue
            try:
                operation = request_operation(
                    db, user, LifecycleCommand.RECONCILE, actor,
                    requested_target=target_id,
                )
            except OperationConflict as error:
                for item in drifts:
                    item.repair_status = "conflict"
                    item.detail = str(error)
                failed += 1
                db.commit()
                continue
            operation.parent_id = run.operation_id
            for item in drifts:
                item.repair_status = "running"
                item.operation_id = operation.id
            operation_id = operation.id
            desired_delete = user.desired_action == "delete" or user.deleted_at is not None
            db.commit()

        await sync_user(
            user_id,
            action="delete" if desired_delete else None,
            target=target_id,
            actor=actor,
            operation_id=operation_id,
        )

        with get_session() as db:
            operation = db.get(LifecycleOperation, operation_id)
            repaired = bool(operation and operation.status == OperationStatus.SUCCEEDED.value)
            findings = db.query(ReconciliationFinding).filter_by(
                run_id=run_id, user_id=user_id, target_id=target_id,
                state=DriftState.DRIFT.value,
            ).all()
            for item in findings:
                item.repair_status = "repaired" if repaired else "failed"
            if repaired:
                succeeded += 1
            else:
                failed += 1
            db.commit()

    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None:
            return
        parent = db.get(LifecycleOperation, run.operation_id) if run.operation_id else None
        final_status = (
            OperationStatus.SUCCEEDED if failed == 0
            else OperationStatus.PARTIALLY_FAILED if succeeded else OperationStatus.FAILED
        )
        if parent:
            parent.status = final_status.value
            parent.completed_targets = succeeded
            parent.failed_targets = failed
            parent.detail = f"repaired={succeeded}; failed={failed}"
            parent.completed_at = utcnow()
        run.status = (
            "completed" if failed == 0 else "partially_failed" if succeeded else "failed"
        )
        run.completed_at = utcnow()
        run.detail = f"Repair completed: {succeeded} target(s) repaired; {failed} failed or blocked."
        record_audit(
            db, actor, "reconcile.completed", f"reconciliation:{run.id}",
            run.detail, run.operation_id,
        )
        enqueue_notification(
            db, "approval.completed", actor=actor,
            subject=f"reconciliation:{run.id}", dedupe_key=f"reconcile:{run.id}",
            operation_id=run.operation_id, outcome=run.status,
        )
        db.commit()


async def run_scheduled_reconciliation() -> int:
    policy = get_settings().file.reconciliation_policy
    if not policy.enabled:
        return 0
    async with _schedule_lock:
        now = _now()
        with get_session() as db:
            due = db.query(ReconciliationRun).filter(
                ReconciliationRun.source == "scheduled",
                ReconciliationRun.status == "retrying",
                ReconciliationRun.next_attempt_at <= now,
            ).order_by(ReconciliationRun.next_attempt_at).first()
            latest = db.query(ReconciliationRun).filter_by(source="scheduled").order_by(
                ReconciliationRun.created_at.desc()
            ).first()
            due_id = due.id if due else None
            latest_at = as_utc(latest.created_at) if latest else None
        if due_id:
            await refresh_reconciliation(due_id)
            return 1
        if latest_at and latest_at + timedelta(seconds=policy.interval_seconds) > now:
            return 0
        await create_reconciliation_preview(actor="system", source="scheduled")
        return 1


async def reconciliation_worker() -> None:
    while True:
        await asyncio.sleep(get_settings().file.reconciliation_policy.scan_seconds)
        await run_scheduled_reconciliation()


@router.get("")
async def reconciliation_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates

    with get_session() as db:
        runs = db.query(ReconciliationRun).order_by(
            ReconciliationRun.created_at.desc()
        ).limit(25).all()
        users = db.query(ManagedUser).filter(
            ManagedUser.role != "root", ManagedUser.deleted_at.is_(None)
        ).order_by(ManagedUser.username).limit(100).all()
    return template_response(templates, request, "reconciliation.html", {
        "admin": principal["username"],
        "admin_area": True,
        "active_nav": "reconciliation",
        "permissions": permission_context(principal["role"]),
        "runs": runs,
        "users": users,
        "targets": get_connectors(),
        "policy": get_settings().file.reconciliation_policy,
    })


@router.post("/preview")
async def reconciliation_preview(
    request: Request,
    user_id: int = Form(0),
    target_id: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    run = await create_reconciliation_preview(
        actor=principal["username"],
        user_id=user_id or None,
        target_id=target_id.strip() or None,
    )
    return RedirectResponse(f"/reconciliation/{run.id}", status_code=303)


@router.get("/{run_id}")
async def reconciliation_detail(request: Request, run_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates

    with get_session() as db:
        run = db.get(ReconciliationRun, run_id)
        if run is None:
            return RedirectResponse("/reconciliation", status_code=303)
        findings = db.query(ReconciliationFinding).filter_by(run_id=run.id).order_by(
            ReconciliationFinding.username,
            ReconciliationFinding.target_name,
            ReconciliationFinding.id,
        ).all()
    return template_response(templates, request, "reconciliation_detail.html", {
        "admin": principal["username"],
        "admin_area": True,
        "active_nav": "reconciliation",
        "permissions": permission_context(principal["role"]),
        "run": run,
        "findings": findings,
    })


@router.post("/{run_id}/approve")
async def reconciliation_approve(
    request: Request,
    background_tasks: BackgroundTasks,
    run_id: str,
    approval_token: str = Form(...),
    confirm_destructive: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    actor = principal["username"]
    try:
        _run, operation = approve_reconciliation(
            run_id,
            actor=actor,
            approval_token=approval_token,
            confirm_destructive=confirm_destructive == "yes",
        )
    except ReconciliationApprovalError as error:
        if error.code == "not_found":
            return RedirectResponse("/reconciliation", status_code=303)
        titles = {
            "invalid_approval": "Approval rejected",
            "approval_already_handled": "Approval already handled",
            "nothing_to_repair": "Nothing to repair",
            "destructive_confirmation_required": "Destructive approval required",
        }
        return redirect_with_feedback(
            f"/reconciliation/{run_id}",
            title=titles.get(error.code, "Approval rejected"),
            message=str(error),
            level="danger" if error.code in {
                "invalid_approval", "destructive_confirmation_required"
            } else "info",
        )
    background_tasks.add_task(execute_reconciliation_repair, run_id, actor)
    return redirect_with_feedback(
        f"/reconciliation/{run_id}", title="Repair approved",
        message=f"The correlated repair has started. Correlation {operation.id[:8]}.",
        level="success",
    )
