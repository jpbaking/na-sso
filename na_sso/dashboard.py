"""Read-only aggregations for the console dashboard.

Two dataset groups: `eager_datasets` backs the tiles and charts rendered on
page load; `insights_datasets` backs the collapsed "More insights" section and
is only computed when its JSON endpoint is hit. All shapes match the lwCharts
inputs (labels/series for bar+line, slices for donut, values for sparkline).
"""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from na_sso.lifecycle import OperationStatus, SyncStateValue
from na_sso.models import (
    AccessReview,
    AccessReviewItem,
    AuditEvent,
    LifecycleOperation,
    ManagedUser,
    ReconciliationFinding,
    ReconciliationRun,
    ServiceAccountCredential,
    SyncState,
    TargetCredential,
    UnmanagedAccountFinding,
    UserSshKey,
    WebhookDelivery,
    as_utc,
    utcnow,
)

_ERROR_SYNC_STATES = {SyncStateValue.FAILED.value, SyncStateValue.UNSUPPORTED.value}

# Reconciliation finding fields are a small closed set; keep donut labels human.
_FIELD_LABELS = {
    "presence": "Presence",
    "status": "Status",
    "password": "Password",
    "memberships": "Memberships",
    "ssh_keys": "SSH keys",
}


def _day_labels(days: int, today: date | None = None) -> list[date]:
    end = today or utcnow().date()
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _bucket_by_day(moments: list[datetime | None], days: int) -> tuple[list[str], list[int]]:
    labels = _day_labels(days)
    counts = {label: 0 for label in labels}
    for moment in moments:
        moment = as_utc(moment)
        if moment is None:
            continue
        day = moment.date()
        if day in counts:
            counts[day] += 1
    return [label.strftime("%b %d") for label in labels], [counts[label] for label in labels]


def _visible_users(db: Session) -> list[ManagedUser]:
    return db.query(ManagedUser).filter(ManagedUser.role != "root").all()


def _users_tile(db: Session) -> dict:
    users = _visible_users(db)
    live = [user for user in users if user.deleted_at is None]
    labels = _day_labels(30)
    spark = []
    for label in labels:
        cutoff = datetime(label.year, label.month, label.day, 23, 59, 59, tzinfo=timezone.utc)
        count = 0
        for user in users:
            created = as_utc(user.created_at)
            deleted = as_utc(user.deleted_at)
            if created is not None and created <= cutoff and (deleted is None or deleted > cutoff):
                count += 1
        spark.append(count)
    return {
        "total": len(live),
        "active": sum(1 for user in live if user.status == "active"),
        "disabled": sum(1 for user in live if user.status == "disabled"),
        "spark": spark,
    }


def _targets_tile(db: Session, target_ids: list[str]) -> dict:
    credentials = {cred.target_id: cred for cred in db.query(TargetCredential).all()}
    healthy = 0
    for target_id in target_ids:
        cred = credentials.get(target_id)
        if cred is not None and cred.verified and cred.last_probe_ok is not False:
            healthy += 1
    return {"healthy": healthy, "total": len(target_ids)}


def _latest_completed_run(db: Session) -> ReconciliationRun | None:
    return (
        db.query(ReconciliationRun)
        .filter(ReconciliationRun.completed_at.isnot(None))
        .order_by(ReconciliationRun.completed_at.desc())
        .first()
    )


def _findings_tile(db: Session) -> dict:
    run = _latest_completed_run(db)
    drift = 0
    if run is not None:
        drift = (
            db.query(ReconciliationFinding)
            .filter(
                ReconciliationFinding.run_id == run.id,
                ReconciliationFinding.state == "drift",
                ReconciliationFinding.repair_status.notin_(("succeeded", "repaired")),
            )
            .count()
        )
    unmanaged = (
        db.query(UnmanagedAccountFinding)
        .filter(
            UnmanagedAccountFinding.decision == "pending",
            UnmanagedAccountFinding.present.is_(True),
        )
        .count()
    )
    return {"open": drift + unmanaged, "drift": drift, "unmanaged": unmanaged}


def _operations_24h_tile(db: Session) -> dict:
    since = utcnow() - timedelta(hours=24)
    operations = (
        db.query(LifecycleOperation)
        .filter(LifecycleOperation.created_at >= since.replace(tzinfo=None))
        .all()
    )
    operations = [op for op in operations if as_utc(op.created_at) >= since]
    succeeded = sum(1 for op in operations if op.status == OperationStatus.SUCCEEDED.value)
    failed = sum(
        1 for op in operations
        if op.status in (OperationStatus.FAILED.value, OperationStatus.PARTIALLY_FAILED.value)
    )
    return {"total": len(operations), "succeeded": succeeded, "failed": failed}


