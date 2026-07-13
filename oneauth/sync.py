import asyncio
from datetime import datetime, timedelta, timezone

from oneauth.audit import record_audit
from oneauth.config import get_settings
from oneauth.connectors import get_connectors
from oneauth.db import get_session
from oneauth.models import ManagedUser, SyncState
from oneauth.security import decrypt_secret
from oneauth.security import encrypt_secret

_scan_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def credential_handoff(user_id: int, password: str) -> None:
    """Stage a verified credential only while assigned targets consume it."""
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user or user.is_root:
            return
        waiting = [state for state in user.sync_states if state.assigned and not state.retired
                   and state.state in {"awaiting_credentials", "expired_disabled"}]
        if not waiting:
            return
        user.pending_secret = encrypt_secret(password)
        for state in waiting:
            state.state = "pending"
            state.detail = "credential supplied after verified authentication"
        db.commit()
    await sync_user(user_id, actor="verified-login")


async def sync_user(user_id: int, action: str | None = None, target: str | None = None, actor: str = "system") -> None:
    available = {c.target_id: c for c in get_connectors()}
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user:
            return
        if user.is_root:
            return
        operation = action or user.desired_action
        password = decrypt_secret(user.pending_secret) if user.pending_secret else None
        states = {state.target: state for state in user.sync_states}
        if not states and not get_settings().config_file:
            for connector in available.values():
                state = SyncState(user=user, target=connector.target_id,
                                  target_type=connector.target_type, assigned=True)
                db.add(state)
                states[state.target] = state
            db.flush()
        connectors = [available[state.target] for state in user.sync_states
                      if (state.assigned or state.state == "pending_disable") and not state.retired and state.target in available
                      and (target is None or state.target == target)]
        for connector in connectors:
            state = states[connector.target_id]
            if state.state == "awaiting_credentials" and password is None:
                continue
            state.state, state.detail, state.next_retry_at = "pending", "", None
            db.commit()
            if operation == "delete":
                result = await connector.delete_user(user)
            elif user.status == "disabled" or not state.assigned or (user.password_decision_required and get_settings().config_file):
                result = await connector.disable_user(user)
            else:
                result = await connector.ensure_user(user, password)
            state.state = "ok" if result.ok else "failed"
            state.detail = result.detail
            if result.ok:
                state.attempt_count, state.next_retry_at = 0, None
                if not state.assigned:
                    state.state = "unassigned"
                elif user.password_decision_required and get_settings().config_file:
                    state.state = "expired_disabled"
            else:
                state.attempt_count += 1
                settings = get_settings()
                delay = min(settings.retry_base_seconds * (2 ** (state.attempt_count - 1)), settings.retry_max_seconds)
                state.next_retry_at = _now() + timedelta(seconds=delay)
            record_audit(db, actor, f"sync.{operation}", user.username, f"{connector.target_id}: {state.state} — {result.detail}")
            db.commit()
        enabled = {s.target for s in user.sync_states if s.assigned and not s.retired and s.target in available}
        all_ok = ((operation == "delete" and not enabled) or (bool(enabled) and all(states.get(name) and states[name].state == "ok" for name in enabled)))
        if all_ok:
            if operation == "delete":
                user.deleted_at = _now()
            else:
                user.pending_secret = None
            db.commit()


async def retry_due() -> int:
    async with _scan_lock:
        now = _now()
        with get_session() as db:
            due = [(s.user_id, s.target) for s in db.query(SyncState).filter(SyncState.assigned.is_(True), SyncState.retired.is_(False), SyncState.state == "failed", SyncState.next_retry_at <= now).all()]
        for user_id, target in due:
            await sync_user(user_id, target=target, actor="auto-retry")
        return len(due)


async def expire_due() -> int:
    """Apply the configured password-age acknowledgement gate idempotently."""
    days = get_settings().file.password_policy.expires_after_days
    if days is None:
        return 0
    cutoff = _now() - timedelta(days=days)
    with get_session() as db:
        users = db.query(ManagedUser).filter(
            ManagedUser.role != "root", ManagedUser.status == "active",
            ManagedUser.password_decision_required.is_(False),
            ManagedUser.password_changed_at.is_not(None),
            ManagedUser.password_changed_at <= cutoff,
        ).all()
        ids = []
        for user in users:
            user.password_decision_required = True
            for state in user.sync_states:
                if state.assigned and not state.retired:
                    state.state = "pending_expiry_disable"
                    state.next_retry_at = None
            record_audit(db, "system", "password.expired", user.username,
                         "targets disabled pending password acknowledgement")
            ids.append(user.id)
        db.commit()
    for user_id in ids:
        await sync_user(user_id, actor="password-expiry")
    return len(ids)


async def retry_worker() -> None:
    while True:
        await asyncio.sleep(get_settings().retry_scan_seconds)
        await expire_due()
        await retry_due()
