import asyncio
import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from na_sso.auth import current_user, permission_guard
from na_sso.connectors import get_connectors
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback, template_response
from na_sso.models import ManagedUser
from na_sso.lifecycle import LifecycleCommand, sync_state_payload
from na_sso.operations import get_latest_operation, operation_payload
from na_sso.permissions import (
    MANAGE_TARGETS,
    MANAGE_USERS,
    has_permission,
    permission_context,
)
from na_sso.audit import record_audit
from na_sso.target_credentials import (
    readiness_map,
    record_probe,
    sanitise_probe_detail,
    save_credentials,
    target_definitions,
)

router = APIRouter()


def _configuration_status(
    *, configured: bool, verified: bool, detail: str, reachable: bool | None = None
) -> str:
    if verified and reachable is False:
        return "reachability failed"
    if verified:
        return "fully configured"
    if not configured:
        return "configuration required"
    normalised = detail.lower()
    if any(marker in normalised for marker in (
        "401", "403", "auth", "unauthor", "forbidden", "permission denied",
    )):
        return "auth failed"
    if any(marker in normalised for marker in (
        "unreachable", "connection", "connect", "timeout", "timed out",
        "name or service", "network",
    )):
        return "Unreachable"
    return "verification failed"


def sync_snapshot() -> dict:
    targets = get_connectors()
    with get_session() as db:
        users = db.query(ManagedUser).order_by(ManagedUser.username).all()
        return {
            "users": [
                {
                    "id": user.id,
                    "desired_action": user.desired_action,
                    "deleted": user.deleted_at is not None,
                    "operation": operation_payload(
                        get_latest_operation(
                            db,
                            user,
                            LifecycleCommand.DELETE
                            if user.desired_action == "delete"
                            else None,
                        ),
                        user.sync_states,
                    ),
                    "states": _snapshot_states(user, targets),
                }
                for user in users
            ]
        }


def _snapshot_states(user: ManagedUser, targets: list) -> dict[str, dict]:
    states = {state.target: state for state in user.sync_states}
    payloads = {}
    for target in targets:
        state = states.get(target.target_id)
        payloads[target.target_id] = sync_state_payload(
            state.state if state else None,
            assigned=state.assigned if state else False,
            retired=state.retired if state else False,
            desired_action=user.desired_action,
            detail=state.detail if state else "",
            attempt_count=state.attempt_count if state else 0,
            next_retry_at=state.next_retry_at if state else None,
            operation_id=state.operation_id if state else None,
        )
    return payloads


@router.get("/events/sync")
async def sync_events(request: Request, once: bool = False):
    principal = current_user(request)
    if not principal or not (
        has_permission(principal["role"], MANAGE_USERS)
        or has_permission(principal["role"], MANAGE_TARGETS)
    ):
        return StreamingResponse(iter(["event: unauthorized\ndata: {}\n\n"]), status_code=401, media_type="text/event-stream")

    async def events():
        previous = None
        ticks = 0
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(sync_snapshot(), separators=(",", ":"))
            if payload != previous:
                yield f"event: sync\ndata: {payload}\n\n"
                previous = payload
                if once:
                    break
            elif ticks % 15 == 0:
                yield ": keepalive\n\n"
            ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/status")
async def status_page(request: Request):
    from na_sso.main import templates

    principal = permission_guard(request, MANAGE_TARGETS)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    readiness = readiness_map()
    definitions = target_definitions()
    probes = []
    for target in definitions:
        item = readiness[target.id]
        probes.append({
            "id": target.id,
            "name": target.display_name,
            "type": target.type,
            "ok": item.verified and item.reachable is not False,
            "detail": item.detail,
            "configured": item.configured,
            "verified": item.verified,
            "reachable": item.reachable,
            "failure_kind": item.failure_kind,
            "auth_mode": item.auth_mode or "password",
            "revision": item.revision,
            "updated_at": item.updated_at,
            "last_checked_at": item.last_checked_at,
            "last_success_at": item.last_success_at,
            "probe_attempt_count": item.probe_attempt_count,
            "next_probe_at": item.next_probe_at,
            "configuration_status": _configuration_status(
                configured=item.configured,
                verified=item.verified,
                detail=item.detail,
                reachable=item.reachable,
            ),
        })
    if not get_settings().config_file:
        probes = []
        for connector in get_connectors():
            result = await connector.probe()
            probes.append({"id": connector.target_id, "name": connector.display_name,
                           "type": connector.target_type, "ok": result.ok,
                           "detail": result.detail, "configured": True,
                           "verified": result.ok, "reachable": result.ok,
                           "failure_kind": "" if result.ok else "unreachable",
                           "revision": None, "updated_at": None,
                           "last_checked_at": None, "last_success_at": None,
                           "probe_attempt_count": 0, "next_probe_at": None,
                           "auth_mode": "password",
                           "configuration_status": _configuration_status(
                               configured=True, verified=result.ok, detail=result.detail
                           )})
    return template_response(
        templates,
        request,
        "status.html",
        {
            "admin": admin,
            "admin_area": True,
            "permissions": permission_context(principal["role"]),
            "probes": probes,
            "expanded_target": request.query_params.get("target", ""),
        },
    )


