"""core/repos/idempotency_repo.py — idempotency dedup for financial POSTs (#7).

Pattern:
  1. INSERT (tg_id, key, result_json=NULL) ON CONFLICT (tg_id, key) DO NOTHING
  2. rowcount==1  → new slot → run fn() → UPDATE result_json → return result
  3. rowcount==0  → conflict → SELECT result_json
     - not None   → return cached result with _replay=True
     - None       → concurrent first request still processing → poll 5×50ms → replay or fallback fn()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.repos.idempotency_table import idempotency_keys

logger = logging.getLogger("core.idempotency_repo")


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


class IdempotencyRepo:

    def _try_reserve_sync(self, tg_id: int, key: str) -> bool:
        """INSERT ON CONFLICT DO NOTHING. Returns True if new slot reserved."""
        ins = pg_insert(idempotency_keys).values(
            tg_id=tg_id, key=key, result_json=None,
        ).on_conflict_do_nothing(constraint="uq_idempotency_tg_key")
        with _get_engine().begin() as conn:
            result = conn.execute(ins)
            return result.rowcount == 1

    def _fetch_result_sync(self, tg_id: int, key: str) -> Optional[Dict[str, Any]]:
        q = (
            select(idempotency_keys.c.result_json)
            .where(
                (idempotency_keys.c.tg_id == tg_id)
                & (idempotency_keys.c.key == key)
            )
        )
        with _get_engine().connect() as conn:
            row = conn.execute(q).fetchone()
        return row[0] if row is not None else None

    def _store_result_sync(self, tg_id: int, key: str, result: Dict[str, Any]) -> None:
        upd = (
            idempotency_keys.update()
            .where(
                (idempotency_keys.c.tg_id == tg_id)
                & (idempotency_keys.c.key == key)
            )
            .values(result_json=result)
        )
        with _get_engine().begin() as conn:
            conn.execute(upd)

    def _cleanup_expired_sync(self) -> int:
        with _get_engine().begin() as conn:
            result = conn.execute(
                text("DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'")
            )
            return result.rowcount

    async def try_reserve(self, tg_id: int, key: str) -> bool:
        return await asyncio.to_thread(self._try_reserve_sync, tg_id, key)

    async def fetch_result(self, tg_id: int, key: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_result_sync, tg_id, key)

    async def store_result(self, tg_id: int, key: str, result: Dict[str, Any]) -> None:
        await asyncio.to_thread(self._store_result_sync, tg_id, key, result)

    async def cleanup_expired(self) -> int:
        return await asyncio.to_thread(self._cleanup_expired_sync)


_idem_repo = IdempotencyRepo()


async def idempotent(
    tg_id: int,
    idem_key: Optional[str],
    fn: Callable[[], Any],
) -> Dict[str, Any]:
    """Run fn() with idempotency guard.

    - No key  → run fn() without dedup (old client path).
    - New key → reserve slot, run fn(), persist result.
    - Replay  → return cached result with _replay=True.
    """
    if not idem_key:
        logger.info("idempotent: no Idempotency-Key header, running without dedup")
        return await fn()

    reserved = await _idem_repo.try_reserve(tg_id, idem_key)
    if not reserved:
        existing = await _idem_repo.fetch_result(tg_id, idem_key)
        if existing is not None:
            logger.info("idempotent: replay tg_id=%s key=%.8s...", tg_id, idem_key)
            return {**existing, "_replay": True}
        # Race: first request still processing (result_json still NULL).
        # Poll briefly — winner should store result within ms.
        _POLL_ATTEMPTS = 5
        _POLL_DELAY = 0.05
        for attempt in range(_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_DELAY)
            existing = await _idem_repo.fetch_result(tg_id, idem_key)
            if existing is not None:
                logger.info(
                    "idempotent: race resolved on attempt %d key=%.8s...",
                    attempt + 1, idem_key,
                )
                return {**existing, "_replay": True}
        logger.warning(
            "idempotent: null result_json after %d polls for key=%.8s (winner crashed?), fallback fn()",
            _POLL_ATTEMPTS, idem_key,
        )
        return await fn()

    result = await fn()
    await _idem_repo.store_result(tg_id, idem_key, result)
    return result
