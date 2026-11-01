"""core/llm_usage.py — журнал вызовов LLM: токены (вход / запись в кеш / чтение из кеша / выход), латентность, модель.

Одна строка на вызов `ask_claude` / `ask_claude_vision` в `llm_call_log` (без текстов промптов и ответов — только числа).
Нужен, чтобы (а) видеть реальные объёмы и $ по ролям, (б) проверять, что prompt caching действительно срабатывает
(`cache_read_input_tokens` > 0), (в) сравнивать латентность до/после смены модели. Запись фоновая и никогда не мешает
ответу: любой сбой — warning. `LLM_USAGE_LOG=0` выключает (тесты). Сводка: scripts/llm_usage_stats.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional, Set

import sqlalchemy as sa

logger = logging.getLogger("core.llm_usage")

_tasks: Set[asyncio.Task] = set()      # держим ссылки, иначе GC снимет незавершённую фоновую запись


def enabled() -> bool:
    return os.environ.get("LLM_USAGE_LOG", "1").strip().lower() not in ("0", "false", "off", "no")


def caller_label(depth: int = 2) -> str:
    """«модуль.функция» вызывающего ask_claude — метка роли без правки 87 мест вызова."""
    try:
        f = sys._getframe(depth)
        return f"{f.f_globals.get('__name__', '?')}.{f.f_code.co_name}"
    except Exception:
        return "unknown"


def usage_numbers(resp: Any) -> Dict[str, int]:
    u = getattr(resp, "usage", None)
    g = lambda k: int(getattr(u, k, 0) or 0)
    return {
        "input_tokens": g("input_tokens"),
        "output_tokens": g("output_tokens"),
        "cache_creation_input_tokens": g("cache_creation_input_tokens"),
        "cache_read_input_tokens": g("cache_read_input_tokens"),
    }


def _write(row: Dict[str, Any]) -> None:
    try:
        from core.db import get_engine
        with get_engine().begin() as c:
            c.execute(sa.text(
                "INSERT INTO llm_call_log (label, model, in_tokens, cache_write_tokens, cache_read_tokens, out_tokens, "
                "latency_ms, ok, cache_ttl, thinking) VALUES (:label, :model, :i, :cw, :cr, :o, :l, :ok, :ttl, :th)"),
                row)
    except Exception as e:
        logger.warning("llm_call_log write failed: %s", e)


def record(label: str, model: str, usage: Dict[str, int], latency_ms: int, ok: bool,
           cache_ttl: Optional[str] = None, thinking: Optional[str] = None) -> None:
    """Fire-and-forget: запись идёт в потоке, вызывающий её не ждёт."""
    if not enabled():
        return
    row = {"label": label, "model": model, "i": usage.get("input_tokens", 0),
           "cw": usage.get("cache_creation_input_tokens", 0), "cr": usage.get("cache_read_input_tokens", 0),
           "o": usage.get("output_tokens", 0), "l": int(latency_ms), "ok": bool(ok), "ttl": cache_ttl, "th": thinking}
    try:
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(_write, row))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:                     # нет работающего loop — пишем синхронно
        _write(row)
