"""Versioned assignment profiles and durable per-user exceptions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse, Response

from na_sso.auth import permission_guard
from na_sso.audit import record_audit
from na_sso.connectors import Connector, get_connectors
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.lifecycle import LifecycleCommand
from na_sso.models import (
    AssignmentProfile,
    AssignmentProfileTarget,
    ManagedUser,
    ProfileApplication,
    UserAssignmentException,
    UserAssignmentProfile,
    utcnow,
)
from na_sso.operations import OperationConflict, request_operation
from na_sso.permissions import MANAGE_USERS, permission_context
from na_sso.sync import sync_user
from na_sso.users import _set_pending


router = APIRouter()
MEMBERSHIP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _guard(request: Request) -> dict | Response:
    return permission_guard(request, MANAGE_USERS)


def _json_set(value: str) -> frozenset[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return frozenset()
    return frozenset(str(item) for item in parsed) if isinstance(parsed, list) else frozenset()


def profile_target_map(db, profile_id: str) -> dict[str, frozenset[str]]:
    rows = db.query(AssignmentProfileTarget).filter_by(profile_id=profile_id).all()
    return {row.target_id: _json_set(row.memberships) for row in rows}


def resolve_assignment_intents(
    db, user: ManagedUser, connectors: Mapping[str, Connector]
) -> dict[str, frozenset[str]]:
    """Resolve profile version plus durable exceptions into target memberships."""
    assignment = db.get(UserAssignmentProfile, user.id)
    if assignment:
        intents = profile_target_map(db, assignment.profile_id)
    else:
        intents = {
            state.target: connectors[state.target].default_memberships
            for state in user.sync_states
            if state.assigned and not state.retired and state.target in connectors
        }
    exceptions = db.query(UserAssignmentException).filter_by(user_id=user.id).all()
    for exception in exceptions:
        if exception.assignment_mode == "exclude":
            intents.pop(exception.target_id, None)
            continue
        if exception.assignment_mode == "include" and exception.target_id not in intents:
            connector = connectors.get(exception.target_id)
            if connector:
                intents[exception.target_id] = connector.default_memberships
        if exception.target_id not in intents:
            continue
        memberships = set(intents[exception.target_id])
        memberships.update(_json_set(exception.add_memberships))
        memberships.difference_update(_json_set(exception.remove_memberships))
        intents[exception.target_id] = frozenset(memberships)
    return intents


def record_selected_target_exceptions(
    db, user: ManagedUser, selected: set[str], *, actor: str = "lifecycle"
) -> None:
    """Preserve manual target choices as exceptions when a profile owns the base."""
    assignment = db.get(UserAssignmentProfile, user.id)
    if assignment is None:
        return
    base = set(profile_target_map(db, assignment.profile_id))
    existing = {
        row.target_id: row
        for row in db.query(UserAssignmentException).filter_by(user_id=user.id).all()
    }
    for target_id in base | selected | set(existing):
        desired_mode = (
            "include" if target_id in selected and target_id not in base
            else "exclude" if target_id not in selected and target_id in base
            else "inherit"
        )
        row = existing.get(target_id)
        if row is None and desired_mode == "inherit":
            continue
        if row is None:
            row = UserAssignmentException(
                user_id=user.id, target_id=target_id, updated_by=actor
            )
            db.add(row)
        row.assignment_mode = desired_mode
        row.updated_by = actor
        if (
            desired_mode == "inherit"
            and not _json_set(row.add_memberships)
            and not _json_set(row.remove_memberships)
        ):
            db.delete(row)


def assignment_context(db, user: ManagedUser, connectors: Mapping[str, Connector]) -> dict:
    assignment = db.get(UserAssignmentProfile, user.id)
    profile = db.get(AssignmentProfile, assignment.profile_id) if assignment else None
    exceptions = db.query(UserAssignmentException).filter_by(user_id=user.id).order_by(
        UserAssignmentException.target_id
    ).all()
    intents = resolve_assignment_intents(db, user, connectors)
    return {
        "profile": profile,
        "exceptions": exceptions,
        "intents": intents,
    }


def _parse_bundle(bundle: str, connectors: Mapping[str, Connector]) -> dict[str, frozenset[str]]:
    targets: dict[str, frozenset[str]] = {}
    for number, raw_line in enumerate(bundle.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        target_id, separator, raw_memberships = line.partition(":")
        target_id = target_id.strip()
        if not separator or target_id not in connectors:
            raise ValueError(f"line {number}: use configured_target:group_or_role|group_or_role")
        if target_id in targets:
            raise ValueError(f"line {number}: target appears more than once")
        memberships = frozenset(
            item.strip() for item in re.split(r"[|,]", raw_memberships) if item.strip()
        )
        if any(not MEMBERSHIP_RE.fullmatch(item) for item in memberships):
            raise ValueError(f"line {number}: memberships must be safe identifiers")
        targets[target_id] = memberships
    if not targets:
        raise ValueError("profile requires at least one target mapping")
    return targets


@router.get("/assignment-profiles")
async def profiles_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        profiles = db.query(AssignmentProfile).order_by(
            AssignmentProfile.name, AssignmentProfile.version.desc()
        ).all()
        for profile in profiles:
            profile.targets
    return template_response(templates, request, "assignment_profiles.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "profiles": profiles, "targets": get_connectors(),
    })


@router.post("/assignment-profiles/preview")
async def profile_preview(
    request: Request,
    name: str = Form(...), description: str = Form(""), bundle: str = Form(...),
    profile_key: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    connectors = {item.target_id: item for item in get_connectors()}
    try:
        target_map = _parse_bundle(bundle, connectors)
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 100 or len(description) > 1000:
            raise ValueError("profile name or description exceeds safe bounds")
        with get_session() as db:
            if profile_key:
                previous = db.query(AssignmentProfile).filter_by(
                    profile_key=profile_key, status="published"
                ).order_by(AssignmentProfile.version.desc()).first()
                if previous is None:
                    raise ValueError("profile version source was not found")
                version = previous.version + 1
            else:
                version = 1
            profile = AssignmentProfile(
                profile_key=profile_key or str(__import__("uuid").uuid4()),
                version=version, name=clean_name, description=description.strip(),
                created_by=principal["username"],
            )
            db.add(profile)
            db.flush()
            for target_id, memberships in target_map.items():
                db.add(AssignmentProfileTarget(
                    profile_id=profile.id, target_id=target_id,
                    memberships=json.dumps(sorted(memberships)),
                ))
            record_audit(
                db, principal["username"], "assignment_profile.previewed",
                f"{profile.name}:v{profile.version}", f"targets={len(target_map)}",
            )
            db.commit()
            profile_id = profile.id
    except ValueError as error:
        return redirect_with_feedback(
            "/assignment-profiles", title="Profile preview rejected",
            message=str(error), level="danger",
        )
    return RedirectResponse(f"/assignment-profiles/{profile_id}", status_code=303)


@router.get("/assignment-profiles/{profile_id}")
async def profile_detail(request: Request, profile_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        profile = db.get(AssignmentProfile, profile_id)
        if profile is None:
            return RedirectResponse("/assignment-profiles", status_code=303)
        profile.targets
        users = db.query(ManagedUser).filter(
            ManagedUser.role == "user", ManagedUser.desired_action != "delete"
        ).order_by(ManagedUser.username).limit(500).all()
        applications = db.query(ProfileApplication).filter_by(profile_id=profile.id).order_by(
            ProfileApplication.created_at.desc()
        ).limit(20).all()
    return template_response(templates, request, "assignment_profile_detail.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "profile": profile, "users": users, "applications": applications,
    })


@router.post("/assignment-profiles/{profile_id}/publish")
async def profile_publish(
    request: Request, profile_id: str, approval_token: str = Form(...)
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        profile = db.get(AssignmentProfile, profile_id)
        if profile is None or profile.approval_token != approval_token:
            return redirect_with_feedback(
                "/assignment-profiles", title="Profile approval rejected",
                message="The saved preview token is invalid.", level="danger",
            )
        if profile.status == "draft":
            profile.status = "published"
            profile.published_at = utcnow()
            record_audit(
                db, principal["username"], "assignment_profile.published",
                f"{profile.name}:v{profile.version}", f"profile_id={profile.id}",
            )
            db.commit()
    return redirect_with_feedback(
        f"/assignment-profiles/{profile_id}", title="Profile published",
        message="This immutable version is now available for assignment.",
    )


@router.post("/assignment-profiles/{profile_id}/apply/preview")
async def profile_apply_preview(
    request: Request, profile_id: str, user_id: int = Form(...)
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        profile = db.get(AssignmentProfile, profile_id)
        user = db.get(ManagedUser, user_id)
        if (
            profile is None or profile.status != "published" or user is None
            or user.role != "user" or user.desired_action == "delete"
        ):
            return redirect_with_feedback(
                f"/assignment-profiles/{profile_id}", title="Assignment preview rejected",
                message="Choose a published profile and available managed user.", level="danger",
            )
        profile_targets = set(profile_target_map(db, profile.id))
        current = {
            state.target for state in user.sync_states if state.assigned and not state.retired
        }
        preserved = sorted(current - profile_targets)
        added = sorted(profile_targets - current)
        application = ProfileApplication(
            user_id=user.id, profile_id=profile.id, actor=principal["username"],
            detail=json.dumps({"added": added, "preserved_as_exceptions": preserved}),
        )
        db.add(application)
        record_audit(
            db, principal["username"], "assignment_profile.apply_previewed",
            user.username, f"profile={profile.name}:v{profile.version}; added={len(added)}; preserved={len(preserved)}",
        )
        db.commit()
        application_id = application.id
    return RedirectResponse(
        f"/assignment-profiles/applications/{application_id}", status_code=303
    )


@router.get("/assignment-profiles/applications/{application_id}")
async def profile_application_detail(request: Request, application_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    with get_session() as db:
        application = db.get(ProfileApplication, application_id)
        if application is None:
            return RedirectResponse("/assignment-profiles", status_code=303)
        profile = db.get(AssignmentProfile, application.profile_id)
        user = db.get(ManagedUser, application.user_id)
        changes = json.loads(application.detail)
    return template_response(templates, request, "assignment_application.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "application": application, "profile": profile, "user": user,
        "changes": changes,
    })


@router.post("/assignment-profiles/applications/{application_id}/confirm")
async def profile_application_confirm(
    request: Request, background_tasks: BackgroundTasks, application_id: str,
    approval_token: str = Form(...),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    actor = principal["username"]
    connectors = {item.target_id: item for item in get_connectors()}
    with get_session() as db:
        application = db.get(ProfileApplication, application_id)
        if application is None or application.approval_token != approval_token:
            return redirect_with_feedback(
                "/assignment-profiles", title="Assignment rejected",
                message="The saved application token is invalid.", level="danger",
            )
        if application.status != "previewed":
            return redirect_with_feedback(
                f"/assignment-profiles/applications/{application.id}",
                title="Assignment already handled",
                message="This saved application was not repeated.",
            )
        profile = db.get(AssignmentProfile, application.profile_id)
        user = db.get(ManagedUser, application.user_id)
        if profile is None or profile.status != "published" or user is None:
            return redirect_with_feedback(
                "/assignment-profiles", title="Assignment rejected",
                message="The profile or account is no longer available.", level="danger",
            )
        current = {
            state.target for state in user.sync_states if state.assigned and not state.retired
        }
        base = set(profile_target_map(db, profile.id))
        assignment = db.get(UserAssignmentProfile, user.id)
        if assignment is None:
            assignment = UserAssignmentProfile(
                user_id=user.id, profile_id=profile.id, assigned_by=actor
            )
            db.add(assignment)
        else:
            assignment.profile_id = profile.id
            assignment.assigned_by = actor
            assignment.assigned_at = utcnow()
        for target_id in current - base:
            exception = db.query(UserAssignmentException).filter_by(
                user_id=user.id, target_id=target_id
            ).one_or_none()
            if exception is None:
                exception = UserAssignmentException(
                    user_id=user.id, target_id=target_id, updated_by=actor
                )
                db.add(exception)
            exception.assignment_mode = "include"
            exception.updated_by = actor
        db.flush()
        intents = resolve_assignment_intents(db, user, connectors)
        try:
            operation = request_operation(db, user, LifecycleCommand.UPDATE, actor)
        except OperationConflict as error:
            return redirect_with_feedback(
                f"/assignment-profiles/applications/{application.id}",
                title="Assignment blocked", message=str(error), level="danger",
            )
        _set_pending(db, user, None, set(intents), update_assignment_exceptions=False)
        application.status = "applied"
        application.operation_id = operation.id
        application.applied_at = utcnow()
        record_audit(
            db, actor, "assignment_profile.applied", user.username,
            f"profile={profile.name}:v{profile.version}; exceptions_preserved={len(current - base)}",
            operation.id,
        )
        db.commit()
        user_id, operation_id = user.id, operation.id
    background_tasks.add_task(sync_user, user_id, actor=actor, operation_id=operation_id)
    return redirect_with_feedback(
        f"/users/{user_id}/assignment-exceptions", title="Profile assigned",
        message=f"Version {profile.version} was applied and explicit exceptions were preserved.",
    )


@router.get("/users/{user_id}/assignment-exceptions")
async def assignment_exceptions_page(request: Request, user_id: int):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    from na_sso.main import templates
    connectors = {item.target_id: item for item in get_connectors()}
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user is None or user.role != "user":
            return RedirectResponse("/users", status_code=303)
        context = assignment_context(db, user, connectors)
    return template_response(templates, request, "assignment_exceptions.html", {
        "admin": principal["username"], "admin_area": True,
        "permissions": permission_context(principal["role"]),
        "user": user, "connectors": list(connectors.values()), **context,
    })


@router.post("/users/{user_id}/assignment-exceptions")
async def assignment_exception_save(
    request: Request, background_tasks: BackgroundTasks, user_id: int,
    target_id: str = Form(...), assignment_mode: str = Form("inherit"),
    add_memberships: str = Form(""), remove_memberships: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    actor = principal["username"]
    connectors = {item.target_id: item for item in get_connectors()}
    additions = frozenset(item for item in re.split(r"[|,\s]+", add_memberships) if item)
    removals = frozenset(item for item in re.split(r"[|,\s]+", remove_memberships) if item)
    if (
        target_id not in connectors or assignment_mode not in {"inherit", "include", "exclude"}
        or any(not MEMBERSHIP_RE.fullmatch(item) for item in additions | removals)
        or additions & removals
    ):
        return redirect_with_feedback(
            f"/users/{user_id}/assignment-exceptions", title="Exception rejected",
            message="Choose a configured target, one assignment mode, and distinct safe membership names.",
            level="danger",
        )
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if user is None or user.role != "user" or user.desired_action == "delete":
            return RedirectResponse("/users", status_code=303)
        row = db.query(UserAssignmentException).filter_by(
            user_id=user.id, target_id=target_id
        ).one_or_none()
        if row is None:
            row = UserAssignmentException(
                user_id=user.id, target_id=target_id, updated_by=actor
            )
            db.add(row)
        row.assignment_mode = assignment_mode
        row.add_memberships = json.dumps(sorted(additions))
        row.remove_memberships = json.dumps(sorted(removals))
        row.updated_by = actor
        db.flush()
        intents = resolve_assignment_intents(db, user, connectors)
        try:
            operation = request_operation(db, user, LifecycleCommand.UPDATE, actor)
        except OperationConflict as error:
            return redirect_with_feedback(
                f"/users/{user_id}/assignment-exceptions", title="Exception blocked",
                message=str(error), level="danger",
            )
        _set_pending(db, user, None, set(intents), update_assignment_exceptions=False)
        record_audit(
            db, actor, "assignment_exception.updated", user.username,
            f"target={target_id}; mode={assignment_mode}; additions={len(additions)}; removals={len(removals)}",
            operation.id,
        )
        db.commit()
        operation_id = operation.id
    background_tasks.add_task(sync_user, user_id, actor=actor, operation_id=operation_id)
    return redirect_with_feedback(
        f"/users/{user_id}/assignment-exceptions", title="Exception saved",
        message="The explicit override is visible and will be retained during reconciliation.",
    )
