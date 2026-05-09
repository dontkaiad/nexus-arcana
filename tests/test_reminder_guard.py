"""Issue #33: гвард против галлюцинированного reminder.

Если в исходном тексте задачи нет слова «напомни/напоминалку/...» —
reminder/reminder_time от Haiku должны быть отброшены.
"""
import pytest


class TestHasRemindWord:
    """`_has_remind_word` — детектор слова-триггера напоминания."""

    def _f(self):
        from nexus.handlers.tasks import _has_remind_word
        return _has_remind_word

    def test_empty_text(self):
        assert self._f()("") is False

    def test_no_remind_words(self):
        f = self._f()
        # Бесплатный кейс из issue #33
        assert f("поставь задачу через неделю выполнить команду git push") is False
        # Простые задачи
        assert f("купить корм завтра") is False
        assert f("сдать отчёт до пятницы") is False
        # Дедлайн с временем — не reminder
        assert f("позвонить маме в 18:00") is False

    def test_remind_words_caught(self):
        f = self._f()
        assert f("напомни в 15") is True
        assert f("напомнить завтра купить корм") is True
        assert f("поставь напоминалку на пятницу") is True
        assert f("напоминание в 18:00") is True
        assert f("remind me at 15") is True

    def test_unrelated_words_do_not_match(self):
        """Слова без триггеров напоминания не должны срабатывать."""
        f = self._f()
        assert f("номинал монеты") is False
        assert f("понять задачу") is False
        # Косвенные формы вроде «напоминаний» в _REMIND_WORDS отсутствуют —
        # это ОК: гвард в худшем случае выкинет лишнее напоминание,
        # а не сохранит галлюцинированное.
        assert f("задача не имеет напоминаний — просто сделай") is False


class TestReminderGuardBehavior:
    """Поведение гварда в handle_task_parsed: если has_remind=False,
    data['reminder'] и data['reminder_time'] должны быть выброшены."""

    def test_guard_drops_hallucinated_reminder(self):
        from nexus.handlers.tasks import _has_remind_word

        # Эмулируем логику гварда из handle_task_parsed:1100-1116
        original_text = "поставь задачу через неделю выполнить команду git push"
        data = {
            "title": "выполнить команду git push",
            "deadline": "2026-05-16",
            "reminder": "2026-05-09T15:00",  # ← галлюцинация Haiku
        }

        has_remind = _has_remind_word(original_text)
        if not has_remind:
            data.pop("reminder", None)
            data.pop("reminder_time", None)

        assert has_remind is False
        assert "reminder" not in data
        assert "reminder_time" not in data
        assert data["deadline"] == "2026-05-16"  # дедлайн не тронут

    def test_guard_keeps_reminder_when_word_present(self):
        from nexus.handlers.tasks import _has_remind_word

        original_text = "напомни завтра в 15 купить корм"
        data = {
            "title": "купить корм",
            "deadline": None,
            "reminder": "2026-05-10T15:00",
        }

        has_remind = _has_remind_word(original_text)
        if not has_remind:
            data.pop("reminder", None)
            data.pop("reminder_time", None)

        assert has_remind is True
        assert data["reminder"] == "2026-05-10T15:00"
