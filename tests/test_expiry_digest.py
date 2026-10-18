"""tests/test_expiry_digest.py — ежемесячный итог по срокам годности инвентаря Nexus."""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from core import list_manager
from core.repos.pg_nexus_lists_repo import ListItem

TODAY = date(2026, 11, 1)


def _it(name, expires, qty=None):
    return ListItem(id=name, name=name, list_type="инвентарь", status="not_started",
                    quantity=qty, expires_at=expires)


def test_empty_when_nothing_expiring():
    items = [_it("Далеко", "2027-06-01"), _it("Без срока", ""), _it("Мусор", "abc")]
    assert list_manager.build_expiry_digest(items, TODAY) == ""


def test_expired_and_soon_sections():
    items = [
        _it("Старый сироп", "2026-09-01", qty=2),
        _it("Скоро йогурт", "2026-11-10"),
        _it("Далеко", "2027-06-01"),
    ]
    text = list_manager.build_expiry_digest(items, TODAY)
    assert "Пора выкинуть" in text and "Старый сироп × 2" in text and "с 2026-09-01" in text
    assert "Скоро испортится" in text and "Скоро йогурт" in text and "через 9 дн." in text
    assert "Далеко" not in text


def test_today_is_not_expired_and_boundary_30_days():
    items = [_it("Сегодня", "2026-11-01"), _it("Ровно30", "2026-12-01"), _it("Лишний", "2026-12-02")]
    text = list_manager.build_expiry_digest(items, TODAY)
    assert "Пора выкинуть" not in text
    assert "Сегодня" in text and "Ровно30" in text and "Лишний" not in text


def test_sorted_by_date_and_html_escaped():
    items = [_it("B<b>", "2026-08-01"), _it("A", "2026-07-01")]
    text = list_manager.build_expiry_digest(items, TODAY)
    assert text.index("A") < text.index("B&lt;b&gt;")


def test_monthly_digest_sends_to_all_allowed_ids():
    bot = MagicMock(); bot.send_message = AsyncMock()
    items = [_it("Старый сироп", "2000-01-01")]
    with patch.object(list_manager._nexus_repo, "get_expiry_due", AsyncMock(return_value=items)), \
         patch.object(list_manager.config, "allowed_ids", [1, 2]):
        sent = asyncio.run(list_manager.monthly_expiry_digest(bot))
    assert sent == 2 and bot.send_message.await_count == 2
    assert "Старый сироп" in bot.send_message.await_args.args[1]


def test_monthly_digest_silent_when_nothing():
    bot = MagicMock(); bot.send_message = AsyncMock()
    with patch.object(list_manager._nexus_repo, "get_expiry_due", AsyncMock(return_value=[])), \
         patch.object(list_manager.config, "allowed_ids", [1]):
        assert asyncio.run(list_manager.monthly_expiry_digest(bot)) == 0
    bot.send_message.assert_not_awaited()


def test_monthly_digest_survives_send_error():
    bot = MagicMock(); bot.send_message = AsyncMock(side_effect=[RuntimeError("blocked"), None])
    items = [_it("Старый сироп", "2000-01-01")]
    with patch.object(list_manager._nexus_repo, "get_expiry_due", AsyncMock(return_value=items)), \
         patch.object(list_manager.config, "allowed_ids", [1, 2]):
        assert asyncio.run(list_manager.monthly_expiry_digest(bot)) == 1
