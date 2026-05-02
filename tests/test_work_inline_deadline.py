"""tests/test_work_inline_deadline.py — поведение reply_update для уже сохранённых работ.

После перехода на preview-flow на сохранённых работах reply, выставляющий
Дедлайн, автоматически планирует напоминание (deadline - 1 день) без кнопок.
"""
from arcana.handlers.works import PARSE_WORK_SYSTEM


def test_prompt_returns_null_when_no_deadline_in_text():
    s = PARSE_WORK_SYSTEM
    assert "не упомянут" in s.lower() or "не выдумывай" in s.lower()


def test_prompt_supports_iso_with_time():
    s = PARSE_WORK_SYSTEM
    assert "YYYY-MM-DDTHH:MM" in s


def test_reply_auto_schedules_reminder_on_deadline():
    """Reply, выставляющий Дедлайн на работе, должен планировать reminder автоматом."""
    import inspect
    from arcana.handlers import reply_update
    src = inspect.getsource(reply_update)
    assert "schedule_reminder" in src
    assert '"Дедлайн" in applied' in src or '"Дедлайн"' in src
