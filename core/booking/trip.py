"""core/booking/trip.py — «уезжаю с X по Y» (Заря): блок в букинге + задача «Выбрать ситтера для котов (X–Y)» с напоминанием.

Без LLM: разбор дат детерминированный. Запись — прямые записи в общую Postgres (как core/booking/linkage.py), токен не нужен.

Правила разбора (решения владельца):
  • год не назван → СЛЕДУЮЩЕЕ такое число (сегодня включительно); конец — первое такое число не раньше начала;
  • «по» раньше «с» (в одном месяце, при явных годах и т.п.) → отказ; поездка длиннее MAX_TRIP_DAYS → отказ;
  • нет месяца ни у одной даты → отказ с подсказкой; несуществующая дата (31 ноября) → отказ.
Напоминание: завтра 12:00 МСК; отъезд дальше 14 дней → за 14 дней до него в 12:00 МСК. Идемпотентность — по названию задачи.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

import sqlalchemy as sa

logger = logging.getLogger("booking.trip")

MSK = timezone(timedelta(hours=3))
MAX_TRIP_DAYS = 180
REMIND_DAYS_BEFORE = 14
REMIND_HOUR = 12
BLOCK_REASON = "Отъезд"
TASK_PREFIX = "Выбрать ситтера для котов"

_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
_MON = r"(?:янв|фев|мар|апр|ма[яй]|июн|июл|авг|сен|окт|ноя|дек)[а-я]*"
_DATE = r"\d{1,2}(?:\.\d{1,2}(?:\.\d{2,4})?|\s+" + _MON + r"(?:\s+\d{4})?)?"
_RANGE_RE = re.compile(r"(?<![\d.])(?:с\s+)?(?P<a>" + _DATE + r")\s*(?:по|до|-|–|—)\s*(?P<b>" + _DATE + r")(?![\d])", re.I)
_PART_RE = re.compile(r"^(?P<d>\d{1,2})(?:\.(?P<m>\d{1,2})(?:\.(?P<y>\d{2,4}))?|\s+(?P<mn>" + _MON + r")(?:\s+(?P<y2>\d{4}))?)?$", re.I)
_TRIGGER_RE = re.compile(r"\b(?:уезжа[юе]\w*|уед[уе]\w*|улета[юе]\w*|улеч[уе]\w*|отъезд\w*|в\s+отъезд\w*|в\s+командировк\w*)", re.I)


@dataclass(frozen=True)
class Trip:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class TripError(Exception):
    """code: reversed | past | too_long | month | date"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_trip_text(text: str) -> bool:
    """Глагол отъезда И диапазон дат в одной фразе: иначе обычные фразы («уезжаю завтра в 19») идут своими обработчиками."""
    t = (text or "").lower()
    return bool(_TRIGGER_RE.search(t) and _RANGE_RE.search(t))


def _month_of(name: str) -> int:
    n = name.lower()
    return _MONTHS.get(n[:3]) or _MONTHS["мая"]


def _next_occurrence(day: int, month: int, floor: date) -> date:
    for y in (floor.year, floor.year + 1, floor.year + 2):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if d >= floor:
            return d
    raise TripError("date")


