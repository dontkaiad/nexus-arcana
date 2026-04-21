"""Тесты всех regex-фильтров из core/classifier.py.

Unit тесты — без моков, только regex matching.
"""
import pytest
from core.classifier import (
    _WEEKDAY_DEADLINE_RE,
    _EDIT_RE,
    _RENAME_RE,
    _EDIT_NOTE_RE,
    _CANCEL_RE,
    _DONE_RE,
    _ZAPOMNI_RE,
    _MEMORY_SAVE_RE,
    _BUDGET_RE,
    _DEBT_CMD_RE,
    _GOAL_CMD_RE,
    _LIMIT_OVERRIDE_RE,
    _DEACTIVATE_RE,
    _NOTE_DELETE_RE,
    _MEMORY_DELETE_RE,
    _BUY_TASK_RE,
    _CURRENCY_RE,
    _MEMORY_SEARCH_RE,
    _TZ_RE,
    _TASK_KEYWORDS_RE,
    _STATS_RE,
)


class TestWeekdayDeadline:
    def test_matches_full_weekday(self):
        assert _WEEKDAY_DEADLINE_RE.search("сделать до понедельника")

    def test_matches_short(self):
        assert _WEEKDAY_DEADLINE_RE.search("до пн важно")

    def test_no_match_without_do(self):
        assert not _WEEKDAY_DEADLINE_RE.search("понедельник будет")


class TestEdit:
    def test_change_category(self):
        assert _EDIT_RE.search("поменяй категорию на продукты")

    def test_update_priority(self):
        assert _EDIT_RE.search("обнови приоритет у задачи")

    def test_no_match_plain(self):
        assert not _EDIT_RE.search("купить молоко")


class TestRename:
    def test_rename(self):
        assert _RENAME_RE.search("переименуй задачу в новое название")

    def test_no_v(self):
        assert not _RENAME_RE.search("переименуй задачу просто")


class TestEditNote:
    def test_change_tag(self):
        assert _EDIT_NOTE_RE.search("измени тег заметки")

    def test_update_tags(self):
        assert _EDIT_NOTE_RE.search("обнови теги последней заметки")


class TestCancel:
    def test_cancel_task(self):
        assert _CANCEL_RE.search("отмени задачу тест")

    def test_delete_task(self):
        assert _CANCEL_RE.search("удали задачу")

    def test_no_match_note(self):
        assert not _CANCEL_RE.search("отмени заметку")


class TestDone:
    def test_sdelala(self):
        assert _DONE_RE.search("сделала тест")

    def test_done_ready(self):
        assert _DONE_RE.search("готово")

    def test_called(self):
        assert _DONE_RE.search("позвонила маме")


class TestZapomni:
    def test_matches(self):
        assert _ZAPOMNI_RE.match("запомни что я не пью молоко")

    def test_case_insensitive(self):
        assert _ZAPOMNI_RE.match("ЗАПОМНИ это")

    def test_not_middle(self):
        assert not _ZAPOMNI_RE.match("ты запомни что")


class TestMemorySave:
    def test_zapomni(self):
        assert _MEMORY_SAVE_RE.match("запомни факт")

    def test_save_in_memory(self):
        assert _MEMORY_SAVE_RE.match("сохрани в памяти")

    def test_limit(self):
        assert _MEMORY_SAVE_RE.match("лимит на кафе 5000")

    def test_goal(self):
        assert _MEMORY_SAVE_RE.match("цель айфон")

    def test_debt(self):
        assert _MEMORY_SAVE_RE.match("долг маме 10000")


class TestBudget:
    def test_show_budget(self):
        assert _BUDGET_RE.match("покажи бюджет")

    def test_can_spend(self):
        assert _BUDGET_RE.match("сколько могу тратить")

    def test_my_budget(self):
        assert _BUDGET_RE.match("мой бюджет")


class TestDebtCmd:
    def test_close_debt(self):
        assert _DEBT_CMD_RE.search("закрыла долг маме")

    def test_new_debt(self):
        assert _DEBT_CMD_RE.search("новый долг 5000")

    def test_pogasila(self):
        assert _DEBT_CMD_RE.search("погасила кредит")


class TestGoalCmd:
    def test_new_goal(self):
        assert _GOAL_CMD_RE.search("новая цель айфон")

    def test_remove_goal(self):
        assert _GOAL_CMD_RE.search("убери цель поездка")


class TestLimitOverride:
    def test_limit_cafe(self):
        m = _LIMIT_OVERRIDE_RE.search("лимит на кафе 5000")
        assert m
        assert m.group(1) == "кафе"
        assert m.group(2) == "5000"

    def test_limit_habit_short(self):
        m = _LIMIT_OVERRIDE_RE.search("лимит привычки 15к")
        assert m
        assert m.group(1) == "привычки"


class TestDeactivate:
    def test_neaktualno(self):
        assert _DEACTIVATE_RE.match("неактуально")

    def test_neaktualno_with_hint(self):
        assert _DEACTIVATE_RE.match("неактуально маша")


class TestNoteDelete:
    def test_delete_note_about(self):
        m = _NOTE_DELETE_RE.match("удали заметку про расходники")
        assert m

    def test_delete_all(self):
        m = _NOTE_DELETE_RE.match("удали все заметки")
        assert m
        assert m.group(1) is not None  # "все" захвачено


class TestMemoryDelete:
    def test_forget(self):
        assert _MEMORY_DELETE_RE.match("забудь про это")

    def test_delete_from_memory(self):
        assert _MEMORY_DELETE_RE.match("удали из памяти факт")


class TestBuyTask:
    def test_kupit(self):
        assert _BUY_TASK_RE.match("купить молоко")

    def test_kupi(self):
        assert _BUY_TASK_RE.match("купи хлеб")


class TestCurrency:
    def test_rub(self):
        assert _CURRENCY_RE.search("500 руб")

    def test_symbol(self):
        assert _CURRENCY_RE.search("1000₽")

    def test_r(self):
        assert _CURRENCY_RE.search("300 р")


class TestMemorySearch:
    def test_chto_pomnish(self):
        assert _MEMORY_SEARCH_RE.search("что ты помнишь обо мне")

    def test_rasskazhi(self):
        assert _MEMORY_SEARCH_RE.search("расскажи про кота")

    def test_show_memory(self):
        assert _MEMORY_SEARCH_RE.search("покажи память")


class TestTZ:
    def test_utc(self):
        assert _TZ_RE.search("мой часовой пояс UTC+5")

    def test_zhivu(self):
        assert _TZ_RE.search("живу в Москве")

    def test_pereehala(self):
        assert _TZ_RE.search("переехала в Питер")


class TestTaskKeywords:
    def test_napomni(self):
        assert _TASK_KEYWORDS_RE.search("напомни купить молоко")

    def test_sdelai(self):
        assert _TASK_KEYWORDS_RE.search("сделать отчёт")

    def test_kupi(self):
        assert _TASK_KEYWORDS_RE.search("купи хлеб")


class TestStats:
    def test_skolko_potratila(self):
        assert _STATS_RE.search("сколько потратила за месяц")

    def test_rashody_za(self):
        assert _STATS_RE.search("расходы за март")
