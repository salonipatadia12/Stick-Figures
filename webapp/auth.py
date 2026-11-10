"""The single login (scrypt hash), the signed session cookie, and the Mac worker's bearer token."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2 ** 14, 8, 1, 32
COOKIE = 'stick_session'
SESSION_DAYS = 30


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt$<salt hex>$<hash hex> (n=2**14, r=8, p=1, dklen=32)."""
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return f'scrypt${salt.hex()}${key.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, key_hex = stored.split('$')
        if scheme != 'scrypt':
            return False
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False
    got = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=len(want) or SCRYPT_DKLEN)
    return hmac.compare_digest(got, want)


def check_login(settings, user: str, password: str) -> bool:
    if not settings.app_user or not settings.app_password_hash:
        return False
    ok_user = hmac.compare_digest((user or '').encode(), settings.app_user.encode())
    ok_pass = verify_password(password or '', settings.app_password_hash)
    return ok_user and ok_pass


def _signer(settings):
    return TimestampSigner(settings.session_secret, salt='stick-session')


def make_session(settings, user: str) -> str:
    return _signer(settings).sign(user.encode()).decode()


def read_session(settings, value: str | None) -> str | None:
    if not value:
        return None
    try:
        user = _signer(settings).unsign(value, max_age=SESSION_DAYS * 86400).decode()
    except (BadSignature, SignatureExpired, UnicodeDecodeError):
        return None
    return user if settings.app_user and hmac.compare_digest(user.encode(), settings.app_user.encode()) else None


def set_cookie(response, settings, user: str):
    response.set_cookie(COOKIE, make_session(settings, user), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite='lax', secure=settings.secure_cookies, path='/')


def clear_cookie(response, settings):
    response.delete_cookie(COOKIE, path='/', httponly=True, samesite='lax', secure=settings.secure_cookies)


def worker_token_ok(settings, header: str | None) -> bool:
    """Authorization: Bearer <token>, whose sha256 hex equals WORKER_TOKEN_SHA256 (constant-time compare)."""
    want = settings.worker_token_sha256
    if not want or not header or not header.startswith('Bearer '):
        return False
    token = header[len('Bearer '):].strip()
    if not token:
        return False
    got = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(got, want)
