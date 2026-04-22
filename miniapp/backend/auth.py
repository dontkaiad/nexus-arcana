"""miniapp/backend/auth.py — Telegram WebApp initData validation."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from core.config import config

logger = logging.getLogger("miniapp.auth")

INIT_DATA_TTL = 24 * 3600  # 24 hours


def verify_init_data(init_data: str) -> int:
    """Validate Telegram WebApp initData, return tg_id. Raise ValueError on failure.

    Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        raise ValueError("empty init_data")

    # parse_qsl preserves Telegram's URL-encoded form exactly
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("no hash in init_data")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))
    secret_key = hmac.new(b"WebAppData", config.nexus.tg_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("hash mismatch")

    auth_date_raw = data.get("auth_date", "0")
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        raise ValueError("bad auth_date")
    if time.time() - auth_date > INIT_DATA_TTL:
        raise ValueError("init_data expired")

    user_raw = data.get("user")
    if not user_raw:
        raise ValueError("no user in init_data")
    try:
        user = json.loads(user_raw)
        tg_id = int(user["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"bad user json: {e}")

    return tg_id


async def current_user_id(
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> int:
    """FastAPI dependency: extract and validate tg_id from X-Telegram-Init-Data header."""
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="missing X-Telegram-Init-Data")
    try:
        return verify_init_data(x_telegram_init_data)
    except ValueError as e:
        logger.warning("init_data validation failed: %s", e)
        raise HTTPException(status_code=401, detail=f"invalid init_data: {e}")
