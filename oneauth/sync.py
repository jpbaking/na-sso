import asyncio
from datetime import datetime, timedelta, timezone

from oneauth.audit import record_audit
from oneauth.config import get_settings
from oneauth.connectors import get_connectors
from oneauth.db import get_session
from oneauth.models import ManagedUser, SyncState
from oneauth.security import decrypt_secret

_scan_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def sync_user(user_id: int, action: str | None = None, target: str | None = None, actor: str = "system") -> None:
    connectors = [c for c in get_connectors() if target is None or c.name == target]
    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user:
            return
        operation = action or user.desired_action
        password = decrypt_secret(user.pending_secret) if user.pending_secret else None
        states = {state.target: state for state in user.sync_states}
        for connector in connectors:
            state = states.get(connector.name)
            if state is None:
                state = SyncState(user=user, target=connector.name)
                db.add(state)
                states[connector.name] = state
            state.state, state.detail, state.next_retry_at = "pending", "", None
            db.commit()
            if operation == "delete":
                result = await connector.delete_user(user)
            elif user.status == "disabled":
                result = await connector.disable_user(user)
            else:
                result = await connector.ensure_user(user, password)
            state.state = "ok" if result.ok else "failed"
            state.detail = result.detail
            if result.ok:
                state.attempt_count, state.next_retry_at = 0, None
            else:
                state.attempt_count += 1
                settings = get_settings()
                delay = min(settings.retry_base_seconds * (2 ** (state.attempt_count - 1)), settings.retry_max_seconds)
                state.next_retry_at = _now() + timedelta(seconds=delay)
            record_audit(db, actor, f"sync.{operation}", user.username, f"{connector.name}: {state.state} — {result.detail}")
            db.commit()
        enabled = {c.name for c in get_connectors()}
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
            due = [(s.user_id, s.target) for s in db.query(SyncState).filter(SyncState.state == "failed", SyncState.next_retry_at <= now).all()]
        for user_id, target in due:
            await sync_user(user_id, target=target, actor="auto-retry")
        return len(due)


async def retry_worker() -> None:
    while True:
        await asyncio.sleep(get_settings().retry_scan_seconds)
        await retry_due()
