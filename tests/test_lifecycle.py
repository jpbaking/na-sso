from datetime import datetime, timezone

from na_sso.lifecycle import (
    LifecycleCommand,
    OperationStatus,
    SyncStateValue,
    TransitionMode,
    decide_transition,
    normalise_sync_state,
    present_sync_state,
    sync_state_is_terminal,
)
from na_sso.models import ManagedUser
from na_sso.operations import (
    create_operation,
    finish_operation,
    finish_target_attempt,
    get_active_operation,
    start_operation,
    start_target_attempt,
)


def test_delete_overrides_other_work_and_reuses_active_delete():
    supersede = decide_transition(
        LifecycleCommand.DELETE,
        active_command=LifecycleCommand.UPDATE,
        active_status=OperationStatus.RUNNING,
    )
    assert supersede.mode is TransitionMode.SUPERSEDE

    reuse = decide_transition(
        LifecycleCommand.DELETE,
        active_command=LifecycleCommand.DELETE,
        active_status=OperationStatus.RUNNING,
    )
    assert reuse.mode is TransitionMode.REUSE


def test_restore_and_purge_require_completed_delete():
    assert not decide_transition(LifecycleCommand.RESTORE).allowed
    assert not decide_transition(LifecycleCommand.PURGE).allowed
    assert decide_transition(LifecycleCommand.RESTORE, delete_complete=True).allowed
    assert decide_transition(LifecycleCommand.PURGE, delete_complete=True).allowed


def test_retry_belongs_to_failed_operation():
    assert not decide_transition(
        LifecycleCommand.RETRY,
        active_command=LifecycleCommand.UPDATE,
        active_status=OperationStatus.SUCCEEDED,
    ).allowed
    decision = decide_transition(
        LifecycleCommand.RETRY,
        active_command=LifecycleCommand.UPDATE,
        active_status=OperationStatus.PARTIALLY_FAILED,
    )
    assert decision.mode is TransitionMode.REUSE


def test_state_normalisation_preserves_absent_unassigned_and_retired_meanings():
    assert normalise_sync_state(None) is SyncStateValue.NOT_ASSIGNED
    assert normalise_sync_state("ok", assigned=False) is SyncStateValue.UNASSIGNED
    assert normalise_sync_state("failed", retired=True) is SyncStateValue.RETIRED
    assert normalise_sync_state("pending_disable", assigned=False) is SyncStateValue.PENDING_DISABLE


def test_unassigned_failures_are_never_masked_as_completed_disable():
    assert normalise_sync_state("failed", assigned=False) is SyncStateValue.FAILED
    assert normalise_sync_state("unsupported", assigned=False) is SyncStateValue.UNSUPPORTED


def test_unsupported_presents_as_terminal_without_retry():
    presentation = present_sync_state("unsupported")
    assert presentation.label == "Unsupported on target"
    assert presentation.terminal and not presentation.retryable


def test_presentations_cover_retry_and_delete_context():
    deleted = present_sync_state("ok", desired_action="delete")
    assert deleted.label == "Deleted" and deleted.terminal

    retrying = present_sync_state(
        "failed", next_retry_at=datetime.now(timezone.utc)
    )
    assert retrying.label == "Retrying" and retrying.retryable and not retrying.terminal

    assert present_sync_state(None).label == "Not assigned"
    assert present_sync_state("chpw").label == "Password change required"


def test_terminal_checks_are_operation_aware():
    assert sync_state_is_terminal("chpw")
    assert not sync_state_is_terminal("chpw", operation="delete")
    assert sync_state_is_terminal("ok", operation="delete")


def test_operation_and_target_attempt_persistence(client):
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(username="correlated", display_name="Correlated")
        db.add(user)
        db.flush()

        operation = create_operation(
            db, user, LifecycleCommand.UPDATE, "admin", requested_target="target-a"
        )
        start_operation(operation, 1)
        attempt = start_target_attempt(
            db, operation, target="target-a", target_type="opnsense"
        )
        finish_target_attempt(
            attempt, succeeded=True, result_state="ok", detail="saved"
        )
        finish_operation(
            user,
            operation,
            OperationStatus.SUCCEEDED,
            completed_targets=1,
            failed_targets=0,
        )
        db.commit()

        assert operation.id and attempt.operation_id == operation.id
        assert operation.started_at and operation.completed_at
        assert operation.completed_targets == 1 and operation.failed_targets == 0
        assert user.active_operation_id is None
        assert get_active_operation(db, user) is None
