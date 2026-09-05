"""arcana/repos/pg_works_repo.py — PostgreSQL adapter for 🔮 Работы."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List

from sqlalchemy import select, text

from arcana.repos.works_repo import Work
from arcana.repos.works_tables import (
    works, work_priority, work_status, work_repeat, work_day_of_week,
)
from core.db import get_engine

logger = logging.getLogger("arcana.pg_works")

_PRIORITY_TO_CODE = {
    "срочно":      "urgent",
    "важно":       "important",
    "можно потом": "later",
    "urgent":      "urgent",
    "important":   "important",
    "later":       "later",
}

# RU label / EN alias → work_repeat.code
_REPEAT_TO_CODE = {
    "нет":         "none",
    "ежедневно":   "daily",
    "еженедельно": "weekly",
    "ежемесячно":  "monthly",
    "none":        "none",
    "daily":       "daily",
    "weekly":      "weekly",
    "monthly":     "monthly",
}

# RU label → work_day_of_week.code
_DOW_TO_CODE = {
    "пн": "mon", "вт": "tue", "ср": "wed", "чт": "thu",
    "пт": "fri", "сб": "sat", "вс": "sun",
}


def _code_for(mapping: dict, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return mapping.get(raw.lower().strip())


def _resolve(conn, table, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = conn.execute(
        select(table.c.id).where(table.c.code == code)
    ).fetchone()
    return row[0] if row else None


def _row_to_work(row) -> Work:
    deadline_str = ""
    deadline_dt = None
    deadline_iso = ""
    if row.deadline:
        d = row.deadline
        deadline_str = f" · 📅 {d.day:02d}.{d.month:02d}"
        if d.hour or d.minute:
            deadline_str += f" {d.hour:02d}:{d.minute:02d}"
        deadline_dt = d
        try:
            deadline_iso = d.isoformat()
        except Exception:
            pass
    cat = row.category or ""
    reminder_dt = getattr(row, "reminder", None) or None
    return Work(
        id=str(row.id),
        title=row.title or "",
        priority=row.priority_label or "Можно потом",
        deadline_str=deadline_str,
        category_str=f" · {cat}" if cat else "",
        has_client=bool(row.client_id),
        status=row.status_code or "open",
        client_id=str(row.client_id) if row.client_id else None,
        deadline_dt=deadline_dt,
        reminder_dt=reminder_dt,
        deadline_iso=deadline_iso,
        category=cat,
        repeat=getattr(row, "repeat_label", None) or "Нет",
        day_of_week=getattr(row, "dow_label", None) or "",
        repeat_time=getattr(row, "repeat_time", None) or "",
    )


def _select_works():
    p = work_priority.alias("p")
    rp = work_repeat.alias("rp")
    dw = work_day_of_week.alias("dw")
    return (
        select(
            works.c.id,
            works.c.title,
            works.c.deadline,
            works.c.reminder,
            works.c.category,
            works.c.client_id,
            works.c.repeat_time,
            p.c.label.label("priority_label"),
            work_status.c.code.label("status_code"),
            rp.c.label.label("repeat_label"),
            dw.c.label.label("dow_label"),
        )
        .outerjoin(p,           works.c.priority_id == p.c.id)
        .outerjoin(work_status, works.c.status_id   == work_status.c.id)
        .outerjoin(rp,          works.c.repeat_id   == rp.c.id)
        .outerjoin(dw,          works.c.day_of_week_id == dw.c.id)
    )


class PgWorksRepo:

    def _list_open_sync(self, user_notion_id: str) -> List[Work]:
        stmt = (
            _select_works()
            .where(work_status.c.code != "done")
            .order_by(works.c.deadline.asc().nullslast())
        )
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_work(r) for r in rows]

    def _find_by_title_sync(self, query: str, user_notion_id: str) -> List[Work]:
        """ILIKE-поиск открытых Работ по названию (#152: PG-эквивалент старого
        Notion title-contains для «привязать список к работе»)."""
        stmt = _select_works().where(work_status.c.code != "done")
        if query:
            stmt = stmt.where(works.c.title.ilike(f"%{query}%"))
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        stmt = stmt.order_by(works.c.deadline.asc().nullslast()).limit(10)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_work(r) for r in rows]

    def _find_active_for_client_sync(
        self, client_id: str, category: str, user_notion_id: str
    ) -> Optional[Work]:
        """Первая открытая Работа клиента нужной категории (для авто-привязки
        записи #151). category — точное совпадение works.category
        («🃏 Расклад»/«✨ Ритуал»). Status не done/archived, сорт дедлайн ASC."""
        try:
            cid = int(client_id)
        except (ValueError, TypeError):
            return None
        stmt = (
            _select_works()
            .where(works.c.client_id == cid)
            .where(works.c.category == category)
            .where(work_status.c.code.notin_(["done", "archived"]))
            .order_by(works.c.deadline.asc().nullslast())
            .limit(1)
        )
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).fetchone()
        return _row_to_work(row) if row else None

    def _create_sync(
        self,
        title: str,
        priority: str,
        deadline: Optional[datetime],
        category: Optional[str],
        client_id: Optional[str],
        user_notion_id: str,
        repeat: str = "Нет",
        repeat_time: Optional[str] = None,
        day_of_week: Optional[str] = None,
    ) -> Optional[str]:
        pcode = _code_for(_PRIORITY_TO_CODE, priority) or "later"
        rcode = _code_for(_REPEAT_TO_CODE, repeat)
        dcode = _code_for(_DOW_TO_CODE, day_of_week)
        with get_engine().begin() as conn:
            open_id  = _resolve(conn, work_status,   "open")
            prio_id  = _resolve(conn, work_priority, pcode)
            rep_id   = _resolve(conn, work_repeat,   rcode) if rcode else None
            dow_id   = _resolve(conn, work_day_of_week, dcode) if dcode else None
            cid_int  = int(client_id) if client_id and client_id.isdigit() else None
            row = conn.execute(
                works.insert().values(
                    title=title,
                    deadline=deadline,
                    category=category or None,
                    priority_id=prio_id,
                    status_id=open_id,
                    client_id=cid_int,
                    repeat_id=rep_id,
                    day_of_week_id=dow_id,
                    repeat_time=repeat_time or None,
                    user_notion_id=user_notion_id or None,
                ).returning(works.c.id)
            ).fetchone()
        return str(row[0]) if row else None

    def _set_repeat_fields_sync(
        self,
        work_id: str,
        repeat: str,
        day_of_week: Optional[str],
        repeat_time: Optional[str],
    ) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        rcode = _code_for(_REPEAT_TO_CODE, repeat)
        dcode = _code_for(_DOW_TO_CODE, day_of_week)
        with get_engine().begin() as conn:
            vals: dict = {}
            if rcode:
                vals["repeat_id"] = _resolve(conn, work_repeat, rcode)
            if dcode:
                vals["day_of_week_id"] = _resolve(conn, work_day_of_week, dcode)
            if repeat_time:
                vals["repeat_time"] = repeat_time
            if not vals:
                return False
            res = conn.execute(works.update().where(works.c.id == wid).values(**vals))
        return res.rowcount > 0

    def _mark_done_sync(self, work_id: str) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            done_id = _resolve(conn, work_status, "done")
            res = conn.execute(
                works.update()
                .where(works.c.id == wid)
                .values(status_id=done_id)
            )
        return res.rowcount > 0

    def _find_by_id_sync(self, work_id: str) -> Optional[Work]:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return None
        stmt = _select_works().where(works.c.id == wid)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).fetchone()
        return _row_to_work(row) if row else None

    def _list_all_sync(self, user_notion_id: str) -> List[Work]:
        stmt = _select_works().order_by(works.c.deadline.asc().nullslast())
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_work(r) for r in rows]

    def _active_with_future_reminder_sync(self, user_notion_id: str) -> List[Work]:
        """Работы, у которых есть будущее напоминание и статус не done/archived
        (для restore reminders на старте — паритет с Nexus tasks)."""
        stmt = (
            _select_works()
            .where(work_status.c.code.notin_(["done", "archived"]))
            .where(works.c.reminder.isnot(None))
            .where(works.c.reminder > text("now()"))
        )
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_work(r) for r in rows]

    def _set_status_sync(self, work_id: str, status_code: str) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            sid = _resolve(conn, work_status, status_code)
            res = conn.execute(
                works.update()
                .where(works.c.id == wid)
                .values(status_id=sid)
            )
        return res.rowcount > 0

    def _set_deadline_sync(self, work_id: str, new_date: date) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        new_dt = datetime(
            new_date.year, new_date.month, new_date.day, 0, 0, 0,
            tzinfo=timezone.utc,
        )
        with get_engine().begin() as conn:
            res = conn.execute(
                works.update()
                .where(works.c.id == wid)
                .values(deadline=new_dt)
            )
        return res.rowcount > 0

    def _set_props_sync(self, work_id: str, fields: dict, tz_offset: int = 3) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            vals = {}
            cat = fields.get("category")
            if cat is not None:
                vals["category"] = str(cat)
            pr = fields.get("priority")
            if pr is not None:
                pcode = _code_for(_PRIORITY_TO_CODE, pr)
                pid = _resolve(conn, work_priority, pcode) if pcode else None
                if pid:
                    vals["priority_id"] = pid
            dl = fields.get("deadline")
            if dl:
                iso = str(dl).replace(" ", "T")
                parsed = None
                try:
                    parsed = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                except ValueError:
                    try:
                        parsed = datetime.strptime(iso[:10], "%Y-%m-%d")
                    except ValueError:
                        parsed = None
                if parsed:
                    if parsed.tzinfo is None:
                        # `dl` идёт от Haiku как наивная строка ЛОКАЛЬНОГО
                        # времени юзера, не UTC — раньше здесь молча
                        # ставился timezone.utc, из-за чего в PG уезжало
                        # время (тот же баг что был в reply-правке задач
                        # Nexus, см. core/reply_update.py _with_tz_suffix).
                        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=tz_offset)))
                    vals["deadline"] = parsed
            if not vals:
                return False
            res = conn.execute(works.update().where(works.c.id == wid).values(**vals))
        return res.rowcount > 0

    def _reschedule_cycle_sync(
        self,
        work_id: str,
        deadline: Optional[datetime],
        reminder: Optional[datetime],
    ) -> bool:
        """Сдвинуть повторяющуюся Работу на следующий цикл: новые
        deadline/reminder + статус обратно в 'open' (Работы Арканы не имеют
        промежуточного 'in progress' — см. _handle_recurring_work_done)."""
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            open_id = _resolve(conn, work_status, "open")
            vals: dict = {"status_id": open_id}
            if deadline is not None:
                vals["deadline"] = deadline
            if reminder is not None:
                vals["reminder"] = reminder
            res = conn.execute(works.update().where(works.c.id == wid).values(**vals))
        return res.rowcount > 0

    async def reschedule_cycle(
        self, work_id: str,
        deadline: Optional[datetime] = None,
        reminder: Optional[datetime] = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._reschedule_cycle_sync, work_id, deadline, reminder
        )

    async def set_repeat_fields(
        self,
        work_id: str,
        repeat: str,
        day_of_week: Optional[str] = None,
        repeat_time: Optional[str] = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._set_repeat_fields_sync, work_id, repeat, day_of_week, repeat_time
        )

    async def set_props(self, work_id: str, tz_offset: int = 3, **fields) -> bool:
        """Обновить поля Работы (reply-правка #156; переиспользуемо #154).

        Поля: category (Text), priority ('срочно/важно/можно потом'),
        deadline ('YYYY-MM-DD[ HH:MM]'). `tz_offset` — часовой пояс юзера,
        применяется к naive deadline (см. _set_props_sync).
        """
        return await asyncio.to_thread(self._set_props_sync, work_id, fields, tz_offset)

    # ── Public async interface ────────────────────────────────────────────────

    async def list_open(self, user_notion_id: str = "") -> List[Work]:
        return await asyncio.to_thread(self._list_open_sync, user_notion_id)

    async def find_by_id(self, work_id: str) -> Optional[Work]:
        return await asyncio.to_thread(self._find_by_id_sync, work_id)

    async def find_by_title(self, query: str, user_notion_id: str = "") -> List[Work]:
        return await asyncio.to_thread(self._find_by_title_sync, query, user_notion_id)

    async def list_all(self, user_notion_id: str = "") -> List[Work]:
        return await asyncio.to_thread(self._list_all_sync, user_notion_id)

    async def active_with_future_reminder(self, user_notion_id: str = "") -> List[Work]:
        return await asyncio.to_thread(self._active_with_future_reminder_sync, user_notion_id)

    async def find_active_for_client(
        self, client_id: str, category: str, user_notion_id: str = "",
    ) -> Optional[Work]:
        return await asyncio.to_thread(
            self._find_active_for_client_sync, client_id, category, user_notion_id
        )

    async def set_status(self, work_id: str, status_code: str) -> bool:
        return await asyncio.to_thread(self._set_status_sync, work_id, status_code)

    async def set_deadline(self, work_id: str, new_date: date) -> bool:
        return await asyncio.to_thread(self._set_deadline_sync, work_id, new_date)

    async def create(
        self,
        title: str,
        priority: str = "Можно потом",
        deadline: Optional[datetime] = None,
        category: Optional[str] = None,
        client_id: Optional[str] = None,
        user_notion_id: str = "",
        repeat: str = "Нет",
        repeat_time: Optional[str] = None,
        day_of_week: Optional[str] = None,
    ) -> Optional[str]:
        return await asyncio.to_thread(
            self._create_sync,
            title, priority, deadline, category, client_id, user_notion_id,
            repeat, repeat_time, day_of_week,
        )

    async def mark_done(self, work_id: str) -> bool:
        return await asyncio.to_thread(self._mark_done_sync, work_id)
