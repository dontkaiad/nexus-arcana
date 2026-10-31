"""tests/test_zarya_agenda.py — Zarya admin agenda: read-only Nexus tasks/bookings/works by day."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking.agenda import AgendaItem, day_agenda
from zarya.formatting import MSK, agenda_range, format_agenda, wants_agenda

UTC = timezone.utc
TUE = date(2026, 11, 3)          # вт 03.11
MON = date(2026, 11, 2)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _msk(day: date, h: int, m: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=MSK)


def _engine():
    eng = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        c.execute(sa.text("CREATE TABLE work_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
                          "emoji TEXT, label TEXT NOT NULL DEFAULT '', sort INTEGER DEFAULT 0)"))
        for code in ("Not started", "Done"):
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
            c.execute(sa.text("INSERT INTO work_status (code, label) VALUES (:c, :c)"), {"c": code})
        c.execute(sa.text("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                          "deadline TEXT, status_id INTEGER, duration_min INTEGER, user_id TEXT DEFAULT '', "
                          "deadline_all_day INTEGER NOT NULL DEFAULT 0)"))
        c.execute(sa.text("CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                          "scheduled_at TEXT, status_id INTEGER, duration_min INTEGER, user_id TEXT DEFAULT '')"))
        c.execute(sa.text("CREATE TABLE booking (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
                          "start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
                          "requester_name TEXT DEFAULT '', note TEXT DEFAULT '', nexus_task_id TEXT, arcana_work_id TEXT)"))
    return eng


def _sid(eng, table, code):
    with eng.connect() as c:
        return c.execute(sa.text(f"SELECT id FROM {table} WHERE code=:c"), {"c": code}).scalar()


def _add_task(eng, title, when, *, dur=None, all_day=0, status="Not started", user="u1"):
    with eng.begin() as c:
        return c.execute(sa.text(
            "INSERT INTO tasks (title, deadline, status_id, duration_min, deadline_all_day, user_id) "
            "VALUES (:t,:d,:s,:du,:a,:u)"),
            {"t": title, "d": _iso(when), "s": _sid(eng, "task_status", status), "du": dur, "a": all_day, "u": user},
        ).lastrowid


# ── day_agenda (DB) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_agenda_lists_tasks_works_bookings_sorted():
    eng = _engine()
    _add_task(eng, "созвон", _msk(TUE, 14), dur=180)
    _add_task(eng, "оплатить счёт", _msk(TUE, 0, 1), all_day=1)  # +1 мин: SQLite сравнивает даты строками, ровно на границе суток промахивается
    _add_task(eng, "чужая", _msk(TUE, 10), user="other")
    _add_task(eng, "сделано", _msk(TUE, 11), status="Done")
    _add_task(eng, "вчера", _msk(MON, 12))
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO works (title, scheduled_at, status_id, user_id) VALUES ('расклад', :d, :s, 'u1')"),
                  {"d": _iso(_msk(TUE, 19)), "s": _sid(eng, "work_status", "Not started")})
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, requester_name, note) "
                          "VALUES ('u1', :s, :e, 'confirmed', 'Рома', 'шашлыки')"),
                  {"s": _iso(_msk(TUE, 16)), "e": _iso(_msk(TUE, 18))})
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, note) "
                          "VALUES ('u1', :s, :e, 'rejected', 'отклонено')"),
                  {"s": _iso(_msk(TUE, 9)), "e": _iso(_msk(TUE, 10))})
    items = await day_agenda("u1", TUE, TUE, engine=eng)
    assert [(i.kind, i.title, i.all_day) for i in items] == [
        ("task", "оплатить счёт", True),
        ("task", "созвон", False),
        ("booking", "шашлыки", False),
        ("work", "расклад", False),
    ]
    assert items[1].end - items[1].start == timedelta(minutes=180)


@pytest.mark.asyncio
async def test_linked_task_shown_once_via_booking():
    eng = _engine()
    tid = _add_task(eng, "Рома · шашлыки", _msk(TUE, 16), dur=120)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, note, nexus_task_id) "
                          "VALUES ('u1', :s, :e, 'confirmed', 'шашлыки', :t)"),
                  {"s": _iso(_msk(TUE, 16)), "e": _iso(_msk(TUE, 18)), "t": str(tid)})
    items = await day_agenda("u1", TUE, TUE, engine=eng)
    assert [(i.kind, i.title) for i in items] == [("booking", "шашлыки")]


@pytest.mark.asyncio
async def test_empty_day_and_week_range():
    eng = _engine()
    _add_task(eng, "в четверг", _msk(TUE + timedelta(days=2), 12))
    assert await day_agenda("u1", TUE, TUE, engine=eng) == []
    week = await day_agenda("u1", TUE, TUE + timedelta(days=6), engine=eng)
    assert [i.title for i in week] == ["в четверг"]


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("t,hit", [
    ("какие у меня задачи на завтра", True),
    ("что у меня во вторник", True),
    ("покажи план на неделю", True),
    ("чем я занята сегодня", True),
    ("расписание на среду", True),
    ("как дела", False),
    ("когда у меня свободные окна", False),
    ("запиши меня на завтра в 19", False),
    ("привет", False),
])
def test_wants_agenda(t, hit):
    assert wants_agenda(t) is hit


def test_agenda_range():
    assert agenda_range("что у меня", TUE) == (TUE, TUE)                      # нет даты → сегодня
    assert agenda_range("задачи на завтра", TUE) == (TUE + timedelta(days=1),) * 2
    assert agenda_range("что у меня на неделе", TUE) == (TUE, TUE + timedelta(days=6))


# ── formatting ───────────────────────────────────────────────────────────────

def _item(kind, title, start, end=None, all_day=False, day=TUE):
    return AgendaItem(day, kind, title, start, end, all_day)


def test_format_agenda_layout():
    items = [
        _item("task", "оплатить X", _msk(TUE, 0), all_day=True),
        _item("task", "созвон", _msk(TUE, 14), _msk(TUE, 17)),
        _item("work", "расклад", _msk(TUE, 19)),
    ]
    assert format_agenda(items, TUE, TUE, MON) == (
        "🗓 <b>вт 03.11</b>\n• весь день: оплатить X\n• 14:00–17:00 созвон\n🔮 19:00 расклад")


def test_format_agenda_escapes_html():
    out = format_agenda([_item("task", "<b>x</b>", _msk(TUE, 9))], TUE, TUE, MON)
    assert "&lt;b&gt;x&lt;/b&gt;" in out


@pytest.mark.parametrize("d,today,expected", [
    (TUE, TUE - timedelta(days=3), "Вторник чистый 💅"),
    (TUE, TUE, "Сегодня чисто 💅"),
    (TUE, MON, "Завтра чисто 💅"),
])
def test_format_agenda_empty_day(d, today, expected):
    assert format_agenda([], d, d, today) == expected


def test_format_agenda_empty_week():
    assert format_agenda([], TUE, TUE + timedelta(days=6), TUE) == "Неделя чистая 💅"


# ── handlers: admin only ─────────────────────────────────────────────────────

def _msg(text):
    return SimpleNamespace(text=text, answer=AsyncMock(), chat=SimpleNamespace(type="private", id=1),
                           from_user=SimpleNamespace(id=1, full_name="x"), bot=SimpleNamespace(id=9),
                           entities=None, reply_to_message=None)


def test_agenda_filter_admin_only():
    from zarya.handlers import _is_agenda
    m = _msg("что у меня на завтра")
    assert _is_agenda(m, role="admin") is True
    assert _is_agenda(m, role="friend") is False
    assert _is_agenda(m, role="guest") is False
    assert _is_agenda(_msg("/bookings что у меня"), role="admin") is False


@pytest.mark.asyncio
async def test_admin_sees_tasks():
    from zarya.handlers import nl_agenda
    m = _msg("что у меня сегодня")
    today = datetime.now(MSK).date()
    items = [_item("task", "секретная задача", _msk(today, 12), day=today)]
    with patch("zarya.handlers._owner_user_id", AsyncMock(return_value="u1")), \
         patch("core.booking.agenda.day_agenda", AsyncMock(return_value=items)):
        await nl_agenda(m, role="admin")
    assert "секретная задача" in m.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_friend_never_sees_agenda_via_router():
    """Через реальный Dispatcher: friend пишет «что у меня» → agenda-хендлер не зовётся."""
    from zarya.handlers import _is_agenda
    with patch("core.booking.agenda.day_agenda", AsyncMock()) as da:
        assert _is_agenda(_msg("какие у Кай задачи"), role="friend") is False
    da.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrecognized_routes_agenda_only_for_admin():
    from zarya.handlers import on_unrecognized
    ag = {"intent": "agenda", "reply": ""}
    with patch("zarya.classifier.classify_zarya", AsyncMock(return_value=ag)), \
         patch("zarya.handlers._show_agenda", AsyncMock()) as sa_:
        await on_unrecognized(_msg("чем занимаюсь"), role="admin")
        sa_.assert_awaited_once()
        sa_.reset_mock()
        m = _msg("чем занимается Кай")
        await on_unrecognized(m, role="friend")
        sa_.assert_not_awaited()
        m.answer.assert_awaited_once()  # дефолтная заглушка, не повестка


@pytest.mark.asyncio
async def test_classifier_demotes_agenda_for_non_admin():
    from zarya.classifier import classify_zarya
    raw = '{"intent":"agenda","reply":""}'
    with patch("zarya.classifier.ask_claude", AsyncMock(return_value=raw)), \
         patch("zarya.classifier._friend_shared_summary", AsyncMock(return_value="")):
        assert (await classify_zarya("что у Кай", role="friend"))["intent"] == "chat"
        assert (await classify_zarya("что у Кай", role="guest"))["intent"] == "chat"
        assert (await classify_zarya("что у меня", role="admin"))["intent"] == "agenda"
