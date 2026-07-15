"""Account lifecycle scheduling and attested access reviews."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.lifecycle import LifecycleCommand
from na_sso.models import (
    AccessReview,
    AccessReviewItem,
    AccountLifecyclePolicy,
    ManagedUser,
    as_utc,
    utcnow,
)
from na_sso.notifications import enqueue_notification
from na_sso.operations import OperationConflict, request_operation
from na_sso.permissions import MANAGE_USERS, permission_context
from na_sso.sync import sync_user


router = APIRouter()


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_USERS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("start and end must use a valid date and time") from error
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _policy_review_at(user: ManagedUser, days: int | None) -> datetime | None:
    if days is None:
        return None
    baseline = as_utc(user.last_authenticated_at) or as_utc(user.created_at) or _now()
    return baseline + timedelta(days=days)


@router.get("/users/{user_id}/lifecycle-policy")
async def lifecycle_policy_page(request: Request, user_id: int):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user is None or user.role != "user":
            return RedirectResponse("/users", status_code=303)
        policy = db.get(AccountLifecyclePolicy, user.id)
    return template_response(templates, request, "lifecycle_policy.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "user": user, "policy": policy,
    })


@router.post("/users/{user_id}/lifecycle-policy")
async def lifecycle_policy_save(
    request: Request, background_tasks: BackgroundTasks, user_id: int,
    owner: str = Form(...), reason: str = Form(...),
    starts_at: str = Form(""), ends_at: str = Form(""),
    temporary: str = Form(""), inactivity_review_days: int = Form(0),
    end_action: str = Form("disable"), confirm_delete: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    actor = principal["username"]
    try:
        start = _parse_datetime(starts_at)
        end = _parse_datetime(ends_at)
        if not owner.strip() or len(owner.strip()) > 100:
            raise ValueError("owner is required and must be at most 100 characters")
        if not reason.strip() or len(reason.strip()) > 1000:
            raise ValueError("access reason is required and must be at most 1,000 characters")
        if start and end and end <= start:
            raise ValueError("end must be after start")
        if temporary == "yes" and end is None:
            raise ValueError("temporary access requires an end date")
        if inactivity_review_days and not 1 <= inactivity_review_days <= 3650:
            raise ValueError("inactivity review must be 1–3,650 days")
        if end_action not in {"disable", "delete"}:
            raise ValueError("end action must be disable or delete")
        if end_action == "delete" and confirm_delete != "yes":
            raise ValueError("confirm scheduled remote deletion")
    except ValueError as error:
        return redirect_with_feedback(
            f"/users/{user_id}/lifecycle-policy", title="Lifecycle policy rejected",
            message=str(error), level="danger",
        )
    queued: tuple[int, str] | None = None
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user is None or user.role != "user" or user.desired_action == "delete":
            return RedirectResponse("/users", status_code=303)
        policy = db.get(AccountLifecyclePolicy, user.id)
        if policy is None:
            policy = AccountLifecyclePolicy(
                user_id=user.id, owner=owner.strip(), reason=reason.strip(),
                updated_by=actor,
            )
            db.add(policy)
        policy.owner = owner.strip()
        policy.reason = reason.strip()
        policy.starts_at = start
        policy.ends_at = end
        policy.temporary = temporary == "yes"
        policy.inactivity_review_days = inactivity_review_days or None
        policy.end_action = end_action
        policy.start_applied_at = None if start and start > _now() else (policy.start_applied_at or utcnow())
        policy.end_applied_at = None
        policy.next_review_at = _policy_review_at(user, policy.inactivity_review_days)
        policy.updated_by = actor
        if start and start > _now() and user.status != "disabled":
            try:
                operation = request_operation(db, user, LifecycleCommand.DISABLE, actor)
            except OperationConflict as error:
                return redirect_with_feedback(
                    f"/users/{user_id}/lifecycle-policy", title="Lifecycle policy blocked",
                    message=str(error), level="danger",
                )
            user.status = "disabled"
            queued = (user.id, operation.id)
        record_audit(
            db, actor, "lifecycle_policy.updated", user.username,
            f"owner={policy.owner}; temporary={policy.temporary}; end_action={end_action}; inactivity_days={policy.inactivity_review_days or 'disabled'}",
            queued[1] if queued else None,
        )
        db.commit()
    if queued:
        background_tasks.add_task(sync_user, queued[0], actor=actor, operation_id=queued[1])
    return redirect_with_feedback(
        f"/users/{user_id}/lifecycle-policy", title="Lifecycle policy saved",
        message="Ownership, timing, inactivity review, and the scheduled end action are now audited.",
    )


def _create_inactivity_reviews(now: datetime) -> int:
    created = 0
    with get_session() as db:
        due = db.query(AccountLifecyclePolicy).filter(
            AccountLifecyclePolicy.inactivity_review_days.is_not(None),
            AccountLifecyclePolicy.next_review_at.is_not(None),
            AccountLifecyclePolicy.next_review_at <= now,
        ).all()
        for policy in due:
            user = db.get(ManagedUser, policy.user_id)
            if user is None or user.desired_action == "delete":
                policy.next_review_at = None
                continue
            source_key = f"inactivity:{user.id}:{as_utc(policy.next_review_at).date().isoformat()}"
            if db.query(AccessReview).filter_by(source_key=source_key).first():
                policy.next_review_at = None
                continue
            review = AccessReview(
                name=f"Inactivity review — {user.username}",
                source="inactivity", source_key=source_key, status="open",
                due_at=now + timedelta(days=get_settings().file.lifecycle_automation_policy.reminder_days_before_due),
                created_by="system", opened_at=now,
            )
            db.add(review)
            db.flush()
            db.add(AccessReviewItem(
                review_id=review.id, user_id=user.id, username=user.username,
                owner=policy.owner, reason=policy.reason,
            ))
            policy.next_review_at = None
            record_audit(
                db, "system", "access_review.inactivity_opened", user.username,
                f"review={review.id}; last_authenticated={as_utc(user.last_authenticated_at)}",
            )
            created += 1
        db.commit()
    return created


async def apply_lifecycle_automation() -> int:
    now = _now()
    queued: list[tuple[int, str, str | None]] = []
    with get_session() as db:
        policies = db.query(AccountLifecyclePolicy).all()
        for policy in policies:
            user = db.get(ManagedUser, policy.user_id)
            if user is None or user.role != "user":
                continue
            start = as_utc(policy.starts_at)
            end = as_utc(policy.ends_at)
            if start and start <= now and policy.start_applied_at is None and user.desired_action != "delete":
                try:
                    operation = request_operation(db, user, LifecycleCommand.ENABLE, "lifecycle-scheduler")
                except OperationConflict:
                    continue
                user.status = "active"
                policy.start_applied_at = now
                queued.append((user.id, operation.id, None))
                record_audit(db, "lifecycle-scheduler", "lifecycle_policy.started", user.username, operation_id=operation.id)
            if end and end <= now and policy.end_applied_at is None:
                try:
                    command = LifecycleCommand.DELETE if policy.end_action == "delete" else LifecycleCommand.DISABLE
                    operation = request_operation(db, user, command, "lifecycle-scheduler")
                except OperationConflict:
                    continue
                if policy.end_action == "delete":
                    user.desired_action = "delete"
                    user.deletion_requested_at = now
                    user.deleted_at = None
                    action = "delete"
                else:
                    user.status = "disabled"
                    action = None
                policy.end_applied_at = now
                queued.append((user.id, operation.id, action))
                record_audit(
                    db, "lifecycle-scheduler", f"lifecycle_policy.{policy.end_action}d",
                    user.username, f"scheduled end={end.isoformat()}", operation.id,
                )
        db.commit()
    for user_id, operation_id, action in queued:
        await sync_user(
            user_id, action=action, actor="lifecycle-scheduler", operation_id=operation_id
        )
    return len(queued) + _create_inactivity_reviews(now)


def _send_review_reminders(review_id: str, actor: str) -> int:
    sent = 0
    with get_session() as db:
        review = db.get(AccessReview, review_id)
        if review is None or review.status != "open":
            return 0
        items = db.query(AccessReviewItem).filter_by(
            review_id=review.id, decision="pending"
        ).all()
        for item in items:
            item.reminded_at = utcnow()
            enqueue_notification(
                db, "access_review.reminder", actor=actor, subject=item.username,
                dedupe_key=f"{review.id}:{item.id}:{item.reminded_at.date().isoformat()}",
                outcome="pending",
            )
            record_audit(
                db, actor, "access_review.reminded", item.username,
                f"review={review.id}; due={as_utc(review.due_at).isoformat()}",
            )
            sent += 1
        db.commit()
    return sent


async def governance_worker() -> None:
    while True:
        await asyncio.sleep(get_settings().file.lifecycle_automation_policy.scan_seconds)
        await apply_lifecycle_automation()
        now = _now()
        reminder_window = now + timedelta(
            days=get_settings().file.lifecycle_automation_policy.reminder_days_before_due
        )
        with get_session() as db:
            due_ids = [
                review.id for review in db.query(AccessReview).filter(
                    AccessReview.status == "open", AccessReview.due_at <= reminder_window
                ).all()
                if any(item.decision == "pending" and item.reminded_at is None for item in review.items)
            ]
        for review_id in due_ids:
            _send_review_reminders(review_id, "review-scheduler")


@router.get("/access-reviews")
async def access_reviews_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        reviews = db.query(AccessReview).order_by(AccessReview.created_at.desc()).limit(50).all()
        users = db.query(ManagedUser).filter(
            ManagedUser.role == "user", ManagedUser.desired_action != "delete"
        ).order_by(ManagedUser.username).limit(
            get_settings().file.lifecycle_automation_policy.max_review_accounts
        ).all()
    return template_response(templates, request, "access_reviews.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "reviews": reviews, "users": users,
    })


@router.post("/access-reviews/preview")
async def access_review_preview(
    request: Request, name: str = Form(...), due_at: str = Form(...),
    user_ids: list[int] = Form(default=[]),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    try:
        due = _parse_datetime(due_at)
        if due is None or due <= _now():
            raise ValueError("review due date must be in the future")
        if not name.strip() or len(name.strip()) > 120:
            raise ValueError("review name is required and must be at most 120 characters")
    except ValueError as error:
        return redirect_with_feedback(
            "/access-reviews", title="Review preview rejected",
            message=str(error), level="danger",
        )
    selected = list(dict.fromkeys(user_ids))[:get_settings().file.lifecycle_automation_policy.max_review_accounts]
    with get_session() as db:
        users = db.query(ManagedUser).filter(
            ManagedUser.id.in_(selected), ManagedUser.role == "user",
            ManagedUser.desired_action != "delete",
        ).order_by(ManagedUser.username).all() if selected else []
        if not users:
            return redirect_with_feedback(
                "/access-reviews", title="Review preview rejected",
                message="Select at least one available managed user.", level="danger",
            )
        review = AccessReview(
            name=name.strip(), due_at=due, created_by=principal["username"]
        )
        db.add(review)
        db.flush()
        for user in users:
            policy = db.get(AccountLifecyclePolicy, user.id)
            db.add(AccessReviewItem(
                review_id=review.id, user_id=user.id, username=user.username,
                owner=policy.owner if policy else "Unassigned",
                reason=policy.reason if policy else "No lifecycle reason recorded",
            ))
        record_audit(
            db, principal["username"], "access_review.previewed", review.name,
            f"accounts={len(users)}; due={due.isoformat()}",
        )
        db.commit()
        review_id = review.id
    return RedirectResponse(f"/access-reviews/{review_id}", status_code=303)


@router.get("/access-reviews/{review_id}")
async def access_review_detail(request: Request, review_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        review = db.get(AccessReview, review_id)
        if review is None:
            return RedirectResponse("/access-reviews", status_code=303)
        review.items
    return template_response(templates, request, "access_review_detail.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]), "review": review,
    })


@router.post("/access-reviews/{review_id}/open")
async def access_review_open(
    request: Request, review_id: str, approval_token: str = Form(...)
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        review = db.get(AccessReview, review_id)
        if review is None or review.approval_token != approval_token:
            return redirect_with_feedback(
                "/access-reviews", title="Review approval rejected",
                message="The saved preview token is invalid.", level="danger",
            )
        if review.status == "draft":
            review.status = "open"
            review.opened_at = utcnow()
            record_audit(
                db, principal["username"], "access_review.opened", review.name,
                f"items={len(review.items)}; due={as_utc(review.due_at).isoformat()}",
            )
            db.commit()
    return redirect_with_feedback(
        f"/access-reviews/{review_id}", title="Review opened",
        message="Reviewers can now record attested retain, disable, or delete decisions.",
    )


@router.post("/access-reviews/{review_id}/remind")
async def access_review_remind(request: Request, review_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    sent = _send_review_reminders(review_id, principal["username"])
    return redirect_with_feedback(
        f"/access-reviews/{review_id}", title="Reminders recorded",
        message=f"{sent} pending reviewer reminder(s) were audited and queued.",
    )


@router.post("/access-reviews/{review_id}/items/{item_id}")
async def access_review_decision(
    request: Request, background_tasks: BackgroundTasks,
    review_id: str, item_id: int, decision: str = Form(...),
    attestation: str = Form(...), confirm_delete: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    actor = principal["username"]
    if decision not in {"retain", "disable", "delete"} or len(attestation.strip()) < 10:
        return redirect_with_feedback(
            f"/access-reviews/{review_id}", title="Decision rejected",
            message="Choose retain, disable, or delete and provide an attestation of at least 10 characters.",
            level="danger",
        )
    if decision == "delete" and confirm_delete != "yes":
        return redirect_with_feedback(
            f"/access-reviews/{review_id}", title="Deletion confirmation required",
            message="Confirm the attested remote account deletion decision.", level="danger",
        )
    queued: tuple[int, str, str | None] | None = None
    with get_session() as db:
        review = db.get(AccessReview, review_id)
        item = db.get(AccessReviewItem, item_id)
        if review is None or item is None or item.review_id != review.id or review.status != "open":
            return RedirectResponse("/access-reviews", status_code=303)
        if item.decision != "pending":
            return redirect_with_feedback(
                f"/access-reviews/{review.id}", title="Decision already recorded",
                message="The existing attestation and action were not repeated.",
            )
        user = db.get(ManagedUser, item.user_id)
        policy = db.get(AccountLifecyclePolicy, item.user_id)
        operation = None
        if decision in {"disable", "delete"} and user and user.desired_action != "delete":
            try:
                operation = request_operation(
                    db, user,
                    LifecycleCommand.DELETE if decision == "delete" else LifecycleCommand.DISABLE,
                    actor,
                )
            except OperationConflict as error:
                return redirect_with_feedback(
                    f"/access-reviews/{review.id}", title="Decision blocked",
                    message=str(error), level="danger",
                )
            if decision == "delete":
                user.desired_action = "delete"
                user.deletion_requested_at = utcnow()
                user.deleted_at = None
                action = "delete"
            else:
                user.status = "disabled"
                action = None
            queued = (user.id, operation.id, action)
        if policy:
            policy.last_reviewed_at = utcnow()
            interval = policy.inactivity_review_days or get_settings().file.lifecycle_automation_policy.default_review_interval_days
            policy.next_review_at = utcnow() + timedelta(days=interval) if decision == "retain" else None
        item.decision = decision
        item.attestation = attestation.strip()[:1000]
        item.reviewer = actor
        item.decided_at = utcnow()
        item.operation_id = operation.id if operation else None
        record_audit(
            db, actor, f"access_review.{decision}", item.username,
            f"review={review.id}; attested=yes", item.operation_id,
        )
        pending = db.query(AccessReviewItem).filter_by(
            review_id=review.id, decision="pending"
        ).filter(AccessReviewItem.id != item.id).count()
        if pending == 0:
            review.status = "completed"
            review.completed_at = utcnow()
        db.commit()
    if queued:
        background_tasks.add_task(
            sync_user, queued[0], action=queued[2], actor=actor, operation_id=queued[1]
        )
    return redirect_with_feedback(
        f"/access-reviews/{review_id}", title="Decision recorded",
        message=f"The {decision} attestation is audited and any lifecycle action is correlated.",
    )
