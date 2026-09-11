"""core/shared_items.py — расшаренные задачи и пункты списков Кай для контекста
Зари (#242).

"флаг shared на задачах/списках, чтобы бот мог сказать «Кай хочет в кино на
выходных»" — Заря подтягивает короткую сводку в system-промпт при болтовне с
другом (role="friend"), сама решает как вплести её в реплику.

Задачи (tasks.shared) и Списки (nexus_lists.shared) идут в одну сводку —
у Кай нет отдельного NL-флоу под "расшарь пункт списка X" (только задачи,
core/classifier.py:_SHARE_RE + nexus/handlers/tasks.py:_apply_edit), но
колонка читается здесь тоже: "и подключи колонку к заре" — если Кай
проставит shared пункту списка напрямую (Mini App/БД), Заря его увидит.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from core.repos.lists_table import nexus_lists
from nexus.repos.tasks_tables import tasks, task_status

_DONE_TASK_CODES = ("Done", "Archived")
_DONE_LIST_STATUSES = ("done", "archived")
_MSK = timezone(timedelta(hours=3))
_DOW_PHRASE = [
    "в понедельник", "во вторник", "в среду", "в четверг",
    "в пятницу", "в субботу", "в воскресенье",
]


def _date_hint(d: Optional[date], now: datetime) -> str:
    """Человекочитаемый срок по МСК-календарю: "сегодня"/"завтра"/"на выходных"/
    день недели (в пределах недели) / "дд.мм" дальше. Пусто, если срока нет
    или он уже прошёл."""
    if d is None:
        return ""
    today = now.astimezone(_MSK).date()
    delta = (d - today).days
    if delta < 0:
        return ""
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta <= 7 and d.weekday() in (5, 6):
        return "на выходных"
    if delta <= 7:
        return _DOW_PHRASE[d.weekday()]
    return d.strftime("%d.%m")


def _shared_tasks_sync(engine, user_id: str) -> List[dict]:
    done_ids = sa.select(task_status.c.id).where(task_status.c.code.in_(_DONE_TASK_CODES))
    q = (
        sa.select(tasks.c.title, tasks.c.deadline)
        .where(tasks.c.shared.is_(True))
        .where(tasks.c.status_id.notin_(done_ids))
        .order_by(tasks.c.deadline.asc().nullslast())
        .limit(5)
    )
    if user_id:
        q = q.where(tasks.c.user_id.in_([user_id, ""]))
    with engine.connect() as conn:
        rows = []
        for r in conn.execute(q):
            d = r.deadline
            if d is not None:
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                d = d.astimezone(_MSK).date()
            rows.append({"title": r.title, "date": d})
        return rows


def _shared_list_items_sync(engine, user_id: str) -> List[dict]:
    q = (
        sa.select(nexus_lists.c.name, nexus_lists.c.expires_at)
        .where(nexus_lists.c.shared.is_(True))
        .where(nexus_lists.c.status.notin_(_DONE_LIST_STATUSES))
        .order_by(nexus_lists.c.expires_at.asc().nullslast())
        .limit(5)
    )
    if user_id:
        q = q.where(nexus_lists.c.user_id.in_([user_id, ""]))
    with engine.connect() as conn:
        return [{"title": r.name, "date": r.expires_at} for r in conn.execute(q)]


async def shared_items_summary(user_id: str, *, engine=None, now: Optional[datetime] = None) -> str:
    """Короткая строка вида "хочет в кино (на выходных); забрать посылку (в среду)"
    из расшаренных задач + пунктов списков — для системного промпта Зари.
    Пустая строка, если нечего показать — вызывающий код тогда просто не
    добавляет секцию в промпт."""
    if not user_id:
        return ""
    if engine is None:
        from core.db import get_engine
        engine = get_engine()
    try:
        task_rows = await asyncio.to_thread(_shared_tasks_sync, engine, user_id)
        list_rows = await asyncio.to_thread(_shared_list_items_sync, engine, user_id)
    except Exception:
        return ""
    rows = task_rows + list_rows
    if not rows:
        return ""
    now = now or datetime.now(timezone.utc)
    parts = []
    for r in rows:
        title = (r["title"] or "").strip()
        if not title:
            continue
        hint = _date_hint(r["date"], now)
        parts.append(f"{title} ({hint})" if hint else title)
    return "; ".join(parts)


# Обратная совместимость: старое имя (до #242-b — подключение Списков).
shared_tasks_summary = shared_items_summary
