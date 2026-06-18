"""tests/test_miniapp_auth.py — dual-token initData + owner-allowlist 403.

Покрывает:
- initData, подписанная arcana-токеном → проходит verify_init_data
- initData, подписанная nexus-токеном → проходит verify_init_data
- ни один токен не совпал → ValueError
- initData истёкшая (auth_date в далёком прошлом) → ValueError
- current_user_id: tg_id в allowed_ids → 200 OK (no HTTP error)
- current_user_id: tg_id НЕ в allowed_ids → 403
- current_user_id: оба owner-ID проходят (механизм, не конкретные значения)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest

from miniapp.backend.auth import verify_init_data, current_user_id, INIT_DATA_TTL


# ── helpers ───────────────────────────────────────────────────────────────────

NEXUS_TOKEN = "nexus_fake_token_111"
ARCANA_TOKEN = "arcana_fake_token_222"
OWNER_A = 100_001
OWNER_B = 100_002
STRANGER = 999_999


def _make_init_data(bot_token: str, tg_id: int, auth_date: Optional[int] = None) -> str:
    """Сгенерировать валидную initData строку для указанного бота."""
    if auth_date is None:
        auth_date = int(time.time())
    user_json = json.dumps({"id": tg_id, "first_name": "Test"}, separators=(",", ":"))
    pairs = sorted([("auth_date", str(auth_date)), ("user", user_json)])
    data_check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    hash_val = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    all_pairs = list(pairs) + [("hash", hash_val)]
    return urlencode(all_pairs)


# нужен Optional в Python 3.9 без PEP 604
from typing import Optional


# ── verify_init_data ──────────────────────────────────────────────────────────

def test_verify_nexus_token_passes():
    """initData, подписанная nexus-токеном → проходит."""
    init_data = _make_init_data(NEXUS_TOKEN, OWNER_A)
    tg_id = verify_init_data(init_data, extra_tokens=[NEXUS_TOKEN, ARCANA_TOKEN])
    assert tg_id == OWNER_A


def test_verify_arcana_token_passes():
    """initData, подписанная arcana-токеном → проходит (dual-token fix)."""
    init_data = _make_init_data(ARCANA_TOKEN, OWNER_A)
    tg_id = verify_init_data(init_data, extra_tokens=[NEXUS_TOKEN, ARCANA_TOKEN])
    assert tg_id == OWNER_A


def test_verify_wrong_token_fails():
    """initData, подписанная неизвестным токеном → ValueError hash mismatch."""
    init_data = _make_init_data("unknown_token", OWNER_A)
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_init_data(init_data, extra_tokens=[NEXUS_TOKEN, ARCANA_TOKEN])


def test_verify_expired_fails():
    """initData с auth_date старше TTL → ValueError init_data expired."""
    old_ts = int(time.time()) - INIT_DATA_TTL - 60
    init_data = _make_init_data(NEXUS_TOKEN, OWNER_A, auth_date=old_ts)
    with pytest.raises(ValueError, match="expired"):
        verify_init_data(init_data, extra_tokens=[NEXUS_TOKEN, ARCANA_TOKEN])


def test_verify_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        verify_init_data("", extra_tokens=[NEXUS_TOKEN])


def test_verify_no_hash_raises():
    with pytest.raises(ValueError, match="no hash"):
        verify_init_data("auth_date=12345&user=%7B%22id%22%3A1%7D", extra_tokens=[NEXUS_TOKEN])


# ── current_user_id (FastAPI dependency) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_passes():
    """tg_id ∈ allowed_ids → зависимость возвращает tg_id без ошибки."""
    init_data = _make_init_data(NEXUS_TOKEN, OWNER_A)
    with patch("miniapp.backend.auth.verify_init_data", return_value=OWNER_A), \
         patch("miniapp.backend.auth.config") as mock_cfg:
        mock_cfg.allowed_ids = [OWNER_A, OWNER_B]
        result = await current_user_id(x_telegram_init_data=init_data)
    assert result == OWNER_A


@pytest.mark.asyncio
async def test_second_owner_passes():
    """Второй owner-ID тоже проходит."""
    init_data = _make_init_data(NEXUS_TOKEN, OWNER_B)
    with patch("miniapp.backend.auth.verify_init_data", return_value=OWNER_B), \
         patch("miniapp.backend.auth.config") as mock_cfg:
        mock_cfg.allowed_ids = [OWNER_A, OWNER_B]
        result = await current_user_id(x_telegram_init_data=init_data)
    assert result == OWNER_B


@pytest.mark.asyncio
async def test_stranger_gets_403():
    """tg_id НЕ в allowed_ids → HTTPException 403."""
    from fastapi import HTTPException
    init_data = _make_init_data(NEXUS_TOKEN, STRANGER)
    with patch("miniapp.backend.auth.verify_init_data", return_value=STRANGER), \
         patch("miniapp.backend.auth.config") as mock_cfg:
        mock_cfg.allowed_ids = [OWNER_A, OWNER_B]
        with pytest.raises(HTTPException) as exc_info:
            await current_user_id(x_telegram_init_data=init_data)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_header_gets_401():
    """Без заголовка X-Telegram-Init-Data → 401."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await current_user_id(x_telegram_init_data=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_initdata_gets_401():
    """Кривая initData → 401, не 403."""
    from fastapi import HTTPException
    with patch("miniapp.backend.auth.verify_init_data", side_effect=ValueError("hash mismatch")), \
         patch("miniapp.backend.auth.config") as mock_cfg:
        mock_cfg.allowed_ids = [OWNER_A]
        with pytest.raises(HTTPException) as exc_info:
            await current_user_id(x_telegram_init_data="garbage")
    assert exc_info.value.status_code == 401
