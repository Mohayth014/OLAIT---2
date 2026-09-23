"""
Authentication & role-based authorization for the two-portal Legal Metrology
console: 'inspector' (scans packages, records their own decisions) and
'manager' (oversight dashboard, statutory rule config, verdict overrides).

Passwords are hashed with salted PBKDF2-HMAC-SHA256 (stdlib only, no extra
dependency). Sessions are stateless signed JWTs carrying {user_id, role, name}.

NOTE: TRACE_JWT_SECRET defaults to a placeholder for local development only.
Set the environment variable to a long random value before any real deployment.
"""
import hashlib
import os
import time
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException

SECRET = os.environ.get("TRACE_JWT_SECRET", "change-me-in-production-set-TRACE_JWT_SECRET-env-var")
ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 24 * 60 * 60  # 24h session

VALID_ROLES = {"inspector", "manager"}


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Returns 'salt_hex$digest_hex'. Generates a fresh random salt if none given."""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return check == digest


def issue_token(user_id: str, role: str, name: str = "") -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "name": name,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def _decode(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token. Log in via /api/auth/login.")
    token = authorization[len("Bearer "):]
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again.")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """FastAPI dependency: any authenticated user (inspector or manager)."""
    payload = _decode(authorization)
    role = payload.get("role")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="Token has an unrecognized role.")
    return {"user_id": payload["sub"], "role": role, "name": payload.get("name", "")}


def get_current_user_flexible(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = None,
) -> dict:
    """Same as get_current_user, but also accepts the JWT as a `?token=` query
    parameter. Reserved for the report/export routes, which the frontend opens
    via plain `<a href>` / `window.open()` navigation -- there is no way to
    attach a custom Authorization header to that kind of request. Every other
    endpoint uses the strict header-only get_current_user; this is a narrow,
    deliberate trade-off (the token is visible in the URL/browser history for
    these three download links only), not a general auth bypass."""
    bearer = authorization or (f"Bearer {token}" if token else None)
    payload = _decode(bearer)
    role = payload.get("role")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="Token has an unrecognized role.")
    return {"user_id": payload["sub"], "role": role, "name": payload.get("name", "")}


def require_role(*roles: str):
    """FastAPI dependency factory: 403s unless the caller's role is one of `roles`.

    This is the single security boundary for role-gated endpoints -- the frontend's
    hiding of buttons/links per role is UX polish only, never trust it.
    """
    def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"This action requires role: {' or '.join(roles)}.")
        return user
    return dependency
