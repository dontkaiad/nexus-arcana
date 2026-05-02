"""tests/test_work_deadline.py — reminder_keyboard + парсер дедлайна.

После рефакторинга в Arcana убрана UX-инициатива «Когда сделать?»
(deadline_keyboard / cb_work_deadline / _compute_deadline). Дедлайн
парсится из текста как в Nexus; остался только reminder_keyboard.
"""
from arcana.handlers.work_kb import reminder_keyboard
from arcana.handlers.works import PARSE_WORK_SYSTEM


def test_reminder_keyboard_3_options():
    kb = reminder_keyboard("aaaa-bbbb")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    cbs = [b.callback_data for b in flat]
    assert any("24h" in c for c in cbs)
    assert any("3h" in c for c in cbs)
    assert any("none" in c for c in cbs)


def test_deadline_keyboard_removed():
    """deadline_keyboard и _compute_deadline убраны — UX-инициатива
    'Когда сделать?' больше не актуальна."""
    import arcana.handlers.work_kb as wk
    assert not hasattr(wk, "deadline_keyboard")
    assert not hasattr(wk, "_compute_deadline")
    assert not hasattr(wk, "cb_work_deadline")


def test_parse_prompt_lists_relative_date_rules():
    """Парсер должен явно описывать «завтра / в пятницу / через N дней»."""
    s = PARSE_WORK_SYSTEM
    assert "завтра" in s
    assert "в пятницу" in s or "пятниц" in s
    assert "через N" in s or "через" in s
