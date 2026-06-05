"""tests/test_list_classifier_v1_2_1.py — расширение _LIST_BUY_RE и Haiku
few-shot для формата «добавь в [группа]: товар цена, товар цена».

Контекст: до v1.2.1 regex ловил только «добавь в (покупки|список)», поэтому
сообщения вида «добавь в [своё-имя-подсписка]: A 100к, B 50к» проваливались
к Haiku-классификатору, который маршрутизировал их как массив expense.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch



# ── _LIST_BUY_RE: позитивные кейсы ────────────────────────────────────────────


def test_list_buy_re_simple_buy():
    """Регресс старого кейса."""
    from core.list_classifier import _LIST_BUY_RE
    assert _LIST_BUY_RE.search("купи молоко")


def test_list_buy_re_old_explicit_keyword():
    """Регресс: «добавь в покупки» / «добавь в список»."""
    from core.list_classifier import _LIST_BUY_RE
    assert _LIST_BUY_RE.search("добавь в покупки молоко")
    assert _LIST_BUY_RE.search("добавь в список молоко, яйца")


def test_list_buy_re_custom_group_with_colon():
    """NEW: произвольная группа + двоеточие."""
    from core.list_classifier import _LIST_BUY_RE
    assert _LIST_BUY_RE.search("добавь в косметичка: тушь 800₽, крем 1.5к")
    assert _LIST_BUY_RE.search("добавь в гардероб: пальто 15к в Lamoda")
    assert _LIST_BUY_RE.search("добавь в подарки: книга 800р маме")


def test_list_buy_re_latin_group_name():
    """Группа с латиницей и дефисом."""
    from core.list_classifier import _LIST_BUY_RE
    assert _LIST_BUY_RE.search("добавь в моя-группа: товар1 100к, товар2")


def test_list_buy_re_added_dobav_tovar():
    """«добавь товар X» — короткая форма."""
    from core.list_classifier import _LIST_BUY_RE
    assert _LIST_BUY_RE.search("добавь товар молоко")


# ── _LIST_BUY_RE: anti-overlap (системные группы НЕ должны цепляться) ─────────


def test_list_buy_re_negative_tasks():
    """«добавь в задачи …» — это task, НЕ list_buy."""
    from core.list_classifier import _LIST_BUY_RE
    assert not _LIST_BUY_RE.search("добавь в задачи позвонить нотариусу")
    assert not _LIST_BUY_RE.search("добавь в задачу: подготовить отчёт")


def test_list_buy_re_negative_notes():
    """«добавь в заметки …» — это note."""
    from core.list_classifier import _LIST_BUY_RE
    assert not _LIST_BUY_RE.search("добавь в заметки идея про подкаст")
    assert not _LIST_BUY_RE.search("добавь в заметку: рецепт супа")


def test_list_buy_re_negative_memory():
    """«добавь в память …» — это memory_save."""
    from core.list_classifier import _LIST_BUY_RE
    assert not _LIST_BUY_RE.search("добавь в память что Маша любит чай")


def test_list_buy_re_negative_calendar_finance_budget_inventory():
    """Системные группы — все не должны триггерить list_buy."""
    from core.list_classifier import _LIST_BUY_RE
    assert not _LIST_BUY_RE.search("добавь в календарь: встреча")
    assert not _LIST_BUY_RE.search("добавь в финансы 5000 еда")
    assert not _LIST_BUY_RE.search("добавь в бюджет: лимит 3000")
    # Инвентарь имеет свой regex (_LIST_INV_ADD_RE), мы не должны конфликтовать
    assert not _LIST_BUY_RE.search("добавь в инвентарь свечи")


def test_list_buy_re_negative_unrelated():
    """Совсем не про списки — не цепляется."""
    from core.list_classifier import _LIST_BUY_RE
    assert not _LIST_BUY_RE.search("позвонила маме")
    assert not _LIST_BUY_RE.search("сегодня устала")
    assert not _LIST_BUY_RE.search("потратила 500₽ на кафе")


# ── Few-shot в LIST_HAIKU_TYPES ───────────────────────────────────────────────


def test_haiku_types_mention_grouped_list_buy():
    """v1.2.1: системный промпт должен содержать пример с группой и ценами,
    иначе Haiku продолжит маршрутизировать в expense."""
    from core.list_classifier import LIST_HAIKU_TYPES
    joined = "\n".join(LIST_HAIKU_TYPES)
    assert "добавь в косметичка" in joined or "добавь в гардероб" in joined
    # Должно быть явное правило
    assert "ВСЕГДА list_buy" in joined or "всегда list_buy" in joined.lower()


def test_haiku_types_distinguishes_list_buy_from_expense():
    """Промпт должен явно противопоставлять list_buy и expense."""
    from core.list_classifier import LIST_HAIKU_TYPES
    joined = "\n".join(LIST_HAIKU_TYPES).lower()
    assert "expense" in joined
    assert "потратила" in joined  # пример expense


# ── Полный classify pipeline (проверка что pre-filter ловит до Haiku) ────────


def test_classify_grouped_list_does_not_call_haiku():
    """«добавь в [группа]: …» должно матчиться regex'ом ДО вызова Haiku.

    Если ask_claude вообще не дёргается — pre-filter отработал.
    """
    from core import classifier as clf

    text = "добавь в косметичка: тушь 800₽, крем 1.5к"
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value="{}")) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    assert out and out[0]["type"] == "list_buy"
    assert out[0]["text"] == text
    mock_haiku.assert_not_awaited(), "pre-filter должен сработать без Haiku"


def test_classify_task_with_dobav_v_zadachi_routes_to_haiku():
    """«добавь в задачи …» НЕ должно матчиться list_buy regex'ом —
    отдадим Haiku пусть классифицирует как task."""
    from core import classifier as clf

    text = "добавь в задачи позвонить нотариусу"
    fake_haiku_resp = (
        '{"type":"task","title":"позвонить нотариусу",'
        '"category":"💳 Прочее","priority":"Важно","deadline":null,'
        '"reminder":null,"repeat":"Нет","repeat_time":null,'
        '"day_of_week":null,"confidence":"high"}'
    )
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value=fake_haiku_resp)) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    # Haiku должен быть вызван (pre-filter не сработал на «добавь в задачи»)
    mock_haiku.assert_awaited_once()
    assert out and out[0]["type"] == "task"


def test_classify_kupila_routes_to_list_done_not_buy():
    """«купила X 89р» → list_done (старый pre-filter, не должен сломаться)."""
    from core import classifier as clf

    text = "купила молоко 89р"
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value="{}")) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    assert out and out[0]["type"] == "list_done"
    mock_haiku.assert_not_awaited()


def test_classify_simple_kupi_still_works():
    """Регресс: «купи молоко» → list_buy без Haiku."""
    from core import classifier as clf

    text = "купи молоко"
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value="{}")) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    assert out and out[0]["type"] == "list_buy"
    mock_haiku.assert_not_awaited()


# ── issue #80: продолжение списка «<товары> ещё» / «ещё <товары>» ─────────────


def test_buy_continuation_positive():
    """Маркер «ещё» в начале/конце короткого сообщения → продолжение покупок."""
    from core.list_classifier import looks_like_buy_continuation
    assert looks_like_buy_continuation("монстры ещё")
    assert looks_like_buy_continuation("монстры еще")  # без ё
    assert looks_like_buy_continuation("ещё кофе")
    assert looks_like_buy_continuation("ещё кофе и чай")
    assert looks_like_buy_continuation("и ещё молоко")


def test_buy_continuation_negative_guards():
    """Guard'ы: вопросы, суммы, глаголы задач, системные группы → НЕ покупки."""
    from core.list_classifier import looks_like_buy_continuation
    assert not looks_like_buy_continuation("ещё раз")
    assert not looks_like_buy_continuation("напомни ещё раз")
    assert not looks_like_buy_continuation("что ещё")
    assert not looks_like_buy_continuation("ещё 300 на кофе")     # сумма
    assert not looks_like_buy_continuation("ещё 2 монстра")       # цифра
    assert not looks_like_buy_continuation("добавь в задачи ещё позвонить")
    assert not looks_like_buy_continuation("сделай ещё одну заметку")
    assert not looks_like_buy_continuation("кофе")               # нет маркера «ещё»
    # Слишком длинное — не короткое продолжение
    assert not looks_like_buy_continuation(
        "ещё купи молоко яйца хлеб масло сыр колбасу"
    )


def test_classify_buy_continuation_routes_to_list_buy():
    """«монстры ещё» → list_buy через pre-filter, без Haiku."""
    from core import classifier as clf

    text = "монстры ещё"
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value="{}")) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    assert out and out[0]["type"] == "list_buy"
    assert out[0]["text"] == text
    mock_haiku.assert_not_awaited()


def test_classify_esche_raz_not_list_buy():
    """«ещё раз» не должно уходить в list_buy (улетает в Haiku)."""
    from core import classifier as clf

    text = "ещё раз"
    fake = '{"type":"unknown"}'
    with patch.object(clf, "ask_claude",
                       AsyncMock(return_value=fake)) as mock_haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))

    assert out and out[0]["type"] != "list_buy"
    mock_haiku.assert_awaited_once()
