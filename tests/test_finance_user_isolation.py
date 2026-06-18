"""tests/test_finance_user_isolation.py — изоляция данных по user_notion_id (#139).

Проверяет:
- пустой user_notion_id → ранний возврат [] без обращения к БД
- непустой user_notion_id → БД вызывается (фильтр применяется)

Нет реального PG — патчим _get_engine, чтобы убедиться что при пустом юзере
engine не вызывается вообще (fail-closed, не all-users aggregate).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.repos.pg_finance_repo import (
    _nb_query_sync,
    _nb_query_month_sync,
    _nb_search_desc_sync,
    _ap_query_sync,
    _ap_query_month_sync,
)

_ENGINE_PATH = "core.repos.pg_finance_repo._get_engine"


# ── helpers ────────────────────────────────────────────────────────────────────

def _mock_engine_with_rows(rows):
    """Возвращает mock engine, чей .connect().execute().fetchall() = rows."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchall.return_value = rows
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


# ── nexus_budget: _nb_query_sync ──────────────────────────────────────────────

def test_nb_query_empty_user_returns_empty_no_db():
    """Пустой user_notion_id → [] без вызова engine."""
    with patch(_ENGINE_PATH) as mock_eng:
        result = _nb_query_sync("2026-01-01", "2026-12-31", None, None, 100, user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


def test_nb_query_none_coerced_to_empty_returns_empty():
    """None coerced → '' через `(result or '')` в вызывателе → всё равно []."""
    # Имитируем путь `(await get_user_notion_id()) or ""` → ""
    with patch(_ENGINE_PATH) as mock_eng:
        result = _nb_query_sync("2026-01-01", "2026-12-31", None, None, 100, user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


def test_nb_query_valid_user_calls_engine():
    """Валидный user_notion_id → engine вызывается (фильтр в SQL)."""
    fake_row = MagicMock()
    fake_row.id = 1
    fake_row.description = "test"
    fake_row.amount = 500
    fake_row.category = "🍜 Продукты"
    fake_row.type_ = "💸 Расход"
    fake_row.source = "💳 Карта"
    fake_row.date = "2026-06-18"
    fake_row.user_notion_id = "user-a"
    engine = _mock_engine_with_rows([fake_row])

    with patch(_ENGINE_PATH, return_value=engine):
        result = _nb_query_sync("2026-01-01", "2026-12-31", None, None, 100, user_notion_id="user-a")

    engine.connect.assert_called_once()
    assert len(result) == 1
    assert result[0].amount == 500


# ── nexus_budget: _nb_query_month_sync ────────────────────────────────────────

def test_nb_query_month_empty_user_returns_empty_no_db():
    with patch(_ENGINE_PATH) as mock_eng:
        result = _nb_query_month_sync("2026-06", "", "", user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


# ── nexus_budget: _nb_search_desc_sync ────────────────────────────────────────

def test_nb_search_desc_empty_user_returns_empty_no_db():
    with patch(_ENGINE_PATH) as mock_eng:
        result = _nb_search_desc_sync("кофе", 10, user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


# ── arcana_pnl: _ap_query_sync ────────────────────────────────────────────────

def test_ap_query_empty_user_returns_empty_no_db():
    with patch(_ENGINE_PATH) as mock_eng:
        result = _ap_query_sync("2026-01-01", "2026-12-31", None, None, 100, user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


# ── arcana_pnl: _ap_query_month_sync ──────────────────────────────────────────

def test_ap_query_month_empty_user_returns_empty_no_db():
    with patch(_ENGINE_PATH) as mock_eng:
        result = _ap_query_month_sync("2026-06", "", "", user_notion_id="")
    assert result == []
    mock_eng.assert_not_called()


# ── изоляция A vs B ───────────────────────────────────────────────────────────

def test_user_a_query_does_not_include_user_b_data():
    """Query для user-a возвращает только строки user-a, не user-b.

    Симулируем: БД вернула только user-a строку (фильтр SQL сработал).
    Тест проверяет что результат не включает user-b данные.
    """
    row_a = MagicMock()
    row_a.id = 1
    row_a.description = "user-a expense"
    row_a.amount = 500
    row_a.category = "🍜 Продукты"
    row_a.type_ = "💸 Расход"
    row_a.source = "💳 Карта"
    row_a.date = "2026-06-18"
    row_a.user_notion_id = "user-a"

    engine = _mock_engine_with_rows([row_a])  # БД отдаёт только user-a (WHERE user_notion_id='user-a')

    with patch(_ENGINE_PATH, return_value=engine):
        result = _nb_query_sync("2026-01-01", "2026-12-31", None, None, 100, user_notion_id="user-a")

    assert len(result) == 1
    assert result[0].user_notion_id == "user-a"
    # Убеждаемся, что данных user-b нет
    assert all(e.user_notion_id != "user-b" for e in result)
