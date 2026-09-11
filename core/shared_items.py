"""core/shared_items.py — расшаренные активные задачи Кай для контекста Зари (#242).

"флаг shared на задачах/списках, чтобы бот мог сказать «Кай хочет в кино на
выходных»" — Заря подтягивает короткую сводку в system-промпт при болтовне с
другом (role="friend"), сама решает как вплести её в реплику.

Списки (nexus_lists.shared) тоже несут колонку — задел на будущее, но сюда
пока не подключены: пример Кай был именно про задачу с дедлайном ("на
выходных"), у пункта списка обычно нет даты для такой формулировки.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from nexus.repos.tasks_tables import tasks, task_status

_DONE_CODES = ("Done", "Archived")
_MSK = timezone(timedelta(hours=3))
_DOW_PHRASE = [
    "в понедельник", "во вторник", "в среду", "в четверг",
    "в пятницу", "в субботу", "в воскресенье",
]


def _deadline_hint(dt: Optional[datetime], now: datetime) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    msk = dt.astimezone(_MSK)
    today = now.astimezone(_MSK).date()
    d = msk.date()
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
    return msk.strftime("%d.%m")


def _shared_tasks_sync(engine, user_id: str) -> List[dict]:
    done_ids = sa.select(task_status.c.id).where(task_status.c.code.in_(_DONE_CODES))
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
        return [{"title": r.title, "deadline": r.deadline} for r in conn.execute(q)]


async def shared_tasks_summary(user_id: str, *, engine=None, now: Optional[datetime] = None) -> str:
    """Короткая строка вида "хочет в кино (на выходных); забрать посылку (в среду)"
    для системного промпта Зари. Пустая строка, если нечего показать —
    вызывающий код тогда просто не добавляет секцию в промпт."""
    if not user_id:
        return ""
    if engine is None:
        from core.db import get_engine
        engine = get_engine()
    try:
        rows = await asyncio.to_thread(_shared_tasks_sync, engine, user_id)
    except Exception:
        return ""
    if not rows:
        return ""
    now = now or datetime.now(timezone.utc)
    parts = []
    for r in rows:
        hint = _deadline_hint(r["deadline"], now)
        title = (r["title"] or "").strip()
        if not title:
            continue
        parts.append(f"{title} ({hint})" if hint else title)
    return "; ".join(parts)