def _sync_health_chart(db: Session, target_ids: list[str]) -> dict:
    rows = (
        db.query(SyncState)
        .filter(SyncState.assigned.is_(True), SyncState.retired.is_(False))
        .all()
    )
    buckets: dict[str, dict[str, int]] = {
        target: {"ok": 0, "pending": 0, "error": 0} for target in target_ids
    }
    for row in rows:
        bucket = buckets.setdefault(row.target, {"ok": 0, "pending": 0, "error": 0})
        if row.state == SyncStateValue.OK.value:
            bucket["ok"] += 1
        elif row.state in _ERROR_SYNC_STATES:
            bucket["error"] += 1
        else:
            bucket["pending"] += 1
    labels = list(buckets.keys())
    return {
        "labels": labels,
        "series": [
            {"name": "In sync", "values": [buckets[t]["ok"] for t in labels]},
            {"name": "Pending", "values": [buckets[t]["pending"] for t in labels]},
            {"name": "Error", "values": [buckets[t]["error"] for t in labels]},
        ],
    }


def _operations_timeline_chart(db: Session) -> dict:
    since = utcnow() - timedelta(days=14)
    operations = (
        db.query(LifecycleOperation)
        .filter(LifecycleOperation.created_at >= since.replace(tzinfo=None) - timedelta(days=1))
        .all()
    )
    succeeded = [op.created_at for op in operations if op.status == OperationStatus.SUCCEEDED.value]
    failed = [
        op.created_at for op in operations
        if op.status in (OperationStatus.FAILED.value, OperationStatus.PARTIALLY_FAILED.value)
    ]
    labels, succeeded_counts = _bucket_by_day(succeeded, 14)
    _, failed_counts = _bucket_by_day(failed, 14)
    return {
        "labels": labels,
        "series": [
            {"name": "Succeeded", "values": succeeded_counts},
            {"name": "Failed", "values": failed_counts},
        ],
    }


def _expiry_horizon_chart(db: Session) -> dict:
    now = utcnow()
    horizons = [("≤7 days", 7), ("≤30 days", 30), ("≤60 days", 60)]

    password_expiries = []
    for user in _visible_users(db):
        if user.deleted_at is not None or user.status != "active":
            continue
        expiry = user.password_expires_at
        if expiry is not None:
            password_expiries.append(as_utc(expiry))

    ssh_expiries = [
        as_utc(key.expires_at)
        for key in db.query(UserSshKey)
        .filter(UserSshKey.revoked_at.is_(None), UserSshKey.expires_at.isnot(None))
        .all()
    ]
    credential_expiries = [
        as_utc(cred.expires_at)
        for cred in db.query(ServiceAccountCredential)
        .filter(ServiceAccountCredential.revoked_at.is_(None))
        .all()
    ]

    def bucket(expiries: list[datetime | None]) -> list[int]:
        counts = []
        for _, days in horizons:
            limit = now + timedelta(days=days)
            counts.append(sum(1 for moment in expiries if moment is not None and now <= moment <= limit))
        return counts

    return {
        "labels": [label for label, _ in horizons],
        "series": [
            {"name": "Passwords", "values": bucket(password_expiries)},
            {"name": "SSH keys", "values": bucket(ssh_expiries)},
            {"name": "Service credentials", "values": bucket(credential_expiries)},
        ],
    }


def _recon_findings_chart(db: Session) -> dict:
    run = _latest_completed_run(db)
    slices: dict[str, int] = {}
    if run is not None:
        findings = (
            db.query(ReconciliationFinding)
            .filter(
                ReconciliationFinding.run_id == run.id,
                ReconciliationFinding.state == "drift",
            )
            .all()
        )
        for finding in findings:
            label = _FIELD_LABELS.get(finding.field, finding.field)
            slices[label] = slices.get(label, 0) + 1
    return {
        "run_completed_at": as_utc(run.completed_at).isoformat() if run and run.completed_at else None,
        "slices": [{"label": label, "value": value} for label, value in sorted(slices.items())],
    }


def eager_datasets(db: Session, target_ids: list[str]) -> dict:
    """Datasets for the tiles and charts rendered on page load."""
    return {
        "tiles": {
            "users": _users_tile(db),
            "targets": _targets_tile(db, target_ids),
            "findings": _findings_tile(db),
            "operations_24h": _operations_24h_tile(db),
        },
        "sync_health": _sync_health_chart(db, target_ids),
        "operations_timeline": _operations_timeline_chart(db),
        "expiry_horizon": _expiry_horizon_chart(db),
        "recon_findings": _recon_findings_chart(db),
    }


