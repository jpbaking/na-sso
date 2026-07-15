import asyncio
from datetime import datetime, timedelta, timezone

from na_sso.config import AuditPolicy, get_settings
from na_sso.db import get_session
from na_sso.models import AuditEvent


def enforce_audit_retention(
    db, policy: AuditPolicy, *, now: datetime | None = None
) -> int:
    """Remove only expired audit events; correlated operations remain intact."""
    if policy.retention_days is None:
        return 0
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=policy.retention_days)
    removed = db.query(AuditEvent).filter(AuditEvent.at < cutoff).delete(
        synchronize_session=False
    )
    db.commit()
    return removed


async def audit_retention_worker() -> None:
    while True:
        with get_session() as db:
            enforce_audit_retention(db, get_settings().file.audit_policy)
        await asyncio.sleep(24 * 60 * 60)
