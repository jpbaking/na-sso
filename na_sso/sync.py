import asyncio
from datetime import datetime, timedelta, timezone

from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.connectors import get_connectors
from na_sso.connectors.base import ConnectorErrorKind
from na_sso.db import get_session
from na_sso.lifecycle import (
    DesiredAction,
    LifecycleCommand,
    OperationStatus,
    SyncStateValue,
    sync_state_is_terminal,
)
from na_sso.models import LifecycleOperation, ManagedUser, SyncState, as_utc
from na_sso.operations import (
    OperationConflict,
    finish_operation,
    finish_target_attempt,
    request_operation,
    resume_operation,
    start_operation,
    start_target_attempt,
)
from na_sso.notifications import enqueue_notification
from na_sso.security import decrypt_secret
from na_sso.security import encrypt_secret
from na_sso.target_credentials import retry_due_target_probes

_scan_lock = asyncio.Lock()
_user_locks: dict[int, asyncio.Lock] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def credential_handoff(user_id: int, password: str) -> None:
    """Stage a verified credential only while assigned targets consume it."""
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.is_root:
            return
        if user.password_decision_kind in {"initial", "reset"}:
            return
        waiting = [state for state in user.sync_states if state.assigned and not state.retired
                   and state.state in {"awaiting_credentials", "expired_disabled", "chpw"}]
        if not waiting:
            return
        user.pending_secret = encrypt_secret(password)
        for state in waiting:
            state.state = "pending"
            state.detail = "credential supplied after verified authentication"
        db.commit()
    await sync_user(user_id, actor="verified-login")


def _command_for(operation: DesiredAction, actor: str) -> LifecycleCommand:
    if operation is DesiredAction.DELETE:
        return LifecycleCommand.DELETE
    if actor == "verified-login":
        return LifecycleCommand.CREDENTIAL_HANDOFF
    if actor == "password-expiry":
        return LifecycleCommand.EXPIRE
    return LifecycleCommand.UPDATE


async def sync_user(
    user_id: int,
    action: str | None = None,
    target: str | None = None,
    actor: str = "system",
    operation_id: str | None = None,
) -> str | None:
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        return await _sync_user(user_id, action, target, actor, operation_id)


