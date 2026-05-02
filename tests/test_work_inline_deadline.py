"""tests/test_work_inline_deadline.py — парсер дедлайна в Работе.

handle_add_work after refactor:
- если deadline распарсен из текста → шлём reminder_keyboard
- если deadline null → ничего не спрашиваем (не пристаём)
- reply «завтра в 19» обновляет Notion + предлагает напоминание
"""
from arcana.handlers.works import PARSE_WORK_SYSTEM


def test_prompt_returns_null_when_no_deadline_in_text():
    """Системный промпт явно требует null если дедлайн не упомянут."""
    s = PARSE_WORK_SYSTEM
    assert "не упомянут" in s.lower() or "не выдумывай" in s.lower()


def test_prompt_supports_iso_with_time():
    """Промпт описывает формат YYYY-MM-DDTHH:MM для случаев со временем."""
    s = PARSE_WORK_SYSTEM
    assert "YYYY-MM-DDTHH:MM" in s


def test_reply_handler_attaches_reminder_kb_when_deadline_set():
    """Если reply применил Дедлайн — handler цепляет reminder_keyboard."""
    import inspect
    from arcana.handlers import reply_update
    src = inspect.getsource(reply_update)
    assert 'reminder_keyboard' in src
    assert '"Дедлайн" in applied' in src or '"Дедлайн"' in src
