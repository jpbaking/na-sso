"""Read-only desired-versus-actual identity comparison contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from na_sso.models import ManagedUser
from na_sso.security import ssh_public_key_fingerprint


class DriftState(StrEnum):
    MATCH = "match"
    DRIFT = "drift"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class ReconciliationStatus(StrEnum):
    IN_SYNC = "in_sync"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"


class ReconciliationField(StrEnum):
    IDENTITY = "identity"
    DISPLAY_NAME = "display_name"
    EMAIL = "email"
    STATUS = "status"
    MEMBERSHIPS = "memberships"
    PUBLIC_KEY = "public_key"


@dataclass(frozen=True)
class InspectionCapabilities:
    display_name: bool = False
    email: bool = False
    status: bool = False
    memberships: bool = False
    public_key: bool = False
    memberships_exact: bool = True


@dataclass(frozen=True)
class RemoteIdentitySnapshot:
    """Sanitised observations only; passwords and key material never belong here."""

    present: bool | None
    username: str | None = None
    display_name: str | None = None
    email: str | None = None
    status: str | None = None
    memberships: frozenset[str] | None = None
    public_key_fingerprints: frozenset[str] | None = None


@dataclass(frozen=True)
class FieldComparison:
    field: ReconciliationField
    state: DriftState
    desired: str | None = None
    actual: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class ReconciliationReport:
    target_id: str
    target_name: str
    subject: str
    fields: tuple[FieldComparison, ...]
    detail: str = ""

    @property
    def status(self) -> ReconciliationStatus:
        states = {item.state for item in self.fields}
        if DriftState.DRIFT in states:
            return ReconciliationStatus.DRIFTED
        if DriftState.UNKNOWN in states:
            return ReconciliationStatus.UNKNOWN
        return ReconciliationStatus.IN_SYNC

    @property
    def has_unknowns(self) -> bool:
        return any(item.state == DriftState.UNKNOWN for item in self.fields)

    def field(self, name: ReconciliationField | str) -> FieldComparison:
        wanted = ReconciliationField(name)
        return next(item for item in self.fields if item.field == wanted)


def mark_unsupported_operation(
    report: ReconciliationReport, detail: str
) -> ReconciliationReport:
    """Replace repairable drift with an explicit pre-execution limitation."""
    limitation = f"Repair unsupported: {detail}"
    fields = tuple(
        FieldComparison(
            item.field,
            DriftState.UNSUPPORTED,
            item.desired,
            item.actual,
            limitation,
        )
        if item.state == DriftState.DRIFT else item
        for item in report.fields
    )
    if fields == report.fields:
        return report
    report_detail = " ".join(part for part in (report.detail, limitation) if part)
    return ReconciliationReport(
        report.target_id,
        report.target_name,
        report.subject,
        fields,
        report_detail,
    )


def _display(value: Any) -> str:
    if isinstance(value, (set, frozenset, tuple, list)):
        return ", ".join(sorted(str(item) for item in value)) or "none"
    if isinstance(value, bool):
        return "present" if value else "absent"
    if value is None or value == "":
        return "none"
    return str(value)


def _unsupported(field: ReconciliationField, detail: str = "Target does not expose this field.") -> FieldComparison:
    return FieldComparison(field, DriftState.UNSUPPORTED, detail=detail)


def _unknown(field: ReconciliationField, desired: Any, detail: str) -> FieldComparison:
    return FieldComparison(field, DriftState.UNKNOWN, desired=_display(desired), detail=detail)


def _compare(field: ReconciliationField, desired: Any, actual: Any) -> FieldComparison:
    state = DriftState.MATCH if desired == actual else DriftState.DRIFT
    return FieldComparison(field, state, _display(desired), _display(actual))


def compare_snapshot(
    *,
    target_id: str,
    target_name: str,
    user: ManagedUser,
    capabilities: InspectionCapabilities,
    snapshot: RemoteIdentitySnapshot,
    required_memberships: frozenset[str] = frozenset(),
) -> ReconciliationReport:
    """Compare one sanitised remote observation without causing remote mutation."""

    desired_present = user.desired_action != "delete" and user.deleted_at is None
    fields: list[FieldComparison] = []
    if snapshot.present is None:
        fields.append(_unknown(ReconciliationField.IDENTITY, user.username if desired_present else "absent", "Remote identity could not be read."))
    elif desired_present and not snapshot.present:
        fields.append(_compare(ReconciliationField.IDENTITY, user.username, "absent"))
    elif desired_present and snapshot.username is None:
        fields.append(_unknown(ReconciliationField.IDENTITY, user.username, "Remote username could not be observed."))
    elif desired_present:
        fields.append(_compare(ReconciliationField.IDENTITY, user.username, snapshot.username))
    else:
        fields.append(_compare(ReconciliationField.IDENTITY, "absent", "present" if snapshot.present else "absent"))

    supported = {
        ReconciliationField.DISPLAY_NAME: capabilities.display_name,
        ReconciliationField.EMAIL: capabilities.email,
        ReconciliationField.STATUS: capabilities.status,
        ReconciliationField.MEMBERSHIPS: capabilities.memberships,
        ReconciliationField.PUBLIC_KEY: capabilities.public_key,
    }
    if not desired_present:
        for field, is_supported in supported.items():
            fields.append(_unsupported(field, "Not applicable when the desired identity is absent.") if is_supported else _unsupported(field))
        return ReconciliationReport(target_id, target_name, user.username, tuple(fields))

    desired_values: dict[ReconciliationField, Any] = {
        ReconciliationField.DISPLAY_NAME: user.display_name or user.username,
        ReconciliationField.EMAIL: user.email,
        ReconciliationField.STATUS: "disabled" if user.status == "disabled" else "active",
    }
    actual_values: dict[ReconciliationField, Any] = {
        ReconciliationField.DISPLAY_NAME: snapshot.display_name,
        ReconciliationField.EMAIL: snapshot.email,
        ReconciliationField.STATUS: snapshot.status,
    }
    for field in (ReconciliationField.DISPLAY_NAME, ReconciliationField.EMAIL, ReconciliationField.STATUS):
        if not supported[field]:
            fields.append(_unsupported(field))
        elif snapshot.present is not True or actual_values[field] is None:
            fields.append(_unknown(field, desired_values[field], "Remote field could not be observed."))
        else:
            fields.append(_compare(field, desired_values[field], actual_values[field]))

    if not capabilities.memberships:
        fields.append(_unsupported(ReconciliationField.MEMBERSHIPS))
    elif snapshot.present is not True or snapshot.memberships is None:
        fields.append(_unknown(ReconciliationField.MEMBERSHIPS, required_memberships, "Remote memberships could not be observed."))
    else:
        memberships_match = (
            snapshot.memberships == required_memberships
            if capabilities.memberships_exact
            else required_memberships.issubset(snapshot.memberships)
        )
        comparison = _compare(ReconciliationField.MEMBERSHIPS, required_memberships, snapshot.memberships)
        fields.append(FieldComparison(
            comparison.field,
            DriftState.MATCH if memberships_match else DriftState.DRIFT,
            comparison.desired,
            comparison.actual,
            "Additional memberships are retained." if memberships_match and not capabilities.memberships_exact else "",
        ))

    desired_fingerprints = frozenset(
        fingerprint for fingerprint in (
            ssh_public_key_fingerprint(key) for key in user.active_ssh_public_keys
        ) if fingerprint
    )
    if not capabilities.public_key:
        fields.append(_unsupported(ReconciliationField.PUBLIC_KEY))
    elif not desired_fingerprints:
        if snapshot.present is not True or snapshot.public_key_fingerprints is None:
            fields.append(_unknown(ReconciliationField.PUBLIC_KEY, desired_fingerprints, "Remote key state could not be observed."))
        else:
            fields.append(_compare(ReconciliationField.PUBLIC_KEY, desired_fingerprints, snapshot.public_key_fingerprints))
    elif snapshot.present is not True or snapshot.public_key_fingerprints is None:
        fields.append(_unknown(ReconciliationField.PUBLIC_KEY, desired_fingerprints, "Remote key state could not be observed."))
    else:
        fields.append(_compare(
            ReconciliationField.PUBLIC_KEY,
            desired_fingerprints,
            snapshot.public_key_fingerprints,
        ))

    return ReconciliationReport(target_id, target_name, user.username, tuple(fields))


def unavailable_report(
    *,
    target_id: str,
    target_name: str,
    user: ManagedUser,
    capabilities: InspectionCapabilities,
    detail: str,
    required_memberships: frozenset[str] = frozenset(),
) -> ReconciliationReport:
    report = compare_snapshot(
        target_id=target_id,
        target_name=target_name,
        user=user,
        capabilities=capabilities,
        snapshot=RemoteIdentitySnapshot(present=None),
        required_memberships=required_memberships,
    )
    return ReconciliationReport(target_id, target_name, user.username, report.fields, detail)