async def _sync_user(
    user_id: int,
    action: str | None,
    target: str | None,
    actor: str,
    operation_id: str | None,
) -> str | None:
    available = {c.target_id: c for c in get_connectors()}
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user:
            return None
        if user.is_root:
            return None
        desired_action = DesiredAction(action or user.desired_action)
        password = decrypt_secret(user.pending_secret) if user.pending_secret else None
        states = {state.target: state for state in user.sync_states}
        from na_sso.assignments import resolve_assignment_intents
        assignment_intents = resolve_assignment_intents(db, user, available)
        if not states and not get_settings().config_file:
            for connector in available.values():
                state = SyncState(user=user, target=connector.target_id,
                                  target_type=connector.target_type, assigned=True)
                db.add(state)
                states[state.target] = state
            db.flush()

        operation = db.get(LifecycleOperation, operation_id) if operation_id else None
        if operation is not None and operation.user_id != user.id:
            return None
        if operation is None and target is not None:
            retry_state = states.get(target)
            if retry_state and retry_state.operation_id:
                operation = db.get(LifecycleOperation, retry_state.operation_id)
                if operation and OperationStatus(operation.status) in {
                    OperationStatus.FAILED,
                    OperationStatus.PARTIALLY_FAILED,
                }:
                    resume_operation(user, operation, requested_target=target)
                else:
                    operation = None
        if operation is None:
            command = _command_for(desired_action, actor)
            try:
                operation = request_operation(
                    db, user, command, actor, requested_target=target
                )
            except OperationConflict:
                return None

        if desired_action is DesiredAction.DELETE:
            scope_states = [
                state for state in user.sync_states if target is None or state.target == target
            ]
            connector_states = [
                state
                for state in scope_states
                if not state.retired and state.target in available
            ]
        else:
            scope_states = [
                state
                for state in user.sync_states
                # An unassigned target stays in scope while its offboarding
                # disable is pending or failed, so retries can reach it.
                if (state.assigned or state.state in {
                    SyncStateValue.PENDING_DISABLE.value, SyncStateValue.FAILED.value,
                })
                and not state.retired
                and state.target in available
                and state.state != SyncStateValue.CHPW.value
                and (target is None or state.target == target)
            ]
            connector_states = list(scope_states)

        for state in scope_states:
            state.operation_id = operation.id
        if OperationStatus(operation.status) is OperationStatus.QUEUED:
            start_operation(operation, len(scope_states))
        else:
            resume_operation(user, operation, requested_target=target)
            if operation.total_targets == 0:
                operation.total_targets = len(scope_states)
        db.commit()

        connectors = [available[state.target] for state in connector_states]
        for connector in connectors:
            state = states[connector.target_id]
            if (
                desired_action is not DesiredAction.DELETE
                and state.state == SyncStateValue.AWAITING_CREDENTIALS.value
                and password is None
            ):
                continue
            chpw_disable = state.state == SyncStateValue.PENDING_CHPW_DISABLE.value
            attempt = start_target_attempt(
                db,
                operation,
                target=connector.target_id,
                target_type=connector.target_type,
            )
            state.state, state.detail, state.next_retry_at = SyncStateValue.PENDING.value, "", None
            db.commit()
            if desired_action is DesiredAction.DELETE:
                result = await connector.delete_user(user)
            elif user.status == "disabled" or not state.assigned or chpw_disable or user.password_decision_kind == "expired":
                result = await connector.disable_user_for_assignment(
                    user,
                    assignment_intents.get(connector.target_id, connector.default_memberships),
                )
            else:
                result = await connector.ensure_user_for_assignment(
                    user,
                    password,
                    assignment_intents.get(connector.target_id, connector.default_memberships),
                )
            state.state = SyncStateValue.OK.value if result.ok else SyncStateValue.FAILED.value
            state.detail = result.detail
            if result.ok:
                state.attempt_count, state.next_retry_at = 0, None
                if desired_action is DesiredAction.DELETE:
                    state.state = SyncStateValue.OK.value
                elif not state.assigned:
                    state.state = SyncStateValue.UNASSIGNED.value
                elif user.password_decision_kind in {"initial", "reset"}:
                    state.state = SyncStateValue.CHPW.value
                    state.detail = "password change required before propagation"
                elif user.password_decision_kind == "expired":
                    state.state = SyncStateValue.EXPIRED_DISABLED.value
            else:
                state.attempt_count += 1
                settings = get_settings()
                if result.error_kind is ConnectorErrorKind.VALIDATION:
                    # The connector declared the request unsatisfiable; retrying
                    # with the same inputs cannot succeed, so the failure is
                    # persistent immediately and no retry is scheduled.
                    state.state = SyncStateValue.UNSUPPORTED.value
                    persistent = True
                else:
                    delay = min(settings.retry_base_seconds * (2 ** (state.attempt_count - 1)), settings.retry_max_seconds)
                    state.next_retry_at = _now() + timedelta(seconds=delay)
                    persistent = state.attempt_count == settings.file.notification_policy.persistent_failure_attempts
                if persistent:
                    enqueue_notification(
                        db, "sync.persistent_failure", actor=actor,
                        subject=user.username,
                        dedupe_key=f"{operation.id}:{connector.target_id}",
                        operation_id=operation.id,
                        target_id=connector.target_id,
                        outcome="failed",
                    )
            finish_target_attempt(
                attempt,
                succeeded=result.ok,
                result_state=state.state,
                detail=result.detail,
            )
            record_audit(
                db,
                actor,
                f"sync.{desired_action.value}",
                user.username,
                f"{connector.target_id}: {state.state} — {result.detail}",
                operation.id,
            )
            db.commit()

        operation_states = [
            state for state in user.sync_states if state.operation_id == operation.id
        ]
        failures = [
            state for state in operation_states
            if state.state in {SyncStateValue.FAILED.value, SyncStateValue.UNSUPPORTED.value}
        ]
        terminal = [
            state
            for state in operation_states
            if sync_state_is_terminal(
                state.state,
                operation=desired_action,
                assigned=state.assigned,
                retired=state.retired,
            )
        ]
        operation.completed_targets = len(terminal)
        operation.failed_targets = len(failures)
        if failures:
            status = (
                OperationStatus.PARTIALLY_FAILED
                if len(terminal) > len(failures)
                else OperationStatus.FAILED
            )
            finish_operation(
                user,
                operation,
                status,
                completed_targets=len(terminal),
                failed_targets=len(failures),
                detail=f"{len(failures)} target operation(s) failed",
            )
        elif len(terminal) == len(operation_states):
            if desired_action is DesiredAction.DELETE:
                user.deleted_at = _now()
            else:
                assigned = [
                    state
                    for state in user.sync_states
                    if state.assigned and not state.retired and state.target in available
                ]
                if assigned and all(state.state == SyncStateValue.OK.value for state in assigned):
                    user.pending_secret = None
            finish_operation(
                user,
                operation,
                OperationStatus.SUCCEEDED,
                completed_targets=len(terminal),
                failed_targets=0,
            )
        if OperationStatus(operation.status).terminal:
            enqueue_notification(
                db, "lifecycle.completed", actor=operation.actor,
                subject=operation.subject,
                dedupe_key=f"{operation.id}:{operation.status}",
                operation_id=operation.id,
                target_id=operation.requested_target,
                outcome=operation.status,
            )
        db.commit()
        return operation.id


