"""tests/test_task_edit_context.py — правка последней задачи, детерминированные
даты, вопрос про задачи (баги A–D).

A. Дедлайн «27 ноября» — дата без времени (никакого «03:00»); опечатка в
   месяце НЕ угадывается (только точные формы).
B. Правка «напоминание на 15 часов» берёт ДАТУ из существующей задачи.
C. «напоминание 20 числа в 15» — правка (не новая задача), день месяца ≥
   сегодняшнего → текущий месяц; контекст last_task переживает правку.
D. «стоит ли у меня задача X?» → интент question → поиск по задачам.

Без Notion/LLM: репозиторий и ask_claude замоканы, SQLite — во временном файле,
«сейчас» заморожено. Privacy: нейтральные названия задач.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.date_extract import merge_when, parse_when, strip_when_tokens

TZ = 3
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone(timedelta(hours=TZ)))


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _msg(uid: int, text: str):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.chat = MagicMock()
    m.chat.id = uid
    m.text = text
    sent = MagicMock()
    sent.message_id = 1
    m.answer = AsyncMock(return_value=sent)
    return m


@pytest.fixture
def tasks_mod(tmp_path):
    """tasks с изолированной SQLite и замороженным «сейчас»."""
    from nexus.handlers import tasks

    with patch.object(tasks, "_PENDING_DB", str(tmp_path / "pending_tasks.db")), \
         patch.object(tasks, "datetime", _FrozenDT), \
         patch.object(tasks, "_get_effective_tz", AsyncMock(return_value=(TZ, "UTC+3"))), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks, "save_task_reminder", AsyncMock()), \
         patch.object(tasks, "ask_claude", AsyncMock(side_effect=AssertionError("LLM не должен вызываться"))):
        yield tasks


def _page(reminder: str = "", deadline: str = "", all_day: bool = False, title: str = "купить корм"):
    return SimpleNamespace(
        title=title, reminder=reminder, deadline=deadline,
        deadline_all_day=all_day, duration_min=None,
    )


# ── A: даты без времени, месяцы только точные ────────────────────────────────

def test_A_deadline_date_only_has_no_time():
    when = parse_when("дедлайн 27 ноября", TZ, NOW)
    assert when == {"date": "2026-11-27", "time": None}
    assert merge_when(when) == "2026-11-27"  # весь день, без T03:00


def test_A_typo_month_is_not_guessed():
    when = parse_when("дедлайн 27 ночября", TZ, NOW)
    assert when == {"date": None, "time": None}  # и главное — не 03:00


def test_A_month_date_rolls_to_next_year_when_passed():
    assert parse_when("5 января", TZ, NOW)["date"] == "2027-01-05"
    assert parse_when("13 ноября", TZ, NOW)["date"] == "2026-11-13"


@pytest.mark.asyncio
async def test_A_deadline_edit_writes_date_only(tasks_mod):
    uid = 900_001
    tasks_mod._last_task_set(uid, "t1")
    captured = []
    with patch.object(tasks_mod._repo, "retrieve_page", AsyncMock(return_value=_page())), \
         patch.object(tasks_mod._repo, "set_props", AsyncMock(side_effect=lambda p, pr: captured.append(pr))):
        ok = await tasks_mod.handle_last_task_clarify(_msg(uid, "дедлайн 27 ноября"), "дедлайн 27 ноября", uid)
    assert ok is True
    start = captured[-1]["Дедлайн"]["date"]["start"]
    assert start == "2026-11-27"  # без времени и без tz-суффикса


# ── B: недостающая часть берётся из существующей задачи ──────────────────────

def test_B_merge_time_only_keeps_existing_date():
    when = parse_when("напоминание на 15 часов", TZ, NOW)
    assert when == {"date": None, "time": "15:00"}
    assert merge_when(when, "2026-11-20T10:00", "2026-11-27") == "2026-11-20T15:00"


def test_B_merge_date_only_keeps_existing_time():
    when = parse_when("перенеси на 21 ноября", TZ, NOW)
    assert merge_when(when, "2026-11-20T10:30") == "2026-11-21T10:30"


@pytest.mark.asyncio
async def test_B_clarify_keeps_task_date(tasks_mod):
    uid = 900_002
    tasks_mod._last_task_set(uid, "t2")
    # напоминание 20.11 10:00 МСК = 07:00 UTC
    page = _page(reminder="2026-11-20T07:00:00+00:00", deadline="2026-11-27T00:00:00+00:00", all_day=True)
    captured = []
    text = "напоминание на 15 часов"
    with patch.object(tasks_mod._repo, "retrieve_page", AsyncMock(return_value=page)), \
         patch.object(tasks_mod._repo, "set_props", AsyncMock(side_effect=lambda p, pr: captured.append(pr))):
        ok = await tasks_mod.handle_last_task_clarify(_msg(uid, text), text, uid)
    assert ok is True
    assert captured[-1]["Напоминание"]["date"]["start"].startswith("2026-11-20T15:00")


@pytest.mark.asyncio
async def test_B_reply_update_merges_with_existing_task():
    """«✏️ Дополнено»: LLM не знает задачу и ставит сегодня — дата берётся из задачи."""
    from core.reply_update import merge_task_when_updates

    page = _page(reminder="2026-11-20T07:00:00+00:00")
    llm_updates = {"reminder": "2026-11-13 15:00"}  # то, что выдал бы LLM (сегодня)
    with patch("nexus.repos.tasks_repo._repo.retrieve_page", AsyncMock(return_value=page)), \
         patch("core.reply_update.datetime", create=True):
        out = await merge_task_when_updates("t", llm_updates, "напоминание на 15 часов", TZ)
    assert out["reminder"] == "2026-11-20 15:00"


# ── C: правка, а не новая задача; день месяца; окно живёт между правками ─────

def test_C_day_of_month_rule():
    assert parse_when("20 числа в 15", TZ, NOW) == {"date": "2026-11-20", "time": "15:00"}
    assert parse_when("13 числа", TZ, NOW)["date"] == "2026-11-13"  # ≥ сегодня → этот месяц
    assert parse_when("12 числа", TZ, NOW)["date"] == "2026-12-12"  # прошло → следующий
    late = datetime(2026, 11, 25, 9, 0, tzinfo=timezone(timedelta(hours=TZ)))
    assert parse_when("20 числа", TZ, late)["date"] == "2026-12-20"


@pytest.mark.parametrize("text,is_edit", [
    ("напоминание 20 числа в 15", True),
    ("напоминание на 15 часов", True),
    ("поставь напоминание на завтра в 10", True),      # одно служебное слово
    ("дедлайн 27 ноября", True),
    ("категория Коты", True),
    ("приоритет срочно", True),
    ("доделать отчёт напоминание 20 ноября дедлайн 27 ноября", False),  # есть заголовок
    ("отдать подушки одеяла напомни завтра в 14 дедлайн 13 сентября", False),
    ("создай задачу напоминание завтра в 10", False),  # «создай» — не служебное
    ("купить корм", False),                            # нет ключевых слов
])
def test_C_edit_or_new_rule(tasks_mod, text, is_edit):
    assert tasks_mod._is_clarification_not_new_task(text) is is_edit


@pytest.mark.asyncio
async def test_C_second_edit_still_hits_same_task_and_month_is_current(tasks_mod):
    uid = 900_003
    tasks_mod._last_task_set(uid, "t3")
    captured = []
    with patch.object(tasks_mod._repo, "retrieve_page", AsyncMock(return_value=_page())), \
         patch.object(tasks_mod._repo, "set_props", AsyncMock(side_effect=lambda p, pr: captured.append((p, pr)))):
        assert await tasks_mod.handle_last_task_clarify(_msg(uid, "дедлайн 27 ноября"), "дедлайн 27 ноября", uid)
        # раньше после первой правки last_task удалялся → вторая правка создавала новую задачу
        assert tasks_mod._last_task_get(uid) == "t3"
        text = "напоминание 20 числа в 15"
        assert await tasks_mod.handle_last_task_clarify(_msg(uid, text), text, uid)
    pid, props = captured[-1]
    assert pid == "t3"
    assert props["Напоминание"]["date"]["start"].startswith("2026-11-20T15:00")  # не 2026-12-20


# ── last_task: TTL 10 мин, продление только правкой, потолок 30 мин ──────────

def _at(tasks_mod, t):
    return patch.object(tasks_mod, "_time", SimpleNamespace(time=lambda: t))


def test_last_task_sliding_ttl_and_ceiling(tasks_mod):
    uid = 900_004
    with _at(tasks_mod, 1000.0):
        tasks_mod._last_task_set(uid, "x")
    with _at(tasks_mod, 1000.0 + 590):
        assert tasks_mod._last_task_get(uid) == "x"
    with _at(tasks_mod, 1000.0 + 610):
        assert tasks_mod._last_task_get(uid) is None  # TTL 10 мин истёк без правки

    with _at(tasks_mod, 1000.0):
        tasks_mod._last_task_set(uid, "y")
    with _at(tasks_mod, 1000.0 + 500):
        tasks_mod._last_task_touch(uid)  # правка продлила окно
    with _at(tasks_mod, 1000.0 + 1000):
        assert tasks_mod._last_task_get(uid) == "y"
    with _at(tasks_mod, 1000.0 + 1500):
        tasks_mod._last_task_touch(uid)
    with _at(tasks_mod, 1000.0 + 1900):
        assert tasks_mod._last_task_get(uid) is None  # абсолютный потолок 30 мин


def test_last_task_get_does_not_extend(tasks_mod):
    uid = 900_005
    with _at(tasks_mod, 0.0):
        tasks_mod._last_task_set(uid, "z")
    for t in (300.0, 590.0):  # чтение окно не продлевает
        with _at(tasks_mod, t):
            assert tasks_mod._last_task_get(uid) == "z"
    with _at(tasks_mod, 700.0):
        assert tasks_mod._last_task_get(uid) is None


# ── D: вопрос про задачи → интент question → поиск ──────────────────────────

@pytest.mark.parametrize("text", [
    "стоит ли у меня задача купить корм",
    "есть ли у меня задача про биометрию?",
    "а есть ли напоминание про корм",
    "У меня есть задача купить корм?",
])
@pytest.mark.asyncio
async def test_D_question_intent(text):
    from core import classifier

    with patch.object(classifier, "_capture_for_eval"), \
         patch.object(classifier, "ask_claude", AsyncMock(side_effect=AssertionError("без LLM")), create=True):
        items = await classifier.classify(text, tz_offset=TZ, user_id="u")
    assert items[0]["type"] == "question"


@pytest.mark.asyncio
async def test_D_statements_are_not_questions():
    from core import classifier

    for text in ("добавь задачу купить корм", "создай задачу позвонить врачу"):
        assert not classifier._TASK_QUESTION_RE.match(text)


@pytest.mark.asyncio
async def test_D_question_searches_tasks(tasks_mod):
    found = SimpleNamespace(
        id="1", title="купить корм коту", deadline="2026-11-27T00:00:00+00:00",
        deadline_all_day=True, reminder="",
    )
    other = SimpleNamespace(id="2", title="позвонить врачу", deadline="", deadline_all_day=False, reminder="")
    msg = _msg(900_006, "")
    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[found, other])):
        await tasks_mod.handle_task_question(msg, "стоит ли у меня задача купить корм?", user_id="u")
    reply = msg.answer.await_args.args[0]
    assert "купить корм коту" in reply and "позвонить врачу" not in reply
    assert "2026-11-27" in reply and "03:00" not in reply


@pytest.mark.asyncio
async def test_D_question_not_found(tasks_mod):
    msg = _msg(900_007, "")
    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[])):
        await tasks_mod.handle_task_question(msg, "есть ли задача про полёт на марс?", user_id="u")
    assert "не нашла" in msg.answer.await_args.args[0]


def test_strip_when_tokens_smoke():
    assert strip_when_tokens("20 числа в 15").strip() == ""
