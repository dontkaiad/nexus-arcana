"""tests/test_work_deadline.py — после рефакторинга work-flow в preview-режим.

Старая UX-инициатива «Когда сделать?» (deadline_keyboard / cb_work_deadline /
_compute_deadline) и кнопка reminder_keyboard на «Работа создана» удалены.
Дедлайн парсится из текста и через уточнения, как в Nexus tasks.
"""
from arcana.handlers.works import PARSE_WORK_SYSTEM


def test_legacy_keyboards_removed():
    """В work_kb.py не должно остаться UX-инициативы старого flow."""
    import arcana.handlers.work_kb as wk
    assert not hasattr(wk, "deadline_keyboard")
    assert not hasattr(wk, "_compute_deadline")
    assert not hasattr(wk, "cb_work_deadline")
    assert not hasattr(wk, "reminder_keyboard")
    assert not hasattr(wk, "cb_work_remind")


def test_parse_prompt_lists_relative_date_rules():
    """Парсер должен явно описывать «завтра / в пятницу / через N дней»."""
    s = PARSE_WORK_SYSTEM
    assert "завтра" in s
    assert "в пятницу" in s or "пятниц" in s
    assert "через N" in s or "через" in s