def _valid(day: int, month: int, year: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        raise TripError("date")


def parse_trip(text: str, today: date) -> Optional[Trip]:
    """None — это не фраза про отъезд; Trip — разобрано; TripError — понятая, но неверная (reversed/past/too_long/month/date)."""
    if not is_trip_text(text):
        return None
    m = _RANGE_RE.search((text or "").lower())
    if not m:
        return None
    a_raw, b_raw = m.group("a"), m.group("b")
    da, ma, ya = _parts(a_raw)
    db, mb, yb = _parts(b_raw)
    if ma is None and mb is None:
        raise TripError("month")
    same_month_only_b = ma is None and mb is not None
    if ma is None:
        ma = mb                                              # «с 20 по 25 ноября»: месяц один на обе даты
    if mb is None:
        mb = ma
    # начало
    if ya is not None:
        start = _valid(da, ma, ya)
        if start < today:
            raise TripError("past")
    elif yb is not None:                                     # «с 20 по 25 ноября 2027» / «с 28 декабря по 5 января 2027»: год начала от года конца
        start = _valid(da, ma, yb - 1 if ma > mb else yb)
        if start < today:
            raise TripError("past")
    else:
        start = _next_occurrence(da, ma, today)
    # конец
    if yb is not None:
        end = _valid(db, mb, yb)
    else:
        if same_month_only_b and db < da:
            raise TripError("reversed")                      # «с 20 по 5 ноября»
        if ma == mb and db < da:
            raise TripError("reversed")                      # «с 20 ноября по 5 ноября»
        end = _next_occurrence(db, mb, start)
    if end < start:
        raise TripError("reversed")
    if (end - start).days + 1 > MAX_TRIP_DAYS:
        raise TripError("too_long")
    return Trip(start, end)


def _parts(raw: str) -> Tuple[int, Optional[int], Optional[int]]:
    m = _PART_RE.match(raw.strip().lower())
    if not m:
        raise TripError("date")
    month = int(m.group("m")) if m.group("m") else (_month_of(m.group("mn")) if m.group("mn") else None)
    y = m.group("y") or m.group("y2")
    year = None
    if y:
        year = int(y) + 2000 if len(y) == 2 else int(y)
    day = int(m.group("d"))
    if month is not None and not 1 <= month <= 12:
        raise TripError("date")
    return day, month, year


def reminder_for(start: date, today: date) -> datetime:
    """Завтра 12:00 МСК; отъезд дальше 14 дней — за 14 дней до него в 12:00 МСК. Всегда в будущем (не раньше завтрашнего дня)."""
    tomorrow = today + timedelta(days=1)
    day = start - timedelta(days=REMIND_DAYS_BEFORE) if (start - today).days > REMIND_DAYS_BEFORE else tomorrow
    day = max(day, tomorrow)
    return datetime.combine(day, time(REMIND_HOUR, 0), tzinfo=MSK)


def task_title(trip: Trip) -> str:
    return "%s (%s–%s)" % (TASK_PREFIX, trip.start.strftime("%d.%m"), trip.end.strftime("%d.%m"))


@dataclass
class TripResult:
    block_id: int
    block_created: bool
    task_id: Optional[int]
    task_created: bool
    reminder: datetime


def _engine():
    from core.db import get_engine
    return get_engine()


def _find_task(conn, user_id: str, title: str) -> Optional[int]:
    from nexus.repos.tasks_tables import task_status, tasks
    return conn.execute(
        sa.select(tasks.c.id).select_from(tasks.join(task_status, task_status.c.id == tasks.c.status_id))
        .where(tasks.c.user_id == user_id, tasks.c.title == title, task_status.c.code.notin_(["Done", "Archived"]))
        .order_by(tasks.c.id)).scalar()


def _create_task_sync(user_id: str, trip: Trip, block_id: int, remind: datetime) -> Tuple[Optional[int], bool]:
    from nexus.repos.tasks_tables import task_status, tasks
    title = task_title(trip)
    with _engine().begin() as conn:
        found = _find_task(conn, user_id, title)
        if found:
            return found, False
        sid = conn.execute(sa.select(task_status.c.id).where(task_status.c.code == "Not started")).scalar()
        tid = conn.execute(tasks.insert().values(
            title=title, status_id=sid, user_id=user_id, reminder=remind,
            note="отъезд %s – %s · блок букинга #%s" % (trip.start.strftime("%d.%m.%Y"), trip.end.strftime("%d.%m.%Y"), block_id),
        ).returning(tasks.c.id)).scalar()
    return (int(tid) if tid else None), bool(tid)


async def create_trip(user_id: str, trip: Trip, today: date) -> TripResult:
    """Блок «Отъезд» (занимает слоты; линкованная задача 🚫 создаётся как у любого блока) + задача выбора ситтера с напоминанием."""
    from core.booking.repo import add_block, list_blocks
    start_at = datetime.combine(trip.start, time(0, 0), tzinfo=MSK)
    end_at = datetime.combine(trip.end, time(23, 59), tzinfo=MSK)
    block = next((b for b in await list_blocks(user_id)
                  if b.get("user_id") == user_id and b["reason"] == BLOCK_REASON and b["start_at"] == start_at and b["end_at"] == end_at), None)
    created = block is None
    if created:
        block = await add_block(user_id, start_at, end_at, BLOCK_REASON)
    remind = reminder_for(trip.start, today)
    tid, tcreated = await asyncio.to_thread(_create_task_sync, user_id, trip, int(block["id"]), remind)
    return TripResult(int(block["id"]), created, tid, tcreated, remind)


def _archive_task_sync(user_id: str, task_id: int) -> bool:
    from nexus.repos.tasks_tables import task_status, tasks
    with _engine().begin() as conn:
        row = conn.execute(
            sa.select(tasks.c.title, task_status.c.code).select_from(tasks.join(task_status, task_status.c.id == tasks.c.status_id))
            .where(tasks.c.id == task_id, tasks.c.user_id == user_id)).first()
        if not row or not row.title.startswith(TASK_PREFIX) or row.code in ("Done", "Archived"):
            return False                                         # чужая, не та задача, уже выполнена (ситтер выбран) или архив — не трогаем
        aid = conn.execute(sa.select(task_status.c.id).where(task_status.c.code == "Archived")).scalar()
        conn.execute(tasks.update().where(tasks.c.id == task_id).values(status_id=aid))
    return True


async def cancel_trip(user_id: str, block_id: int, task_id: Optional[int]) -> Tuple[bool, bool]:
    """Отмена: блок удаляется (его линкованная задача архивируется), задача выбора ситтера архивируется, если ещё не выполнена."""
    from core.booking.repo import del_block, list_blocks
    block = next((b for b in await list_blocks(user_id) if b["id"] == block_id and b.get("user_id") == user_id and b["reason"] == BLOCK_REASON), None)
    block_gone = bool(block) and await del_block(block_id, user_id)
    task_gone = bool(task_id) and await asyncio.to_thread(_archive_task_sync, user_id, int(task_id))
    return block_gone, task_gone
