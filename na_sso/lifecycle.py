"""Typed lifecycle and synchronization contracts.

This module is the single source of truth for persisted lifecycle values,
operation conflict rules, terminal-state checks, and user-facing sync-state
presentation. Routes and workers may add context, but must not invent state
names or duplicate the mappings below.

Conflict rules:

* Delete overrides every unfinished non-delete operation.
* A duplicate delete reuses the active delete operation.
* Restore starts only after delete reaches its successful terminal state.
* Purge starts only after remote deletion is complete.
* Retry belongs to the failed operation it is repairing.
* Other mutations are rejected while a lifecycle operation is running.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DesiredAction(StrEnum):
    ENSURE = "ensure"
    DELETE = "delete"
    LOCAL_ONLY = "local_only"


class SyncStateValue(StrEnum):
    NOT_ASSIGNED = "not_assigned"  # presentation-only; no persisted row
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
    CHPW = "chpw"
    AWAITING_CREDENTIALS = "awaiting_credentials"
    PENDING_DISABLE = "pending_disable"
    PENDING_CHPW_DISABLE = "pending_chpw_disable"
    PENDING_EXPIRY_DISABLE = "pending_expiry_disable"
    UNASSIGNED = "unassigned"
    RETIRED = "retired"
    EXPIRED_DISABLED = "expired_disabled"


class LifecycleCommand(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    ENABLE = "enable"
    DISABLE = "disable"
    DELETE = "delete"
    RESTORE = "restore"
    PURGE = "purge"
    RETRY = "retry"
    EXPIRE = "expire"
    CREDENTIAL_HANDOFF = "credential_handoff"
    PASSWORD_CHANGE = "password_change"
    RECONCILE = "reconcile"
    BULK = "bulk"
    TARGET_PROBE = "target_probe"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_OPERATION_STATUSES


TERMINAL_OPERATION_STATUSES = frozenset({
    OperationStatus.SUCCEEDED,
    OperationStatus.PARTIALLY_FAILED,
    OperationStatus.FAILED,
    OperationStatus.CANCELLED,
    OperationStatus.BLOCKED,
})


class TransitionMode(StrEnum):
    START = "start"
    REUSE = "reuse"
    SUPERSEDE = "supersede"
    REJECT = "reject"


@dataclass(frozen=True)
class TransitionDecision:
    mode: TransitionMode
    reason: str

    @property
    def allowed(self) -> bool:
        return self.mode is not TransitionMode.REJECT


def decide_transition(
    requested: LifecycleCommand,
    *,
    active_command: LifecycleCommand | None = None,
    active_status: OperationStatus | None = None,
    delete_complete: bool = False,
) -> TransitionDecision:
    """Return the durable conflict decision for a requested lifecycle command."""

    active = bool(active_command and active_status and not active_status.terminal)

    if requested is LifecycleCommand.PURGE:
        if delete_complete:
            return TransitionDecision(TransitionMode.START, "remote deletion is complete")
        return TransitionDecision(TransitionMode.REJECT, "purge requires completed remote deletion")

    if requested is LifecycleCommand.RESTORE:
        if active:
            return TransitionDecision(TransitionMode.REJECT, "restore waits for the active operation")
        if delete_complete:
            return TransitionDecision(TransitionMode.START, "completed deletion can be restored")
        return TransitionDecision(TransitionMode.REJECT, "restore requires completed remote deletion")

    if requested is LifecycleCommand.DELETE:
        if active_command is LifecycleCommand.DELETE and active:
            return TransitionDecision(TransitionMode.REUSE, "deletion is already running")
        if active:
            return TransitionDecision(TransitionMode.SUPERSEDE, "deletion overrides the active operation")
        return TransitionDecision(TransitionMode.START, "deletion may start from every lifecycle state")

    if requested is LifecycleCommand.RETRY:
        if not active_command or not active_status:
            return TransitionDecision(TransitionMode.REJECT, "retry requires an existing operation")
        if active_status not in {OperationStatus.FAILED, OperationStatus.PARTIALLY_FAILED}:
            return TransitionDecision(TransitionMode.REJECT, "retry requires a failed operation")
        return TransitionDecision(TransitionMode.REUSE, "retry repairs the existing operation")

    if active:
        return TransitionDecision(TransitionMode.REJECT, "another lifecycle operation is running")
    return TransitionDecision(TransitionMode.START, "no conflicting lifecycle operation")


@dataclass(frozen=True)
class SyncPresentation:
    label: str
    badge_class: str
    description: str
    retryable: bool = False
    terminal: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "label": self.label,
            "badge_class": self.badge_class,
            "description": self.description,
            "retryable": self.retryable,
            "terminal": self.terminal,
        }


_PRESENTATIONS: dict[SyncStateValue, SyncPresentation] = {
    SyncStateValue.NOT_ASSIGNED: SyncPresentation(
        "Not assigned", "badge-planned", "This target is not assigned to the account.", terminal=True
    ),
    SyncStateValue.PENDING: SyncPresentation(
        "Pending", "badge-planned", "The target operation is waiting to run."
    ),
    SyncStateValue.OK: SyncPresentation(
        "OK", "badge-shipped", "The target matches the requested account state.", terminal=True
    ),
    SyncStateValue.FAILED: SyncPresentation(
        "Failed", "badge-danger", "The latest target operation failed.", retryable=True, terminal=True
    ),
    SyncStateValue.CHPW: SyncPresentation(
        "Password change required",
        "badge-planned",
        "The account stays uncreated or disabled until the user replaces the temporary password.",
        terminal=True,
    ),
    SyncStateValue.AWAITING_CREDENTIALS: SyncPresentation(
        "Waiting for verified password",
        "badge-planned",
        "Sign-in or a password action must supply a verified password before this target can continue.",
        terminal=True,
    ),
    SyncStateValue.PENDING_DISABLE: SyncPresentation(
        "Disabling", "badge-planned", "The unassigned target account is being disabled."
    ),
    SyncStateValue.PENDING_CHPW_DISABLE: SyncPresentation(
        "Disabling for password change",
        "badge-planned",
        "The target account is being disabled until the user replaces the temporary password.",
    ),
    SyncStateValue.PENDING_EXPIRY_DISABLE: SyncPresentation(
        "Disabling expired password",
        "badge-planned",
        "The target account is being disabled until the password-expiry decision is complete.",
    ),
    SyncStateValue.UNASSIGNED: SyncPresentation(
        "Unassigned; disabled",
        "badge-planned",
        "The target is no longer assigned and its account is disabled.",
        terminal=True,
    ),
    SyncStateValue.RETIRED: SyncPresentation(
        "Retired target", "badge-planned", "The configured target is no longer available.", terminal=True
    ),
    SyncStateValue.EXPIRED_DISABLED: SyncPresentation(
        "Password expired; disabled",
        "badge-planned",
        "The target account is disabled until the user completes the password-expiry decision.",
        terminal=True,
    ),
}


def normalise_sync_state(
    state: str | SyncStateValue | None,
    *,
    assigned: bool = True,
    retired: bool = False,
) -> SyncStateValue:
    if retired:
        return SyncStateValue.RETIRED
    if state is None:
        return SyncStateValue.NOT_ASSIGNED
    try:
        value = SyncStateValue(state)
    except ValueError:
        return SyncStateValue.PENDING
    if not assigned and value not in {
        SyncStateValue.NOT_ASSIGNED,
        SyncStateValue.PENDING_DISABLE,
        SyncStateValue.RETIRED,
    }:
        return SyncStateValue.UNASSIGNED
    return value


def present_sync_state(
    state: str | SyncStateValue | None,
    *,
    assigned: bool = True,
    retired: bool = False,
    desired_action: str | DesiredAction = DesiredAction.ENSURE,
    next_retry_at: datetime | None = None,
) -> SyncPresentation:
    value = normalise_sync_state(state, assigned=assigned, retired=retired)
    presentation = _PRESENTATIONS[value]
    if value is SyncStateValue.OK and DesiredAction(desired_action) is DesiredAction.DELETE:
        return SyncPresentation(
            "Deleted", "badge-shipped", "The account was removed from this target.", terminal=True
        )
    if value is SyncStateValue.FAILED and next_retry_at is not None:
        return SyncPresentation(
            "Retrying",
            presentation.badge_class,
            "The latest target operation failed and automatic retry is scheduled.",
            retryable=True,
            terminal=False,
        )
    return presentation


def sync_state_payload(
    state: str | SyncStateValue | None,
    *,
    assigned: bool = True,
    retired: bool = False,
    desired_action: str | DesiredAction = DesiredAction.ENSURE,
    detail: str = "",
    attempt_count: int = 0,
    next_retry_at: datetime | None = None,
    operation_id: str | None = None,
) -> dict:
    value = normalise_sync_state(state, assigned=assigned, retired=retired)
    presentation = present_sync_state(
        value,
        assigned=assigned,
        retired=retired,
        desired_action=desired_action,
        next_retry_at=next_retry_at,
    )
    return {
        "state": value.value,
        "assigned": assigned,
        "retired": retired,
        "detail": detail,
        "attempt_count": attempt_count,
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        "operation_id": operation_id,
        "presentation": presentation.as_dict(),
    }


def sync_state_is_terminal(
    state: str | SyncStateValue | None,
    *,
    operation: DesiredAction = DesiredAction.ENSURE,
    assigned: bool = True,
    retired: bool = False,
) -> bool:
    value = normalise_sync_state(state, assigned=assigned, retired=retired)
    if DesiredAction(operation) is DesiredAction.DELETE:
        return value in {SyncStateValue.OK, SyncStateValue.RETIRED, SyncStateValue.NOT_ASSIGNED}
    return _PRESENTATIONS[value].terminal