@router.post("/targets/{target_id}/credentials")
async def configure_target(request: Request, target_id: str,
                           auth_mode: str = Form("password"),
                           admin_user: str = Form(""), password: str = Form(""),
                           api_key: str = Form(""), api_secret: str = Form(""),
                           api_token: str = Form(""),
                           private_key: UploadFile | None = File(default=None)):
    principal = permission_guard(request, MANAGE_TARGETS)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    target = next((item for item in target_definitions() if item.id == target_id), None)
    if target is None:
        return redirect_with_feedback(
            "/status",
            title="Target not found",
            message="The requested target is no longer configured.",
            level="danger",
        )
    payload: dict[str, str]
    if target.type == "opnsense":
        payload = {"api_key": api_key.strip(), "api_secret": api_secret}
    elif target.type in {"nexus", "nextcloud", "npm"}:
        payload = {"admin_user": admin_user.strip(), "admin_password": password}
    elif target.type in {"gitlab", "gitea", "immich"}:
        payload = {"api_token": api_token}
    elif target.type == "jenkins":
        payload = {"admin_user": admin_user.strip(), "api_token": api_token}
    else:
        uploaded = (await private_key.read()).decode("utf-8") if private_key and private_key.filename else ""
        payload = {"management_user": admin_user.strip(),
                   "management_password": password if auth_mode in {
                       "password", "password_and_private_key"
                   } else "",
                   "management_private_key": uploaded if auth_mode in {
                       "private_key", "password_and_private_key"
                   } else ""}
    try:
        save_credentials(target_id, auth_mode, payload)
    except (UnicodeDecodeError, ValueError) as error:
        return redirect_with_feedback(
            f"/status?target={target_id}",
            title="Credentials not saved",
            message=str(error),
            level="danger",
        )
    from na_sso.connectors.base import build_unverified_connector
    try:
        result = await build_unverified_connector(target_id).probe()
        record_probe(target_id, result.ok, result.detail)
    except ValueError as error:
        result = type("Result", (), {"ok": False, "detail": str(error)})()
        record_probe(target_id, False, result.detail)
    safe_detail = sanitise_probe_detail(result.detail)
    with get_session() as db:
        record_audit(db, admin, "target.credentials.updated", target_id,
                     f"{target.type} {auth_mode}; "
                     f"{'verified' if result.ok else 'failed'} — {safe_detail}")
        db.commit()
    return redirect_with_feedback(
        f"/status?target={target_id}",
        title="Credentials verified" if result.ok else "Connection check failed",
        message=(
            "The credential revision was saved and the target accepted the connection."
            if result.ok
            else f"The credential revision was saved, but verification failed: {safe_detail}"
        ),
        level="success" if result.ok else "danger",
    )


@router.post("/targets/{target_id}/probe")
async def probe_target(request: Request, target_id: str):
    principal = permission_guard(request, MANAGE_TARGETS)
    if isinstance(principal, Response):
        return principal
    admin = principal["username"]
    readiness = readiness_map().get(target_id)
    if readiness is None or not readiness.configured:
        return redirect_with_feedback(
            f"/status?target={target_id}",
            title="Test unavailable",
            message="Save complete management credentials before testing the connection.",
            level="danger",
        )
    from na_sso.connectors.base import build_unverified_connector
    try:
        result = await build_unverified_connector(target_id).probe()
        record_probe(target_id, result.ok, result.detail)
    except ValueError as error:
        result = type("Result", (), {"ok": False, "detail": str(error)})()
        record_probe(target_id, False, result.detail)
    safe_detail = sanitise_probe_detail(result.detail)
    with get_session() as db:
        record_audit(db, admin, "target.probe", target_id,
                     f"{'verified' if result.ok else 'failed'} — {safe_detail}")
        db.commit()
    return redirect_with_feedback(
        f"/status?target={target_id}",
        title="Target reachable" if result.ok else "Connection check failed",
        message=(
            "The target accepted the connection."
            if result.ok
            else f"The target did not accept the connection: {safe_detail}"
        ),
        level="success" if result.ok else "danger",
    )
