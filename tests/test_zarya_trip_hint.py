"""tests/test_zarya_trip_hint.py — подсказка формата отъезда, когда глагол есть, а диапазон дат не разобрался.

Реальный путь сообщения: настоящий Dispatcher с роутером Зари и её RoleMiddleware (роль подменена, БД не нужна), Message.answer перехвачен.
Считается число вызовов Haiku-классификатора. Даты и тексты вымышленные."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User

from core.booking import trip as T
from zarya import handlers as H
from zarya.bot import build_dispatcher

HINT = H._TRIP_HINT
BOT = Bot(token="123456:TESTTOKENNOTREAL_abcdefghijklmnopqrstuv")


class Env:
    def __init__(self):
        self.answers = []
        self.classify = AsyncMock(return_value={"intent": "chat", "reply": "болтовня Зари"})
        self.create_trip = AsyncMock(return_value=T.TripResult(7, True, 9, True, datetime(2026, 11, 9, 12, tzinfo=timezone(timedelta(hours=3)))))
        self.create_booking = AsyncMock(return_value=SimpleNamespace(id=5))
        self.show_slots = AsyncMock()


@pytest.fixture
def env():
    e = Env()

    async def fake_answer(self, text, *a, **k):
        e.answers.append(text)

    with patch.object(Message, "answer", fake_answer), \
         patch("zarya.classifier.classify_zarya", e.classify), \
         patch("zarya.handlers.trip_mod.create_trip", e.create_trip), \
         patch("zarya.handlers.create_booking", e.create_booking), \
         patch("zarya.handlers._show_slots", e.show_slots), \
         patch("zarya.handlers._owner_user_id", AsyncMock(return_value="uid")), \
         patch("zarya.handlers.busy_intervals", AsyncMock(return_value=[])), \
         patch("zarya.handlers._confirm_flow", AsyncMock()), \
         patch("core.auth_grants.get_display_name", AsyncMock(return_value=None)):
        yield e


async def send(text, role="admin", chat_type="private"):
    dp = build_dispatcher()                                   # настоящая проводка из zarya/bot.py (роль — внешний middleware, её видят фильтры)
    try:
        with patch("zarya.handlers.booking_role", AsyncMock(return_value=role)):
            msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=42, type=chat_type),
                          from_user=User(id=42, is_bot=False, first_name="Тест"), text=text)
            await dp.feed_update(BOT, Update(update_id=1, message=msg))
    finally:
        H.router._parent_router = None                       # роутер модульный: освободить для следующего теста


# ── подсказка ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["уезжаю 20 ноября", "уезжаю на 2 недели", "улетаю в пятницу", "Уеду в командировку", "отъезд скоро",
                                  "уезжаю с 20 ноября на месяц", "я улетаю", "уезжаю 20.11"])
async def test_admin_gets_the_format_hint_and_the_classifier_is_not_called(env, text):
    await send(text)
    assert env.answers == [HINT]
    env.classify.assert_not_awaited()
    env.create_trip.assert_not_awaited()
    env.create_booking.assert_not_awaited()


def test_hint_text_shows_the_working_format():
    assert "уезжаю с 20 ноября по 5 декабря" in HINT and T.parse_trip("уезжаю с 20 ноября по 5 декабря", datetime(2026, 11, 9).date()) is not None


# ── что подсказка не перехватывает ───────────────────────────────────────────

async def test_quick_booking_stays_a_booking(env):
    await send("уезжаю завтра в 19 шашлыки")
    env.create_booking.assert_awaited_once()
    assert env.create_booking.await_args.kwargs["status"] == "confirmed" and "шашлыки" in env.create_booking.await_args.kwargs["note"]
    assert HINT not in env.answers
    env.classify.assert_not_awaited()


async def test_slot_requests_stay_slot_requests(env):
    await send("уезжаю, когда у Кай окно на выходных?")
    env.show_slots.assert_awaited_once()
    assert HINT not in env.answers
    env.classify.assert_not_awaited()


async def test_a_correct_range_goes_to_the_trip_handler_not_to_the_hint(env):
    await send("уезжаю с 20.11 по 05.12")
    env.create_trip.assert_awaited_once()
    assert HINT not in env.answers and "Отъезд" in env.answers[0]
    env.classify.assert_not_awaited()


async def test_a_refused_range_gets_its_own_error_not_the_hint(env):
    await send("уезжаю с 20 по 5 ноября")
    assert env.answers == ["«по» раньше «с» — проверь даты 💅"] and HINT not in env.answers
    env.create_trip.assert_not_awaited()


async def test_other_chat_without_a_trip_verb_still_reaches_the_classifier_once(env):
    await send("привет, как дела")
    assert env.answers == ["болтовня Зари"] and env.classify.await_count == 1


async def test_commands_with_the_verb_are_not_hinted(env):
    await send("/help уезжаю")
    assert HINT not in env.answers
    await send("/неизвестная уезжаю")                          # неизвестная команда: не подсказка, а прежний путь через классификатор
    assert HINT not in env.answers and env.classify.await_count == 1


def test_the_bot_entrypoint_uses_the_tested_dispatcher_wiring():
    import inspect
    from zarya import bot as zbot
    src = inspect.getsource(zbot.main)
    assert "build_dispatcher()" in src and "Dispatcher()" not in src


# ── роли ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["friend", "guest"])
@pytest.mark.parametrize("text", ["уезжаю 20 ноября", "уезжаю на 2 недели", "улетаю в пятницу"])
async def test_other_roles_are_untouched_and_the_classifier_runs_as_before(env, role, text):
    await send(text, role=role)
    assert env.classify.await_count == 1                      # как и раньше: один вызов
    assert HINT not in env.answers and env.answers == ["болтовня Зари"]


@pytest.mark.parametrize("role", ["friend", "guest"])
async def test_other_roles_cannot_trigger_the_trip_handler(env, role):
    await send("уезжаю с 20.11 по 05.12", role=role)
    env.create_trip.assert_not_awaited()
    assert env.classify.await_count == 1


async def test_in_a_group_the_hint_needs_an_explicit_address(env):
    await send("уезжаю 20 ноября", chat_type="group")
    assert env.answers == [] and env.classify.await_count == 0                # без обращения Заря молчит, как и раньше


# ── публичная функция ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,yes", [("уезжаю 20 ноября", True), ("Улетаю в пятницу", True), ("отъезд скоро", True), ("в командировку", True),
                                      ("привет", False), ("", False), (None, False), ("завтра шашлыки", False), ("приезжаю в пятницу", False)])
def test_has_trip_verb(text, yes):
    assert T.has_trip_verb(text) is yes


def test_has_trip_verb_is_independent_of_the_range():
    assert T.has_trip_verb("уезжаю с 20 по 25 ноября") and T.is_trip_text("уезжаю с 20 по 25 ноября")
    assert T.has_trip_verb("уезжаю 20 ноября") and not T.is_trip_text("уезжаю 20 ноября")


def test_the_role_middleware_is_outer_so_that_filters_see_the_role():
    """Регрессия: с обычным (внутренним) middleware фильтры _is_trip / _is_quick_booking / _is_agenda видели role='guest' и не срабатывали."""
    dp = build_dispatcher()
    try:
        assert any(isinstance(m, H.RoleMiddleware) for m in dp.message.outer_middleware)
        assert any(isinstance(m, H.RoleMiddleware) for m in dp.callback_query.outer_middleware)
        assert not any(isinstance(m, H.RoleMiddleware) for m in dp.message.middleware)
    finally:
        H.router._parent_router = None

