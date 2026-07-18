import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.models import TargetCredential, utcnow
from na_sso.security import decrypt_secret, encrypt_secret


@dataclass(frozen=True)
class TargetReadiness:
    target_id: str
    configured: bool
    verified: bool
    auth_mode: str | None
    detail: str
    revision: int | None
    updated_at: datetime | None
    last_checked_at: datetime | None
    last_success_at: datetime | None
    reachable: bool | None
    failure_kind: str
    probe_attempt_count: int
    next_probe_at: datetime | None


def target_definitions():
    return [target for target in get_settings().file.targets if target.enabled]


def readiness_map() -> dict[str, TargetReadiness]:
    with get_session() as db:
        rows = {row.target_id: row for row in db.query(TargetCredential).all()}
    result = {}
    for target in target_definitions():
        row = rows.get(target.id)
        result[target.id] = TargetReadiness(
            target.id,
            row is not None,
            bool(row and row.verified_at),
            row.auth_mode if row else None,
            row.probe_detail if row else "Credentials required",
            row.revision if row else None,
            row.updated_at if row else None,
            row.last_checked_at if row else None,
            row.last_success_at if row else None,
            row.last_probe_ok if row else None,
            row.probe_failure_kind if row else "",
            row.probe_attempt_count if row else 0,
            row.next_probe_at if row else None,
        )
    return result


def classify_probe_failure(detail: str) -> str:
    lowered = detail.lower()
    if any(marker in lowered for marker in (
        "401", "403", "auth", "unauthor", "forbidden", "permission denied",
    )):
        return "authentication"
    if any(marker in lowered for marker in (
        "unreachable", "connection", "connect", "timeout", "timed out",
        "name or service", "network",
    )):
        return "unreachable"
    return "verification"


def sanitise_probe_detail(detail: str) -> str:
    safe = re.sub(r"://[^/@\s]+:[^/@\s]+@", "://[redacted]@", detail)
    safe = re.sub(
        r"(?i)\b(password|secret|token|api[_ -]?key)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[redacted]",
        safe,
    )
    return safe[:1000]


def save_credentials(target_id: str, auth_mode: str, payload: dict[str, str]) -> None:
    target = next((item for item in target_definitions() if item.id == target_id), None)
    if target is None:
        raise ValueError("unknown target ID")
    cleaned = {key: value for key, value in payload.items() if value}
    ssh_required = {
        "password": {"management_user", "management_password"},
        "private_key": {"management_user", "management_private_key"},
        "password_and_private_key": {
            "management_user", "management_password", "management_private_key"
        },
    }
    required = ({"api_key", "api_secret"} if target.type == "opnsense" else
                {"admin_user", "admin_password"} if target.type in {"nexus", "nextcloud"} else
                {"api_token"} if target.type in {"gitlab", "gitea", "immich"} else
                {"admin_user", "api_token"} if target.type == "jenkins" else
                ssh_required.get(auth_mode, set()))
    allowed_modes = (set(ssh_required) if target.type == "ssh" else
                     {"token"} if target.type in {"gitlab", "gitea", "immich", "jenkins"} else
                     {"password"})
    if auth_mode not in allowed_modes or not required <= cleaned.keys():
        raise ValueError("management credentials are required")
    encrypted = encrypt_secret(json.dumps(cleaned, separators=(",", ":")))
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id=target_id).one_or_none()
        if row is None:
            row = TargetCredential(target_id=target_id, encrypted_payload=encrypted,
                                   auth_mode=auth_mode)
            db.add(row)
        else:
            row.encrypted_payload = encrypted
            row.auth_mode = auth_mode
            row.revision += 1
        row.verified_at = None
        row.probe_detail = "Credentials saved; verification pending"
        row.last_checked_at = None
        row.last_success_at = None
        row.last_probe_ok = None
        row.probe_failure_kind = ""
        row.probe_attempt_count = 0
        row.next_probe_at = None
        db.commit()


def credential_payload(target_id: str, *, verified_only: bool = True) -> dict[str, str] | None:
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id=target_id).one_or_none()
        if row is None or (verified_only and row.verified_at is None):
            return None
        return json.loads(decrypt_secret(row.encrypted_payload))


def record_probe(target_id: str, ok: bool, detail: str) -> None:
    now = utcnow()
    safe_detail = sanitise_probe_detail(detail)
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id=target_id).one()
        row.last_checked_at = now
        row.last_probe_ok = ok
        row.probe_detail = safe_detail
        if ok:
            row.verified_at = row.verified_at or now
            row.last_success_at = now
            row.probe_failure_kind = ""
            row.probe_attempt_count = 0
            row.next_probe_at = None
        else:
            row.probe_failure_kind = classify_probe_failure(safe_detail)
            if row.probe_failure_kind == "authentication":
                row.verified_at = None
            row.probe_attempt_count += 1
            delay = min(
                get_settings().retry_base_seconds
                * (2 ** min(row.probe_attempt_count - 1, 30)),
                get_settings().retry_max_seconds,
            )
            row.next_probe_at = now + timedelta(seconds=delay)
        db.commit()


async def retry_due_target_probes() -> int:
    now = utcnow()
    with get_session() as db:
        target_ids = [
            row.target_id
            for row in db.query(TargetCredential).filter(
                TargetCredential.next_probe_at.is_not(None),
                TargetCredential.next_probe_at <= now,
            ).all()
        ]
    if not target_ids:
        return 0
    from na_sso.connectors.base import build_unverified_connector
    from na_sso.connectors.base import SyncResult
    for target_id in target_ids:
        try:
            result = await build_unverified_connector(target_id).probe()
        except ValueError as error:
            result = SyncResult(False, str(error))
        record_probe(target_id, result.ok, result.detail)
    return len(target_ids)
