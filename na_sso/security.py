import base64
import hashlib
import secrets
import string
from dataclasses import dataclass

import bcrypt
from cryptography.fernet import Fernet

from na_sso.config import get_settings


@dataclass(frozen=True)
class PasswordValidation:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_password(
    plain: str,
    *,
    username: str = "",
    email: str = "",
    display_name: str = "",
    old_password: str | None = None,
    history_hashes: tuple[str, ...] = (),
) -> PasswordValidation:
    policy = get_settings().file.password_policy
    errors: list[str] = []
    if len(plain) < policy.min_length:
        errors.append(f"Password must contain at least {policy.min_length} characters.")
    if len(plain) > policy.max_length:
        errors.append(f"Password must contain at most {policy.max_length} characters.")
    checks = (
        (policy.require_lowercase, any(c.islower() for c in plain), "a lowercase letter"),
        (policy.require_uppercase, any(c.isupper() for c in plain), "an uppercase letter"),
        (policy.require_digit, any(c.isdigit() for c in plain), "a digit"),
        (policy.require_symbol, any(not c.isalnum() for c in plain), "a symbol"),
    )
    for enabled, passed, label in checks:
        if enabled and not passed:
            errors.append(f"Password must contain {label}.")
    if policy.max_repeated_characters is not None:
        limit = policy.max_repeated_characters
        if any(char * (limit + 1) in plain for char in set(plain)):
            errors.append(f"Password cannot repeat one character more than {limit} times.")
    if policy.max_numeric_sequence is not None:
        limit = policy.max_numeric_sequence
        digits = "0123456789"
        lowered = plain.lower()
        for size in range(limit + 1, 11):
            if any(digits[i:i + size] in lowered or digits[i:i + size][::-1] in lowered for i in range(11 - size)):
                errors.append(f"Password cannot contain a numeric sequence longer than {limit} digits.")
                break
    if policy.reject_identity_terms:
        identity_terms = {username.lower(), email.partition("@")[0].lower()}
        identity_terms.update(part.lower() for part in display_name.split())
        if any(len(term) >= 3 and term in plain.lower() for term in identity_terms):
            errors.append("Password cannot contain username, email, or display-name terms.")
    if old_password and policy.min_identity_distance:
        distance = sum(a != b for a, b in zip(plain, old_password)) + abs(len(plain) - len(old_password))
        if distance < policy.min_identity_distance:
            errors.append(f"Password must differ from the previous password by at least {policy.min_identity_distance} edits.")
    if any(verify_password(plain, item) for item in history_hashes):
        errors.append("Password was used recently.")
    return PasswordValidation(tuple(errors))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def generate_password(length: int | None = None) -> str:
    policy = get_settings().file.password_policy
    length = max(length or 20, policy.min_length)
    if length > policy.max_length:
        raise ValueError("generated password length exceeds configured maximum")
    required = []
    if policy.require_lowercase:
        required.append(secrets.choice(string.ascii_lowercase))
    if policy.require_uppercase:
        required.append(secrets.choice(string.ascii_uppercase))
    if policy.require_digit:
        required.append(secrets.choice(string.digits))
    symbols = "-_.!@#%+"
    if policy.require_symbol:
        required.append(secrets.choice(symbols))
    alphabet = string.ascii_letters + string.digits + symbols
    required.extend(secrets.choice(alphabet) for _ in range(length - len(required)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def public_key_from_private(private_key_pem: str) -> str:
    """Derive an OpenSSH public key without returning or retaining private material."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()


def ssh_public_key_fingerprint(public_key: str | None) -> str | None:
    """Return the OpenSSH SHA256 fingerprint without exposing key material."""
    if not public_key:
        return None
    try:
        _algorithm, encoded, *_comment = public_key.split()
        digest = hashlib.sha256(base64.b64decode(encoded, validate=True)).digest()
    except (ValueError, TypeError):
        return None
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def generate_ssh_keypair() -> tuple[str, str]:
    """Return a one-time private key and its persistable public counterpart."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                           serialization.PublicFormat.OpenSSH).decode()
    return private, public


def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
