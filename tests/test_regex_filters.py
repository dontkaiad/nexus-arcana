"""Тесты всех regex-фильтров из core/classifier.py.

Unit тесты — без моков, только regex matching.

Структура:
- test_regex_matches      — позитивные кейсы: фильтр ОБЯЗАН сработать
- test_regex_no_match     — негативные кейсы: фильтр НЕ должен сработать
- test_limit_override_groups / test_note_delete_vse_captured — уникальная
  логика с проверкой capture-групп.
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
    _TASK_EXPLICIT_RE,
)


@pytest.mark.parametrize("regex,method,text", [
    # _WEEKDAY_DEADLINE_RE — дедлайн «до <день недели>»
    pytest.param(_WEEKDAY_DEADLINE_RE, "search", "сделать до понедельника", id="weekday-full"),
    pytest.param(_WEEKDAY_DEADLINE_RE, "search", "до пн важно", id="weekday-short"),
    # _EDIT_RE — редактирование полей задачи
    pytest.param(_EDIT_RE, "search", "поменяй категорию на продукты", id="edit-change-category"),
    pytest.param(_EDIT_RE, "search", "обнови приоритет у задачи", id="edit-update-priority"),
    # _RENAME_RE — «переименуй … в …»
    pytest.param(_RENAME_RE, "search", "переименуй задачу в новое название", id="rename"),
    # _EDIT_NOTE_RE — правка тегов заметки
    pytest.param(_EDIT_NOTE_RE, "search", "измени тег заметки", id="edit-note-change-tag"),
    pytest.param(_EDIT_NOTE_RE, "search", "обнови теги последней заметки", id="edit-note-update-tags"),
    # _CANCEL_RE — отмена/удаление задачи
    pytest.param(_CANCEL_RE, "search", "отмени задачу тест", id="cancel-task"),
    pytest.param(_CANCEL_RE, "search", "удали задачу", id="cancel-delete-task"),
    # #158: прошедшее время отмены без слова «задача» (отменённое событие)
    pytest.param(_CANCEL_RE, "search", "отменила встречу с врачом сегодня", id="cancel-past-fem"),
    pytest.param(_CANCEL_RE, "search", "отменил звонок клиенту", id="cancel-past-masc"),
    pytest.param(_CANCEL_RE, "search", "отменили поездку", id="cancel-past-plural"),
    # _DONE_RE — задача выполнена
    pytest.param(_DONE_RE, "search", "сделала тест", id="done-sdelala"),
    pytest.param(_DONE_RE, "search", "готово", id="done-ready"),
    pytest.param(_DONE_RE, "search", "позвонила маме", id="done-called"),
    # _ZAPOMNI_RE — «запомни …» в начале строки
    pytest.param(_ZAPOMNI_RE, "match", "запомни что я не пью молоко", id="zapomni"),
    pytest.param(_ZAPOMNI_RE, "match", "ЗАПОМНИ это", id="zapomni-case-insensitive"),
    # _MEMORY_SAVE_RE — сохранение в память (факт/лимит/цель/долг)
    pytest.param(_MEMORY_SAVE_RE, "match", "запомни факт", id="memory-save-zapomni"),
    pytest.param(_MEMORY_SAVE_RE, "match", "сохрани в памяти", id="memory-save-in-memory"),
    pytest.param(_MEMORY_SAVE_RE, "match", "лимит на кафе 5000", id="memory-save-limit"),
    pytest.param(_MEMORY_SAVE_RE, "match", "цель айфон", id="memory-save-goal"),
    pytest.param(_MEMORY_SAVE_RE, "match", "долг маме 10000", id="memory-save-debt"),
    # _BUDGET_RE — запрос бюджета
    pytest.param(_BUDGET_RE, "match", "покажи бюджет", id="budget-show"),
    pytest.param(_BUDGET_RE, "match", "сколько могу тратить", id="budget-can-spend"),
    pytest.param(_BUDGET_RE, "match", "мой бюджет", id="budget-my"),
    # _DEBT_CMD_RE — команды по долгам
    pytest.param(_DEBT_CMD_RE, "search", "закрыла долг маме", id="debt-close"),
    pytest.param(_DEBT_CMD_RE, "search", "новый долг 5000", id="debt-new"),
    pytest.param(_DEBT_CMD_RE, "search", "погасила кредит", id="debt-pogasila"),
    # _GOAL_CMD_RE — команды по целям
    pytest.param(_GOAL_CMD_RE, "search", "новая цель айфон", id="goal-new"),
    pytest.param(_GOAL_CMD_RE, "search", "убери цель поездка", id="goal-remove"),
    # _DEACTIVATE_RE — «неактуально [хинт]»
    pytest.param(_DEACTIVATE_RE, "match", "неактуально", id="deactivate-bare"),
    pytest.param(_DEACTIVATE_RE, "match", "неактуально маша", id="deactivate-with-hint"),
    # _NOTE_DELETE_RE — удаление заметки
    pytest.param(_NOTE_DELETE_RE, "match", "удали заметку про расходники", id="note-delete-about"),
    # _MEMORY_DELETE_RE — удаление из памяти
    pytest.param(_MEMORY_DELETE_RE, "match", "забудь про это", id="memory-delete-forget"),
    pytest.param(_MEMORY_DELETE_RE, "match", "удали из памяти факт", id="memory-delete-from-memory"),
    # _BUY_TASK_RE — «купить/купи …»
    pytest.param(_BUY_TASK_RE, "match", "купить молоко", id="buy-kupit"),
    pytest.param(_BUY_TASK_RE, "match", "купи хлеб", id="buy-kupi"),
    # _CURRENCY_RE — упоминание суммы с валютой
    pytest.param(_CURRENCY_RE, "search", "500 руб", id="currency-rub"),
    pytest.param(_CURRENCY_RE, "search", "1000₽", id="currency-symbol"),
    pytest.param(_CURRENCY_RE, "search", "300 р", id="currency-r"),
    # _MEMORY_SEARCH_RE — поиск по памяти
    pytest.param(_MEMORY_SEARCH_RE, "search", "что ты помнишь обо мне", id="memory-search-pomnish"),
    pytest.param(_MEMORY_SEARCH_RE, "search", "расскажи про кота", id="memory-search-rasskazhi"),
    pytest.param(_MEMORY_SEARCH_RE, "search", "покажи память", id="memory-search-show"),
    # _TZ_RE — часовой пояс / переезд
    pytest.param(_TZ_RE, "search", "мой часовой пояс UTC+5", id="tz-utc"),
    pytest.param(_TZ_RE, "search", "живу в Москве", id="tz-zhivu"),
    pytest.param(_TZ_RE, "search", "переехала в Питер", id="tz-pereehala"),
    # _TASK_KEYWORDS_RE — ключевые слова задач
    pytest.param(_TASK_KEYWORDS_RE, "search", "напомни купить молоко", id="task-kw-napomni"),
    pytest.param(_TASK_KEYWORDS_RE, "search", "сделать отчёт", id="task-kw-sdelat"),
    pytest.param(_TASK_KEYWORDS_RE, "search", "купи хлеб", id="task-kw-kupi"),
    # _STATS_RE — статистика расходов
    pytest.param(_STATS_RE, "search", "сколько потратила за месяц", id="stats-skolko-potratila"),
    pytest.param(_STATS_RE, "search", "расходы за март", id="stats-rashody-za"),
    # _TASK_EXPLICIT_RE — явное добавление задачи (B2-fix)
    pytest.param(_TASK_EXPLICIT_RE, "match", "добавь задачу купить молоко", id="task-explicit-dobav"),
    pytest.param(_TASK_EXPLICIT_RE, "match", "поставь задачу позвонить маме", id="task-explicit-postav"),
    pytest.param(_TASK_EXPLICIT_RE, "match", "создай задачу разобрать почту", id="task-explicit-sozdaj"),
    pytest.param(_TASK_EXPLICIT_RE, "match", "задача: оплатить интернет", id="task-explicit-colon"),
])
def test_regex_matches(regex, method, text):
    """Позитивные кейсы: фильтр обязан сработать на этом тексте."""
    assert getattr(regex, method)(text), f"expected {regex.pattern!r} to {method} {text!r}"


@pytest.mark.parametrize("regex,method,text", [
    pytest.param(_WEEKDAY_DEADLINE_RE, "search", "понедельник будет", id="weekday-without-do"),
    pytest.param(_EDIT_RE, "search", "купить молоко", id="edit-plain-text"),
    pytest.param(_RENAME_RE, "search", "переименуй задачу просто", id="rename-without-v"),
    pytest.param(_CANCEL_RE, "search", "отмени заметку", id="cancel-note-not-task"),
    # #158: глагол выполнения НЕ должен срабатывать как отмена
    pytest.param(_CANCEL_RE, "search", "сделала встречу с врачом", id="cancel-not-done-verb"),
    pytest.param(_ZAPOMNI_RE, "match", "ты запомни что", id="zapomni-not-at-start"),
    # _TASK_EXPLICIT_RE — не должна матчить поиск/просмотр/удаление (B2-fix)
    pytest.param(_TASK_EXPLICIT_RE, "match", "покажи задачи на сегодня", id="task-explicit-no-show"),
    pytest.param(_TASK_EXPLICIT_RE, "match", "задачи на сегодня", id="task-explicit-no-plural"),
    pytest.param(_TASK_EXPLICIT_RE, "match", "удали задачу X", id="task-explicit-no-delete"),
])
def test_regex_no_match(regex, method, text):
    """Негативные кейсы: фильтр НЕ должен срабатывать на этом тексте."""
    assert not getattr(regex, method)(text), f"unexpected {method} of {regex.pattern!r} on {text!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "отменила встречу с врачом сегодня",
    "отменил звонок клиенту",
    "отмени задачу позвонить маме",
])
async def test_classify_cancel_routes_to_task_cancel(text):
    """#158: отмена (в т.ч. прошедшее время) → task_cancel, НЕ task_done.

    _CANCEL_RE срабатывает в classify ДО обращения к LLM, поэтому сети нет."""
    from core.classifier import classify
    res = await classify(text)
    assert res and res[0]["type"] == "task_cancel", res


@pytest.mark.asyncio
async def test_classify_done_still_routes_to_task_done():
    """#158 guard: глагол выполнения по-прежнему task_done."""
    from core.classifier import classify
    res = await classify("сделала тестовая задача")
    assert res and res[0]["type"] == "task_done", res


