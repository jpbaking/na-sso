"""Read-only unmanaged-account discovery and explicit disposition workflows."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from na_sso.auth import current_user, permission_guard
from na_sso.audit import record_audit
from na_sso.config import get_settings
from na_sso.connectors import get_connectors
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import ManagedUser, SyncState, UnmanagedAccountFinding, utcnow
from na_sso.permissions import MANAGE_SECURITY, MANAGE_USERS, permission_context
from na_sso.security import hash_password, validate_password

router = APIRouter()
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?$")


def _guard(request: Request, permission: str = MANAGE_USERS) -> dict | Response:
    return permission_guard(request, permission)


def _excluded(username: str, target_type: str, uid: int | None) -> bool:
    policy = get_settings().file.unmanaged_account_policy
    return (
        username in policy.excluded_usernames
        or any(username.startswith(prefix) for prefix in policy.excluded_prefixes)
        or (target_type == "ssh" and uid is not None and uid < policy.ssh_min_uid)
    )


async def discover_unmanaged(actor: str, target_id: str | None = None) -> tuple[int, list[str]]:
    policy = get_settings().file.unmanaged_account_policy
    if not policy.enabled:
        return 0, ["Unmanaged-account discovery is disabled by policy."]
    connectors = [item for item in get_connectors() if target_id is None or item.target_id == target_id]
    discovered = 0
    errors: list[str] = []
    for connector in connectors:
        result = await connector.discover_accounts()
        if not result.supported:
            errors.append(f"{connector.display_name}: account discovery is unsupported")
            continue
        if result.detail:
            errors.append(f"{connector.display_name}: {result.detail}")
            continue
        accounts = result.accounts[:policy.max_accounts_per_target]
        with get_session() as db:
            assigned = {
                row[0] for row in db.query(ManagedUser.username).join(SyncState).filter(
                    SyncState.target == connector.target_id,
                    SyncState.assigned.is_(True), SyncState.retired.is_(False),
                ).all()
            }
            seen: set[str] = set()
            for remote in accounts:
                if not remote.username or _excluded(remote.username, connector.target_type, remote.uid):
                    continue
                seen.add(remote.username)
                finding = db.query(UnmanagedAccountFinding).filter_by(
                    target_id=connector.target_id, username=remote.username,
                ).one_or_none()
                if remote.username in assigned:
                    if finding:
                        finding.present = True
                        finding.last_seen_at = utcnow()
                        if finding.decision == "pending":
                            finding.decision = "managed"
                    continue
                if finding is None:
                    finding = UnmanagedAccountFinding(
                        target_id=connector.target_id, target_type=connector.target_type,
                        username=remote.username,
                    )
                    db.add(finding)
                finding.display_name = remote.display_name[:128]
                finding.email = remote.email[:254]
                finding.remote_status = remote.status[:24]
                finding.remote_uid = remote.uid
                finding.present = True
                finding.last_seen_at = utcnow()
                if finding.decision in {"managed", "resolved"}:
                    finding.decision = "pending"
                    finding.decision_actor = None
                    finding.decided_at = None
                discovered += 1
            for finding in db.query(UnmanagedAccountFinding).filter_by(
                target_id=connector.target_id, present=True,
            ).all():
                if finding.username not in seen:
                    finding.present = False
                    if finding.decision == "pending":
                        finding.decision = "resolved"
            record_audit(
                db, actor, "unmanaged.discovered", connector.target_id,
                f"observed={len(accounts)}; unmanaged={sum(1 for item in accounts if item.username in seen and item.username not in assigned)}",
            )
            db.commit()
    return discovered, errors


def _render(request: Request, *, errors: list[str] | None = None):
    from na_sso.main import templates
    account = current_user(request)
    definitions = {item.target_id: item for item in get_connectors()}
    with get_session() as db:
        findings = db.query(UnmanagedAccountFinding).order_by(
            UnmanagedAccountFinding.present.desc(), UnmanagedAccountFinding.target_id,
            UnmanagedAccountFinding.username,
        ).all()
        rows = [{
            "id": item.id, "target_id": item.target_id,
            "target_name": definitions[item.target_id].display_name if item.target_id in definitions else item.target_id,
            "target_type": item.target_type, "username": item.username,
            "display_name": item.display_name, "email": item.email,
            "remote_status": item.remote_status, "remote_uid": item.remote_uid,
            "decision": item.decision, "present": item.present,
            "first_seen_at": item.first_seen_at, "last_seen_at": item.last_seen_at,
            "removal_token": item.removal_token,
        } for item in findings]
    return template_response(templates, request, "unmanaged_accounts.html", {
        "admin": account["username"], "admin_area": True,
        "permissions": permission_context(account["role"]), "rows": rows,
        "targets": list(definitions.values()), "errors": errors or [],
        "policy": get_settings().file.unmanaged_account_policy,
    })


@router.get("/unmanaged-accounts")
async def unmanaged_accounts_page(request: Request):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    return _render(request)


@router.post("/unmanaged-accounts/discover")
async def run_discovery(request: Request, target_id: str = Form("")):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    count, errors = await discover_unmanaged(principal["username"], target_id or None)
    if errors:
        return _render(request, errors=errors)
    return redirect_with_feedback(
        "/unmanaged-accounts", title="Discovery complete",
        message=f"{count} unmanaged account observation(s) are ready for review.",
    )


@router.post("/unmanaged-accounts/{finding_id}/ignore")
async def ignore_finding(request: Request, finding_id: str):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        if not finding or not finding.present:
            return HTMLResponse("Finding is no longer present.", status_code=409)
        finding.decision = "ignored"
        finding.decision_actor = principal["username"]
        finding.decided_at = utcnow()
        record_audit(db, principal["username"], "unmanaged.ignored", finding.username, finding.target_id)
        db.commit()
    return redirect_with_feedback("/unmanaged-accounts", title="Account ignored", message="The decision persists across future discovery runs.")


@router.post("/unmanaged-accounts/{finding_id}/adopt")
async def adopt_finding(
    request: Request, finding_id: str,
    temporary_password: str = Form(""), confirm_password: str = Form(""),
):
    principal = _guard(request)
    if isinstance(principal, Response):
        return principal
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        if not finding or not finding.present or not USERNAME_RE.fullmatch(finding.username):
            return HTMLResponse("Finding cannot be adopted with the managed username contract.", status_code=422)
        user = db.query(ManagedUser).filter_by(username=finding.username).one_or_none()
        if user is None:
            if temporary_password != confirm_password:
                return HTMLResponse("Password confirmation does not match.", status_code=422)
            validation = validate_password(
                temporary_password, username=finding.username,
                email=finding.email, display_name=finding.display_name,
            )
            if not validation.valid:
                return HTMLResponse(" ".join(validation.errors), status_code=422)
            user = ManagedUser(
                username=finding.username, display_name=finding.display_name,
                email=finding.email, password_hash=hash_password(temporary_password),
                password_decision_required=True, password_decision_kind="initial",
                password_changed_at=utcnow(),
            )
            db.add(user)
            db.flush()
        state = db.query(SyncState).filter_by(user_id=user.id, target=finding.target_id).one_or_none()
        if state is None:
            state = SyncState(user=user, target=finding.target_id, target_type=finding.target_type)
            db.add(state)
        state.assigned = True
        state.retired = False
        state.state = "chpw" if user.password_decision_required else "awaiting_credentials"
        state.detail = "adopted remote identity; credential confirmation required before mutation"
        finding.decision = "adopted"
        finding.decision_actor = principal["username"]
        finding.decided_at = utcnow()
        record_audit(db, principal["username"], "unmanaged.adopted", finding.username, finding.target_id)
        db.commit()
    return redirect_with_feedback(
        "/unmanaged-accounts", title="Account adopted",
        message="The remote account was linked without mutation. Verified credential handoff is required before synchronization.",
    )


@router.post("/unmanaged-accounts/{finding_id}/approve-removal")
async def approve_removal(
    request: Request, finding_id: str,
    confirmation: str = Form(""), recovery_acknowledged: str = Form(""),
):
    principal = _guard(request, MANAGE_SECURITY)
    if isinstance(principal, Response):
        return principal
    if not get_settings().file.unmanaged_account_policy.allow_removal:
        return HTMLResponse("Unmanaged-account removal is disabled by policy.", status_code=403)
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        if not finding or not finding.present:
            return HTMLResponse("Finding is no longer present.", status_code=409)
        if confirmation != finding.username or recovery_acknowledged != "true":
            return HTMLResponse("Confirm the exact username and recovery acknowledgement.", status_code=422)
        finding.decision = "removal_approved"
        finding.decision_actor = principal["username"]
        finding.decided_at = utcnow()
        finding.removal_token = str(uuid4())
        record_audit(db, principal["username"], "unmanaged.removal_approved", finding.username, finding.target_id)
        db.commit()
    return redirect_with_feedback(
        "/unmanaged-accounts", title="Removal approved",
        message="Review the saved account snapshot, then execute the one-use removal approval.",
    )


@router.post("/unmanaged-accounts/{finding_id}/execute-removal")
async def execute_removal(
    request: Request, finding_id: str,
    token: str = Form(""), confirmation: str = Form(""),
):
    principal = _guard(request, MANAGE_SECURITY)
    if isinstance(principal, Response):
        return principal
    if not get_settings().file.unmanaged_account_policy.allow_removal:
        return HTMLResponse("Unmanaged-account removal is disabled by policy.", status_code=403)
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        if (
            not finding or not finding.present or finding.decision != "removal_approved"
            or not finding.removal_token or token != finding.removal_token
            or confirmation != finding.username
        ):
            return HTMLResponse("Removal approval is invalid or already used.", status_code=409)
        target_id, username = finding.target_id, finding.username
    connector = next((item for item in get_connectors() if item.target_id == target_id), None)
    if connector is None:
        return HTMLResponse("Target is unavailable; no removal was attempted.", status_code=409)
    result = await connector.delete_user(ManagedUser(username=username))
    with get_session() as db:
        finding = db.get(UnmanagedAccountFinding, finding_id)
        if result.ok:
            finding.decision = "removed"
            finding.present = False
            finding.removed_at = utcnow()
            finding.removal_token = None
        record_audit(
            db, principal["username"],
            "unmanaged.removed" if result.ok else "unmanaged.removal_failed",
            username, f"{target_id}; {result.detail}",
        )
        db.commit()
    if not result.ok:
        return HTMLResponse("Target removal failed safely; the approval remains available.", status_code=502)
    return redirect_with_feedback(
        "/unmanaged-accounts", title="Remote account removed",
        message="The approved target-local account was deleted and the decision was audited.",
    )