def _lifecycle_chart(db: Session) -> dict:
    users = _visible_users(db)
    counts = {"Active": 0, "Disabled": 0, "Pending delete": 0, "Deleted": 0}
    for user in users:
        if user.deleted_at is not None:
            counts["Deleted"] += 1
        elif user.deletion_requested_at is not None:
            counts["Pending delete"] += 1
        elif user.status == "disabled":
            counts["Disabled"] += 1
        else:
            counts["Active"] += 1
    return {"slices": [{"label": label, "value": value} for label, value in counts.items() if value]}


def _audit_timeline_chart(db: Session) -> dict:
    since = utcnow() - timedelta(days=15)
    events = (
        db.query(AuditEvent.at)
        .filter(AuditEvent.at >= since.replace(tzinfo=None))
        .all()
    )
    labels, counts = _bucket_by_day([row.at for row in events], 14)
    return {"labels": labels, "series": [{"name": "Events", "values": counts}]}


def _webhooks_chart(db: Session) -> dict:
    since = utcnow() - timedelta(days=30)
    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.created_at >= since.replace(tzinfo=None))
        .all()
    )
    counts = {"Delivered": 0, "Failed": 0, "Pending": 0}
    for delivery in deliveries:
        if delivery.status == "delivered":
            counts["Delivered"] += 1
        elif delivery.status == "failed":
            counts["Failed"] += 1
        else:
            counts["Pending"] += 1
    total = sum(counts.values())
    return {
        "total": total,
        "success_rate": round(counts["Delivered"] / total * 100) if total else None,
        "slices": [{"label": label, "value": value} for label, value in counts.items() if value],
    }


def _access_review_chart(db: Session) -> dict:
    review = (
        db.query(AccessReview)
        .filter(AccessReview.status == "open")
        .order_by(AccessReview.due_at.asc())
        .first()
    )
    if review is None:
        return {"open": False}
    items = db.query(AccessReviewItem).filter(AccessReviewItem.review_id == review.id).all()
    decided = sum(1 for item in items if item.decision != "pending")
    return {
        "open": True,
        "name": review.name,
        "due_at": as_utc(review.due_at).isoformat() if review.due_at else None,
        "decided": decided,
        "pending": len(items) - decided,
        "total": len(items),
    }


def insights_datasets(db: Session) -> dict:
    """Datasets for the collapsed, lazily-loaded "More insights" section."""
    return {
        "lifecycle": _lifecycle_chart(db),
        "audit_timeline": _audit_timeline_chart(db),
        "webhooks": _webhooks_chart(db),
        "access_review": _access_review_chart(db),
    }


router = APIRouter()


def _console_guard(request: Request) -> dict | Response:
    """Admit any console role (user/target operator, auditor, root); the
    dashboard is read-only so any console permission suffices. Delegates to
    permission_guard so the MFA step-up flow is identical to other pages."""
    from na_sso.auth import current_user, permission_guard
    from na_sso.permissions import MANAGE_TARGETS, MANAGE_USERS, VIEW_AUDIT, has_permission

    account = current_user(request)
    if account:
        for permission in (MANAGE_USERS, MANAGE_TARGETS, VIEW_AUDIT):
            if has_permission(account["role"], permission):
                return permission_guard(request, permission)
        return Response("Forbidden", status_code=403)
    return permission_guard(request, MANAGE_USERS)


def _target_ids() -> list[str]:
    from na_sso.connectors import get_connectors

    return [connector.target_id for connector in get_connectors()]


@router.get("/dashboard")
async def dashboard_page(request: Request):
    guard = _console_guard(request)
    if isinstance(guard, Response):
        return guard
    from na_sso.db import get_session
    from na_sso.feedback import template_response
    from na_sso.main import templates
    from na_sso.permissions import permission_context

    with get_session() as db:
        data = eager_datasets(db, _target_ids())
    return template_response(
        templates,
        request,
        "dashboard.html",
        {
            "tiles": data["tiles"],
            "data_json": json.dumps(data),
            "admin": guard["username"],
            "admin_area": True,
            "permissions": permission_context(guard["role"]),
        },
    )


@router.get("/dashboard/insights")
async def dashboard_insights(request: Request):
    guard = _console_guard(request)
    if isinstance(guard, Response):
        return guard
    from na_sso.db import get_session

    with get_session() as db:
        return JSONResponse(insights_datasets(db))
