"""Named SSH key enrolment, rotation, revocation, and expiry."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError

from na_sso.audit import record_audit
from na_sso.auth import current_user
from na_sso.config import get_settings
from na_sso.db import get_session
from na_sso.feedback import redirect_with_feedback
from na_sso.models import LifecycleOperation, ManagedUser, UserSshKey, utcnow
from na_sso.security import ssh_public_key_fingerprint, verify_password

router = APIRouter()


class SshKeyError(ValueError):
    pass


def _normalise_public_key(value: str) -> tuple[str, str, str]:
    public_key = " ".join(value.strip().split()[:2])
    try:
        parsed = serialization.load_ssh_public_key(public_key.encode())
    except (TypeError, ValueError) as error:
        raise SshKeyError("Invalid SSH public key.") from error
    if isinstance(parsed, ed25519.Ed25519PublicKey):
        algorithm = "ed25519"
    elif isinstance(parsed, rsa.RSAPublicKey):
        algorithm = "rsa"
        if parsed.key_size < get_settings().file.ssh_key_policy.rsa_min_bits:
            raise SshKeyError(
                f"RSA keys must be at least {get_settings().file.ssh_key_policy.rsa_min_bits} bits."
            )
    else:
        raise SshKeyError("Unsupported SSH public-key algorithm.")
    if algorithm not in get_settings().file.ssh_key_policy.allowed_algorithms:
        raise SshKeyError(f"{algorithm.upper()} keys are disabled by policy.")
    fingerprint = ssh_public_key_fingerprint(public_key)
    if not fingerprint:
        raise SshKeyError("Invalid SSH public key.")
    return public_key, fingerprint, algorithm


def _expiry(value: str) -> datetime | None:
    policy = get_settings().file.ssh_key_policy
    today = utcnow().date()
    if value:
        try:
            chosen = date.fromisoformat(value)
        except ValueError as error:
            raise SshKeyError("Expiry must be a valid date.") from error
    elif policy.default_expiry_days:
        chosen = today + timedelta(days=policy.default_expiry_days)
    else:
        return None
    if chosen <= today:
        raise SshKeyError("Expiry must be in the future.")
    if policy.max_expiry_days and chosen > today + timedelta(days=policy.max_expiry_days):
        raise SshKeyError(f"Expiry cannot be more than {policy.max_expiry_days} days away.")
    return datetime.combine(chosen, time.max, tzinfo=timezone.utc)


def add_key(
    db,
    user: ManagedUser,
    *,
    name: str,
    public_key: str,
    expires_on: str,
    actor: str,
) -> UserSshKey:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 80:
        raise SshKeyError("Key name must contain 1–80 characters.")
    normalised, fingerprint, algorithm = _normalise_public_key(public_key)
    key = UserSshKey(
        user=user,
        name=clean_name,
        public_key=normalised,
        fingerprint=fingerprint,
        algorithm=algorithm,
        expires_at=_expiry(expires_on),
    )
    db.add(key)
    user.ssh_public_key = normalised
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise SshKeyError("That key is already enrolled.") from error
    record_audit(db, actor, "ssh_key.enrolled", user.username, f"{clean_name}; {fingerprint}; {algorithm}")
    return key


def _refresh_legacy_key(user: ManagedUser) -> None:
    active = sorted(user.active_ssh_keys, key=lambda item: item.enrolled_at, reverse=True)
    user.ssh_public_key = active[0].public_key if active else None


def revoke_key(db, user: ManagedUser, key: UserSshKey, *, actor: str, action: str = "ssh_key.revoked") -> None:
    if key.user_id != user.id or key.revoked_at is not None:
        raise SshKeyError("The SSH key is not active.")
    key.revoked_at = utcnow()
    key.revoked_by = actor
    _refresh_legacy_key(user)
    record_audit(db, actor, action, user.username, f"{key.name}; {key.fingerprint}")


async def _sync_succeeded(operation_id: str | None) -> bool:
    if not operation_id:
        return True
    with get_session() as db:
        operation = db.get(LifecycleOperation, operation_id)
        return bool(operation and operation.status == "succeeded")


@router.post("/account/ssh-key")
async def enroll_ssh_key(
    request: Request,
    public_key: str = Form(""),
    private_key: str = Form(""),
    name: str = Form("Personal key"),
    expires_on: str = Form(""),
    replace_key_id: str = Form(""),
):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["role"] == "root":
        return HTMLResponse("Root is local-only.", status_code=403)
    if private_key:
        from na_sso.security import public_key_from_private
        try:
            public_key = public_key_from_private(private_key)
        except (TypeError, ValueError):
            return HTMLResponse("Invalid private key.", status_code=422)
    try:
        with get_session() as db:
            user = db.get(ManagedUser, account["id"])
            replacement = db.get(UserSshKey, replace_key_id) if replace_key_id else None
            if replacement and (replacement.user_id != user.id or replacement.revoked_at is not None):
                raise SshKeyError("Replacement key is not active.")
            key = add_key(
                db, user, name=name, public_key=public_key, expires_on=expires_on,
                actor=account["username"],
            )
            db.commit()
            key_id = key.id
    except SshKeyError as error:
        return HTMLResponse(str(error), status_code=422)

    from na_sso.sync import sync_user
    operation_id = await sync_user(account["id"], actor=account["username"])
    replaced = False
    if replace_key_id and await _sync_succeeded(operation_id):
        with get_session() as db:
            user = db.get(ManagedUser, account["id"])
            old = db.get(UserSshKey, replace_key_id)
            new = db.get(UserSshKey, key_id)
            if old and new and old.revoked_at is None:
                revoke_key(db, user, old, actor=account["username"], action="ssh_key.replaced")
                old.replaced_by_id = new.id
                db.commit()
                replaced = True
        await sync_user(account["id"], actor=account["username"])
    return redirect_with_feedback(
        "/account",
        title="SSH key enrolled" if not replaced else "SSH key replaced",
        message=(
            "The new key was synchronized before the old key was revoked."
            if replaced else
            "The public key was saved and target synchronization has completed or is queued."
        ),
    )


@router.post("/account/ssh-key/{key_id}/revoke")
async def revoke_ssh_key(request: Request, key_id: str):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    try:
        with get_session() as db:
            user = db.get(ManagedUser, account["id"])
            key = db.get(UserSshKey, key_id)
            if not user or not key:
                raise SshKeyError("SSH key not found.")
            revoke_key(db, user, key, actor=account["username"])
            db.commit()
    except SshKeyError as error:
        return HTMLResponse(str(error), status_code=422)
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    return redirect_with_feedback("/account", title="SSH key revoked", message="The key was removed from assigned targets.")


@router.post("/account/ssh-keys/emergency-revoke")
async def emergency_revoke_all(
    request: Request,
    current_password: str = Form(...),
    confirmation: str = Form(...),
):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if confirmation != "REVOKE ALL KEYS":
        return HTMLResponse("Enter REVOKE ALL KEYS exactly.", status_code=422)
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        if not user or not user.password_hash or not verify_password(current_password, user.password_hash):
            return HTMLResponse("Invalid current password.", status_code=422)
        active = list(user.active_ssh_keys)
        for key in active:
            revoke_key(db, user, key, actor=account["username"], action="ssh_key.emergency_revoked")
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    return redirect_with_feedback(
        "/account", title="All SSH keys revoked",
        message=f"{len(active)} active key(s) were removed from assigned targets.",
    )


async def expire_due_ssh_keys() -> int:
    now = utcnow()
    with get_session() as db:
        keys = db.query(UserSshKey).filter(
            UserSshKey.revoked_at.is_(None), UserSshKey.expires_at.is_not(None),
            UserSshKey.expires_at <= now,
        ).all()
        user_ids: set[int] = set()
        for key in keys:
            user = key.user
            key.revoked_at = now
            key.revoked_by = "system"
            _refresh_legacy_key(user)
            record_audit(db, "system", "ssh_key.expired", user.username, f"{key.name}; {key.fingerprint}")
            user_ids.add(user.id)
        db.commit()
    if keys:
        from na_sso.sync import sync_user
        for user_id in user_ids:
            await sync_user(user_id, actor="ssh-key-expiry")
    return len(keys)
