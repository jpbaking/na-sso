"""Persistence helpers for correlated lifecycle operations and attempts."""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from na_sso.lifecycle import (
    DesiredAction,
    LifecycleCommand,
    OperationStatus,
    SyncStateValue,
    TransitionMode,
    decide_transition,
    sync_state_is_terminal,
)
from na_sso.models import LifecycleOperation, ManagedUser, OperationTargetAttempt, SyncState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_active_operation(db: Session, user: ManagedUser) -> LifecycleOperation | None:
    if not user.active_operation_id:
        return None
    operation = db.get(LifecycleOperation, user.active_operation_id)
    if not operation or OperationStatus(operation.status).terminal:
        user.active_operation_id = None
        return None
    return operation


def get_latest_operation(
    db: Session, user: ManagedUser, command: LifecycleCommand | str | None = None
) -> LifecycleOperation | None:
    query = db.query(LifecycleOperation).filter_by(user_id=user.id)
    if command is not None:
        query = query.filter_by(command=LifecycleCommand(command).value)
    return query.order_by(
        LifecycleOperation.created_at.desc(), LifecycleOperation.id.desc()
    ).first()


def operation_payload(
    operation: LifecycleOperation | None, states: list[SyncState] | None = None
) -> dict | None:
    if operation is None:
        return None
    desired = (
        DesiredAction.DELETE
        if operation.command == LifecycleCommand.DELETE.value
        else DesiredAction.ENSURE
    )
    blocking_targets = []
    for state in states or []:
        if state.operation_id != operation.id:
            continue
        if state.state in {
            SyncStateValue.FAILED.value, SyncStateValue.UNSUPPORTED.value,
        } or not sync_state_is_terminal(
            state.state,
            operation=desired,
            assigned=state.assigned,
            retired=state.retired,
        ):
            blocking_targets.append(state.target)
    return {
        "id": operation.id,
        "command": operation.command,
        "status": operation.status,
        "total_targets": operation.total_targets,
        "completed_targets": operation.completed_targets,
        "failed_targets": operation.failed_targets,
        "blocking_targets": blocking_targets,
        "detail": operation.detail,
        "started_at": operation.started_at.isoformat() if operation.started_at else None,
        "completed_at": operation.completed_at.isoformat() if operation.completed_at else None,
    }


def create_operation(
    db: Session,
    user: ManagedUser,
    command: LifecycleCommand | str,
    actor: str,
    *,
    requested_target: str | None = None,
    supersedes: LifecycleOperation | None = None,
) -> LifecycleOperation:
    operation = LifecycleOperation(
        user_id=user.id,
        command=LifecycleCommand(command).value,
        status=OperationStatus.QUEUED.value,
        actor=actor,
        subject=user.username,
        requested_target=requested_target,
        supersedes_id=supersedes.id if supersedes else None,
    )
    db.add(operation)
    db.flush()
    user.active_operation_id = operation.id
    return operation


class OperationConflict(ValueError):
    pass


def request_operation(
    db: Session,
    user: ManagedUser,
    command: LifecycleCommand | str,
    actor: str,
    *,
    requested_target: str | None = None,
) -> LifecycleOperation:
    command = LifecycleCommand(command)
    active = get_active_operation(db, user)
    decision = decide_transition(
        command,
        active_command=LifecycleCommand(active.command) if active else None,
        active_status=OperationStatus(active.status) if active else None,
        delete_complete=user.deleted_at is not None,
    )
    if decision.mode is TransitionMode.REJECT:
        raise OperationConflict(decision.reason)
    if decision.mode is TransitionMode.REUSE:
        if active is None:
            raise OperationConflict("operation to reuse is unavailable")
        return active
    supersedes = None
    if decision.mode is TransitionMode.SUPERSEDE and active is not None:
        supersedes = active
        finish_operation(
            user,
            active,
            OperationStatus.CANCELLED,
            completed_targets=active.completed_targets,
            failed_targets=active.failed_targets,
            detail=f"superseded by {command.value}",
        )
    return create_operation(
        db,
        user,
        command,
        actor,
        requested_target=requested_target,
        supersedes=supersedes,
    )


def resume_operation(
    user: ManagedUser,
    operation: LifecycleOperation,
    *,
    requested_target: str | None = None,
) -> None:
    if OperationStatus(operation.status) not in {
        OperationStatus.FAILED,
        OperationStatus.PARTIALLY_FAILED,
        OperationStatus.QUEUED,
        OperationStatus.RUNNING,
    }:
        raise OperationConflict("only queued, running, or failed operations can resume")
    operation.status = OperationStatus.RUNNING.value
    operation.completed_at = None
    if requested_target is not None:
        operation.requested_target = requested_target
    if operation.started_at is None:
        operation.started_at = _now()
    user.active_operation_id = operation.id


def start_operation(operation: LifecycleOperation, total_targets: int) -> None:
    operation.status = OperationStatus.RUNNING.value
    operation.total_targets = total_targets
    operation.completed_targets = 0
    operation.failed_targets = 0
    operation.started_at = _now()
    operation.completed_at = None


def start_target_attempt(
    db: Session,
    operation: LifecycleOperation,
    *,
    target: str,
    target_type: str | None,
) -> OperationTargetAttempt:
    previous = db.query(func.max(OperationTargetAttempt.attempt_number)).filter_by(
        operation_id=operation.id, target=target
    ).scalar()
    attempt = OperationTargetAttempt(
        operation_id=operation.id,
        target=target,
        target_type=target_type,
        attempt_number=(previous or 0) + 1,
        status=OperationStatus.RUNNING.value,
    )
    db.add(attempt)
    return attempt


def finish_target_attempt(
    attempt: OperationTargetAttempt,
    *,
    succeeded: bool,
    result_state: str,
    detail: str,
) -> None:
    attempt.status = OperationStatus.SUCCEEDED.value if succeeded else OperationStatus.FAILED.value
    attempt.result_state = result_state
    attempt.detail = detail
    attempt.completed_at = _now()


def finish_operation(
    user: ManagedUser,
    operation: LifecycleOperation,
    status: OperationStatus,
    *,
    completed_targets: int,
    failed_targets: int,
    detail: str = "",
) -> None:
    if not status.terminal:
        raise ValueError("finished operation requires a terminal status")
    operation.status = status.value
    operation.completed_targets = completed_targets
    operation.failed_targets = failed_targets
    operation.detail = detail
    operation.completed_at = _now()
    if user.active_operation_id == operation.id:
        user.active_operation_id = None