async def retry_due() -> int:
    async with _scan_lock:
        now = _now()
        with get_session() as db:
            due = [
                (s.user_id, s.target, s.operation_id)
                # Unassigned states are included: a failed offboarding disable
                # retries until the target account is actually disabled.
                for s in db.query(SyncState).filter(
                    SyncState.retired.is_(False),
                    SyncState.state == SyncStateValue.FAILED.value,
                    SyncState.next_retry_at <= now,
                ).all()
            ]
        for user_id, target, operation_id in due:
            await sync_user(
                user_id,
                target=target,
                actor="auto-retry",
                operation_id=operation_id,
            )
        return len(due)


async def expire_due() -> int:
    """Apply the configured password-age acknowledgement gate idempotently."""
    days = get_settings().file.password_policy.expires_after_days
    if days is None:
        return 0
    now = _now()
    with get_session() as db:
        users = db.query(ManagedUser).filter(
            ManagedUser.role != "root", ManagedUser.status == "active",
            ManagedUser.password_decision_required.is_(False),
            ManagedUser.password_changed_at.is_not(None),
        ).all()
        ids = []
        for user in users:
            expires_at = as_utc(user.password_expires_at)
            if expires_at is None or expires_at > now:
                continue
            user.password_decision_required = True
            user.password_decision_kind = "expired"
            for state in user.sync_states:
                if state.assigned and not state.retired:
                    state.state = "pending_expiry_disable"
                    state.next_retry_at = None
            record_audit(db, "system", "password.expired", user.username,
                         "targets disabled pending password acknowledgement")
            enqueue_notification(
                db, "password.expired", actor="system", subject=user.username,
                dedupe_key=f"{user.id}:{expires_at.isoformat()}",
                outcome="expired",
            )
            ids.append(user.id)
        db.commit()
    for user_id in ids:
        await sync_user(user_id, actor="password-expiry")
    return len(ids)


async def retry_worker() -> None:
    while True:
        await asyncio.sleep(get_settings().retry_scan_seconds)
        from na_sso.ssh_keys import expire_due_ssh_keys
        await retry_due_target_probes()
        await expire_due_ssh_keys()
        await expire_due()
        await retry_due()
