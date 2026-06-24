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


class TestVoiceTranscriptReachesGuard:
    """Регресс: при диктовке голосовым `message.text` пуст — транскрипт Whisper
    приходит аргументом `original_text`. Раньше handle_task_parsed брал текст
    только из message.text, поэтому has_remind=False и легитимное напоминание
    срезалось гвардом #33 («Напоминание: нет» на скрине Кай)."""

    def _voice_msg(self):
        from unittest.mock import AsyncMock, MagicMock
        m = MagicMock()
        m.from_user.id = 42
        m.chat.id = 1
        m.text = ""  # голосовое: текста нет, транскрипт идёт отдельным аргументом
        m.answer = AsyncMock()
        return m

    @pytest.mark.asyncio
    async def test_voice_reminder_survives_via_original_text(self):
        """Два времени в транскрипте: deadline 15:30 + reminder 14:00 — оба доезжают."""
        from unittest.mock import AsyncMock, patch
        from nexus.handlers import tasks

        msg = self._voice_msg()
        data = {
            "title": "встреча с Мишей",
            "deadline": "2026-07-27T15:30",
            "reminder": "2026-07-27T14:00",  # отдельное напоминание от Haiku
            "repeat": "Нет",
        }
        with patch.object(tasks, "_do_save_task", AsyncMock()) as save:
            await tasks.handle_task_parsed(
                msg, data,
                original_text=(
                    "двадцать седьмого числа в пятнадцать тридцать встреча с мишей "
                    "напомни мне двадцать седьмого числа в четырнадцать часов"
                ),
            )
        save.assert_awaited_once()
        saved = save.await_args.args[1]
        assert saved.get("reminder_time") == "2026-07-27T14:00"
        assert saved.get("deadline") == "2026-07-27T15:30"

    @pytest.mark.asyncio
    async def test_voice_guard_still_drops_hallucination(self):
        """Без слова «напомни» в транскрипте гвард #33 по-прежнему срезает reminder."""
        from unittest.mock import AsyncMock, patch
        from nexus.handlers import tasks

        msg = self._voice_msg()
        data = {
            "title": "купить корм",
            "deadline": "2026-07-28",
            "reminder": "2026-07-28T09:00",  # галлюцинация Haiku
            "repeat": "Нет",
        }
        with patch.object(tasks, "_do_save_task", AsyncMock()) as save, \
             patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "_pending_set", lambda *a, **k: None):
            await tasks.handle_task_parsed(
                msg, data, original_text="купить корм коту завтра",
            )
        save.assert_not_awaited()  # reminder выброшен → не explicit-save
        sent = msg.answer.await_args.args[0]
        assert "Напоминание: нет" in sent


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