@pytest.mark.parametrize("text,group1,group2", [
    pytest.param("лимит на кафе 5000", "кафе", "5000", id="limit-cafe-5000"),
    pytest.param("лимит привычки 15к", "привычки", None, id="limit-habit-short"),
])
def test_limit_override_groups(text, group1, group2):
    """_LIMIT_OVERRIDE_RE: capture-группы (категория, сумма)."""
    m = _LIMIT_OVERRIDE_RE.search(text)
    assert m
    assert m.group(1) == group1
    if group2 is not None:
        assert m.group(2) == group2


def test_note_delete_vse_captured():
    """_NOTE_DELETE_RE: «удали все заметки» — слово «все» захвачено группой."""
    m = _NOTE_DELETE_RE.match("удали все заметки")
    assert m
    assert m.group(1) is not None  # "все" захвачено


@pytest.mark.parametrize("text,expected_title", [
    ("добавь задачу купить молоко", "купить молоко"),
    ("поставь задачу позвонить маме", "позвонить маме"),
    ("создай задачу разобрать почту", "разобрать почту"),
    ("задача: оплатить интернет", "оплатить интернет"),
])
def test_task_explicit_title_captured(text, expected_title):
    """_TASK_EXPLICIT_RE: group(1) содержит title задачи без глагола-префикса."""
    m = _TASK_EXPLICIT_RE.match(text)
    assert m, f"expected match on {text!r}"
    assert m.group(1).strip() == expected_title
