"""Security primitives: AES-256-GCM, scrypt KDF, Argon2id, TOTP, hashing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ph = PasswordHasher()  # argon2id by default

BACKUP_MAGIC = b"KNRB1"
BACKUP_SALT_LEN = 16
BACKUP_NONCE_LEN = 12
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


# ------------------------------------------------------------- passwords ----
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ------------------------------------------------------------- field enc ----
def _field_key(encryption_key: str) -> bytes:
    return hashlib.sha256(("kanar-field:" + encryption_key).encode()).digest()


def encrypt_field(plaintext: str, encryption_key: str) -> str:
    if not encryption_key:
        raise ValueError("ENCRYPTION_KEY is not configured")
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    ct = AESGCM(_field_key(encryption_key)).encrypt(nonce, plaintext.encode(), b"kanar")
    return nonce.hex() + "$" + ct.hex()


def decrypt_field(token: str, encryption_key: str) -> str:
    nonce_hex, ct_hex = token.split("$", 1)
    pt = AESGCM(_field_key(encryption_key)).decrypt(
        bytes.fromhex(nonce_hex), bytes.fromhex(ct_hex), b"kanar")
    return pt.decode()


# ----------------------------------------------------------- backup enc ----
def derive_backup_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt,
                          n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)


def encrypt_backup(plaintext: bytes, password: str) -> tuple[bytes, str]:
    """Returns (ciphertext_blob, sha256_of_blob). Format: MAGIC|salt|nonce|ct."""
    salt = secrets.token_bytes(BACKUP_SALT_LEN)
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    key = derive_backup_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    blob = BACKUP_MAGIC + salt + nonce + ct
    return blob, hashlib.sha256(blob).hexdigest()


def decrypt_backup(blob: bytes, password: str) -> bytes:
    if len(blob) < BACKUP_SALT_LEN + BACKUP_NONCE_LEN + 16 or blob[:5] != BACKUP_MAGIC:
        raise ValueError("BAD_FORMAT")
    salt = blob[5:5 + BACKUP_SALT_LEN]
    nonce = blob[5 + BACKUP_SALT_LEN:5 + BACKUP_SALT_LEN + BACKUP_NONCE_LEN]
    ct = blob[5 + BACKUP_SALT_LEN + BACKUP_NONCE_LEN:]
    key = derive_backup_key(password, salt)
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception:
        raise ValueError("BAD_PASSWORD_OR_TAMPERED")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------- TOTP ----
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    s = secret.upper().replace(" ", "")
    pad = (8 - len(s) % 8) % 8
    return base64.b32decode(s + "=" * pad)


def totp_code(secret: str, timestamp: Optional[float] = None) -> str:
    counter = int((timestamp if timestamp is not None else time.time()) // 30)
    msg = counter.to_bytes(8, "big")
    digest = hmac.new(_b32decode(secret), msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = time.time()
    for drift in range(-window, window + 1):
        if hmac.compare_digest(totp_code(secret, now + drift * 30), code):
            return True
    return False


def otpauth_uri(secret: str, username: str, issuer: str = "Kanar Super Admin") -> str:
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}"
            f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


def new_recovery_codes(count: int = 8) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(("kanar-rc:" + code.strip().upper()).encode()).hexdigest()


def mask_token(token: str) -> str:
    if not token:
        return "—"
    return token[:6] + "…" + token[-4:] if len(token) > 12 else "…"


def random_username() -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz"
    return "kanar-" + "".join(secrets.choice(alphabet) for _ in range(6))


def random_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%"
    pw = "".join(secrets.choice(alphabet) for _ in range(20))
    return pw


def random_backup_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(32))
    return "-".join(raw[i:i + 8] for i in range(0, 32, 8))
