"""miniapp/backend/tg_auth.py — Telegram Login Widget auth primitives.

Slim copy of heylark_auth/tg_auth.py for Nexus × Arcana Mini App.
Includes only the session/widget parts; psycopg grants are not needed here
(auth check = config.allowed_ids, same as initData path).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# --- AUTHENTICATION: Telegram Login Widget HMAC verification ----------------

DEFAULT_LOGIN_MAX_AGE_SECONDS = 86400
_CLOCK_SKEW_GRACE_SECONDS = 300


def verify_login_widget(
    data: Dict[str, Any],
    bot_token: str,
    *,
    max_age_seconds: int = DEFAULT_LOGIN_MAX_AGE_SECONDS,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Validate Telegram Login Widget payload. Returns user dict or None."""
    if not isinstance(data, dict):
        return None

    received_hash = data.get("hash")
    if not received_hash or not isinstance(received_hash, str):
        return None

    if "auth_date" not in data:
        return None

    pairs = []
    for key in sorted(k for k in data.keys() if k != "hash"):
        value = data[key]
        pairs.append(f"{key}={value}")
    data_check_string = "\n".join(pairs)

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None

    try:
        auth_date = int(data["auth_date"])
    except (TypeError, ValueError):
        return None

    current = int(time.time()) if now is None else int(now)
    if current - auth_date > max_age_seconds or auth_date - current > _CLOCK_SKEW_GRACE_SECONDS:
        return None

    try:
        tg_id = int(data["id"])
    except (KeyError, TypeError, ValueError):
        return None

    result: Dict[str, Any] = {"id": tg_id, "auth_date": auth_date}
    for opt in ("first_name", "last_name", "username", "photo_url"):
        if opt in data:
            result[opt] = data[opt]
    return result


# --- SESSION: stateless signed cookie ---------------------------------------

REMEMBER_MAX_AGE_SECONDS = 2592000  # 30 days
_SESSION_SALT = "heylark.session"
SESSION_COOKIE = "hl_session"


def set_session_cookie(response, token: str, max_age: Optional[int], cookie_domain: str) -> None:
    """Set the SSO session cookie with cross-subdomain attributes."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=max_age,
        domain=cookie_domain,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret, salt=_SESSION_SALT)


def issue_session(tg_id: int, *, secret: str, remember: bool):
    """Mint a signed session token. Returns (token, max_age)."""
    serializer = _serializer(secret)
    payload = {"tg_id": int(tg_id), "remember": bool(remember)}
    token = serializer.dumps(payload)
    max_age = REMEMBER_MAX_AGE_SECONDS if remember else None
    return token, max_age


def read_session(
    token: str,
    *,
    secret: str,
    max_age_seconds: int = REMEMBER_MAX_AGE_SECONDS,
    now: Optional[int] = None,
) -> Optional[int]:
    """Verify session token and return tg_id, or None."""
    if not token or not isinstance(token, str):
        return None

    serializer = _serializer(secret)
    try:
        if now is None:
            payload = serializer.loads(token, max_age=max_age_seconds)
        else:
            payload, issued_at = serializer.loads(
                token, max_age=None, return_timestamp=True
            )
            issued_epoch = int(issued_at.timestamp())
            if int(now) - issued_epoch > max_age_seconds:
                return None
    except (SignatureExpired, BadSignature, Exception):
        return None

    if not isinstance(payload, dict):
        return None
    tg_id = payload.get("tg_id")
    if not isinstance(tg_id, int):
        return None
    return tg_id
