import json
from dataclasses import dataclass

from oneauth.config import get_settings
from oneauth.db import get_session
from oneauth.models import TargetCredential, utcnow
from oneauth.security import decrypt_secret, encrypt_secret


@dataclass(frozen=True)
class TargetReadiness:
    target_id: str
    configured: bool
    verified: bool
    auth_mode: str | None
    detail: str


def target_definitions():
    return [target for target in get_settings().file.targets if target.enabled]


def readiness_map() -> dict[str, TargetReadiness]:
    with get_session() as db:
        rows = {row.target_id: row for row in db.query(TargetCredential).all()}
    return {target.id: TargetReadiness(target.id, target.id in rows,
            bool(rows.get(target.id) and rows[target.id].verified_at),
            rows[target.id].auth_mode if target.id in rows else None,
            rows[target.id].probe_detail if target.id in rows else "Credentials required")
            for target in target_definitions()}


def save_credentials(target_id: str, auth_mode: str, payload: dict[str, str]) -> None:
    target = next((item for item in target_definitions() if item.id == target_id), None)
    if target is None:
        raise ValueError("unknown target ID")
    cleaned = {key: value for key, value in payload.items() if value}
    required = ({"api_key", "api_secret"} if target.type == "opnsense" else
                {"admin_user", "admin_password"} if target.type in {"nexus", "nextcloud"} else
                {"management_user", "management_password" if auth_mode == "password" else "management_private_key"})
    if auth_mode not in ({"password", "private_key"} if target.type == "ssh" else {"password"}) or not required <= cleaned.keys():
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
        db.commit()


def credential_payload(target_id: str, *, verified_only: bool = True) -> dict[str, str] | None:
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id=target_id).one_or_none()
        if row is None or (verified_only and row.verified_at is None):
            return None
        return json.loads(decrypt_secret(row.encrypted_payload))


def record_probe(target_id: str, ok: bool, detail: str) -> None:
    with get_session() as db:
        row = db.query(TargetCredential).filter_by(target_id=target_id).one()
        row.verified_at = utcnow() if ok else None
        row.probe_detail = detail[:1000]
        db.commit()
