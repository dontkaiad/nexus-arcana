"""tests/test_classifier_user_tz.py

Дата транзакции из classifier.process_item (обычный ввод «продукты 500») —
по личному tz пользователя, не серверному МСК.
"""
from __future__ import annotations

from datetime import datetime as _real_dt, timezone as _tzc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import classifier as clf


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


def test_today_moscow_tz_param(monkeypatch):
    # сервер +3 → 30 июня, юзер +5 → 1 июля
    monkeypatch.setattr(clf, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))
    assert clf.today_moscow(3) == "2026-06-30"
    assert clf.today_moscow(5) == "2026-07-01"
    assert clf.today_moscow() == "2026-06-30"  # регресс: дефолт == 3


@pytest.mark.asyncio
async def test_process_item_expense_date_by_user_tz(monkeypatch):
    monkeypatch.setattr(clf, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))

    fake_add = AsyncMock(return_value="page-1")
    data = {"type": "expense", "amount": 500, "category": "🍜 Продукты",
            "source": "💳 Карта", "title": "продукты", "confidence": "high"}
    msg = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()

    async def fake_tz(uid):
        return 5 if uid == 42 else 3

    with patch.object(clf._fin_repo, "add", fake_add), \
         patch("nexus.handlers.tasks._get_user_tz", side_effect=fake_tz), \
         patch("nexus.handlers.finance._check_budget_limit", AsyncMock()), \
         patch("core.classifier.log_error", AsyncMock(return_value="e")):
        await clf.process_item(data=data, original_text="продукты 500р",
                               msg=msg, clarify={}, user_notion_id="u-1")

    assert fake_add.call_args.kwargs["date"] == "2026-07-01"
