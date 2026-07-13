from oneauth.connectors import get_connectors
from oneauth.audit import record_audit
from oneauth.db import get_session
from oneauth.models import ManagedUser, SyncState
from oneauth.security import decrypt_secret


async def sync_user(user_id: int, action: str = "ensure", target: str | None = None) -> None:
    """Propagate one user's current state and persist each connector result."""
    connectors = [
        connector
        for connector in get_connectors()
        if target is None or connector.name == target
    ]
    if not connectors:
        if action == "delete" and target is None:
            with get_session() as db:
                user = db.get(ManagedUser, user_id)
                if user:
                    db.delete(user)
                    db.commit()
        return

    with get_session() as db:
        user = db.get(ManagedUser, user_id)
        if not user:
            return
        password = decrypt_secret(user.pending_secret) if user.pending_secret else None
        states = {state.target: state for state in user.sync_states}

        for connector in connectors:
            state = states.get(connector.name)
            if state is None:
                state = SyncState(user=user, target=connector.name)
                db.add(state)
                states[connector.name] = state
            state.state = "pending"
            state.detail = ""
            db.commit()

            if action == "delete":
                result = await connector.delete_user(user)
            elif user.status == "disabled":
                result = await connector.disable_user(user)
            else:
                result = await connector.ensure_user(user, password)

            state.state = "ok" if result.ok else "failed"
            state.detail = result.detail
            operation = "delete" if action == "delete" else (
                "disable" if user.status == "disabled" else "ensure"
            )
            record_audit(
                db,
                "system",
                f"sync.{operation}",
                user.username,
                f"{connector.name}: {state.state} — {result.detail}",
            )
            db.commit()

        enabled_targets = {connector.name for connector in get_connectors()}
        all_ok = enabled_targets and all(
            states.get(name) is not None and states[name].state == "ok"
            for name in enabled_targets
        )
        if all_ok:
            if action == "delete":
                db.delete(user)
            else:
                user.pending_secret = None
            db.commit()
