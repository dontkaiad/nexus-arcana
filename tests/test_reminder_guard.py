"""Issue #33: гвард против галлюцинированного reminder.
Issue #89: «напоминание на 3 июля 13 часов» — формат «N часов» должен
распознаваться как явное время, а не срезаться гвардом.

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


class TestExtractExplicitClock:
    """`_extract_explicit_clock` — достаёт явное время HH:MM из текста."""

    def _f(self):
        from nexus.handlers.tasks import _extract_explicit_clock
        return _extract_explicit_clock

    def test_hours_word(self):
        """issue #89: формат «N часов»."""
        f = self._f()
        assert f("напоминание на 3 июля 13 часов") == "13:00"
        assert f("напомни 3 июля в 13 часов") == "13:00"
        assert f("в 9 часов") == "09:00"
        assert f("в 2 часа") == "02:00"

    @pytest.mark.parametrize("text,expected", [
        ("в 18:30", "18:30"),
        ("позвонить маме в 18:00", "18:00"),
        ("напомни в 13", "13:00"),
    ])
    def test_colon_and_bare_hour(self, text, expected):
        assert self._f()(text) == expected

    @pytest.mark.parametrize("text", [
        "на 3 июля",          # дата без времени
        "в 3 июля",           # «в <число> <месяц>» — дата, не время
        "напоминание 13.07",  # точка — неоднозначно, доверяем Haiku
        "через 2 часа",       # relative — считается в _parse_relative_time
        "в 99",               # невалидный час
        "купить корм завтра",
    ])
    def test_no_clock(self, text):
        assert self._f()(text) is None


class TestApplyUserTime:
    """`_apply_user_time` — сверка времени напоминания с текстом пользователя."""

    def _f(self):
        from nexus.handlers.tasks import _apply_user_time
        return _apply_user_time

    def test_issue_89_haiku_parsed_time(self):
        """Haiku вернул T13:00 и юзер писал «13 часов» — время остаётся."""
        text = "задача вернуть деньги напоминание на 3 июля 13 часов"
        assert self._f()("2026-07-03T13:00", text) == "2026-07-03T13:00"

    def test_issue_89_haiku_returned_date_only(self):
        """Haiku вернул дату без времени, но «13 часов» есть в тексте — достаём."""
        text = "задача вернуть деньги напоминание на 3 июля 13 часов"
        assert self._f()("2026-07-03", text) == "2026-07-03T13:00"

    def test_issue_33_hallucinated_time_stripped(self):
        """Claude добавил T09:00, юзер время не писал → срезаем до даты."""
        assert self._f()("2026-07-03T09:00", "напомни 3 июля вернуть деньги") == "2026-07-03"

    def test_daypart_keeps_haiku_time(self):
        """«утром» — час знает только Haiku, его время не трогаем."""
        assert self._f()("2026-07-03T09:00", "напомни 3 июля утром") == "2026-07-03T09:00"

    def test_date_only_no_time_in_text(self):
        """Времени нет ни у Haiku, ни в тексте — дата остаётся, время переспросим."""
        assert self._f()("2026-07-03", "напомни 3 июля") == "2026-07-03"
