"""core/cat_health.py — здоровье котов: запись данных и напоминания в ОБЫЧНЫХ задачах Nexus.

Данные (вес, визиты / прививки, курсы лекарств) лежат в cat_weights / cat_vet_visits / cat_medications (pg_cat_health_repo). Напоминание — это
задача Nexus с cat_id и категорией «🐾 Коты»: её видно в списке задач, она приходит в обычный срок, живёт по тем же правилам.
  • визит / прививка: напоминание в 09:00 накануне (если уже поздно — в 09:00 в день визита), дедлайн — дата визита. Состоявшийся визит
    с «следующий срок» ведёт ту же задачу на следующий срок; отменённый или удалённый снимает задачу;
  • курс лекарства: ежедневная задача с напоминанием в 09:00; закончившийся / остановленный курс закрывает задачу.
Запись в таблицу здоровья первична: если задачу создать не вышло, запись остаётся (task_id пуст), в ответе task_linked=false.
Любое изменение, которое меняет срок, пересоздаёт задачу (старая закрывается или архивируется), а не правит её по месту.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from core.props import _date, _relation, _select, _status, _text, _title
from core.repos import pg_cat_health_repo as repo
from core.repos import pg_cats_repo
from core.user_manager import get_user_id
from nexus.repos.pg_tasks_repo import PgTasksRepo

logger = logging.getLogger("cat_health")

REMIND_HOUR = 9
CAT_CATEGORY = "🐾 Коты"
NotFound = repo.NotFound

_tasks = PgTasksRepo()
_VISIT_ICON = {"vaccine": "💉", "deworm": "💊", "flea": "🐜"}
_TASK_KEYS = ("status", "visit_on", "next_due", "title", "kind")
_MED_KEYS = ("status", "starts_on", "days", "name", "dose", "times_per_day")


async def _owner(owner_tg: int) -> str:
    user_id = (await get_user_id(owner_tg)) or ""
    if not user_id:
        raise NotFound("owner")
    return user_id


async def _today_tz(owner_tg: int):
    from miniapp.backend._helpers import today_user_tz
    return await today_user_tz(owner_tg)


def _local_dt(day: date, tz: int, hour: int = REMIND_HOUR) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone(timedelta(hours=tz)))


def visit_reminder(target: date, tz: int, now: datetime) -> Optional[str]:
    """Локальное 'YYYY-MM-DDTHH:MM': 09:00 накануне, а если оно уже прошло — 09:00 в сам день; прошло и это — нет напоминания."""
    for d in (target - timedelta(days=1), target):
        if _local_dt(d, tz) > now:
            return d.strftime("%Y-%m-%d") + "T%02d:00" % REMIND_HOUR
    return None


def med_reminder(first_day: date, tz: int, now: datetime) -> str:
    """Первое напоминание курса: 09:00 в первый день, а если оно уже прошло — 09:00 следующего дня."""
    d = first_day
    while _local_dt(d, tz) <= now:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d") + "T%02d:00" % REMIND_HOUR


async def _make_task(owner_tg: int, user_id: str, title: str, cat_id: int, tz: int, *, deadline: Optional[date],
                     reminder_local: Optional[str], daily: bool = False, note: Optional[str] = None) -> Optional[int]:
    """Создать задачу-напоминание про кота. None при любом сбое (запись здоровья от этого не страдает)."""
    try:
        props: Dict[str, Any] = {"Задача": _title(title), "Статус": _status("Not started"), "Категория": _select(CAT_CATEGORY),
                                 "🪪 Пользователи": _relation(user_id)}
        if deadline:
            props["Дедлайн"] = _date(deadline.isoformat())
        if reminder_local:
            sign = "+" if tz >= 0 else "-"
            props["Напоминание"] = _date(f"{reminder_local}:00{sign}{abs(tz):02d}:00")
        if note:
            props["Заметка"] = _text(note)
        pid = await _tasks.create("", props)
        if not pid:
            return None
        await _tasks.set_cat(pid, cat_id)
        if daily:
            await _tasks.set_repeat_fields(pid, "Ежедневно", None, "%02d:00" % REMIND_HOUR)
        if reminder_local:
            from miniapp.backend.routes.writes import _reschedule_task_reminder
            await _reschedule_task_reminder(pid, owner_tg, title, reminder_local, tz)
        return int(pid)
    except Exception:
        logger.warning("cat health: task create failed", exc_info=True)
        return None


async def _close_task(task_id: Optional[int], *, done: bool) -> None:
    """Закрыть связанную задачу: выполненную (с записью в журнал) или архивную. Снимает запланированные напоминания. Не бросает."""
    if not task_id:
        return
    try:
        from miniapp.backend.routes.writes import _cancel_task_jobs
        if done:
            await _tasks.set_status(str(task_id), "Done")
        else:
            await _tasks.set_archived(str(task_id))
        _cancel_task_jobs(str(task_id))
    except Exception:
        logger.warning("cat health: task close failed", exc_info=True)


def _cat_name(user_id: str, cat_id: int) -> str:
    c = pg_cats_repo.get_cat(user_id, cat_id)
    return (c or {}).get("name") or "кот"


# ── вес ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def add_weight(owner_tg: int, cat_id: int, grams: int, on: Optional[date] = None, note: Optional[str] = None) -> Dict[str, Any]:
    user_id = await _owner(owner_tg)
    today, _ = await _today_tz(owner_tg)
    return await asyncio.to_thread(repo.add_weight, user_id, cat_id, on or today, grams, note)


# ── визиты / прививки ─────────────────────────────────────────────────────────────────────────────────────────────────

def _visit_target(v: Dict[str, Any]) -> Optional[date]:
    if v["status"] == "planned":
        return v["visit_on"]
    if v["status"] == "done":
        return v["next_due"]
    return None


def _visit_title(v: Dict[str, Any], name: str) -> str:
    base = f"{_VISIT_ICON.get(v['kind'], '🩺')} {name}: {v['title']}"
    return base + (" (следующий срок)" if v["status"] == "done" else "")


async def _sync_visit(owner_tg: int, user_id: str, v: Dict[str, Any]) -> Dict[str, Any]:
    """Привести задачу визита в соответствие со строкой: закрыть старую, создать новую на ближайший срок."""
    today, tz = await _today_tz(owner_tg)
    await _close_task(v.get("task_id"), done=False)
    task_id: Optional[int] = None
    target = _visit_target(v)
    if target is not None:
        name = await asyncio.to_thread(_cat_name, user_id, v["cat_id"])
        rem = visit_reminder(target, tz, datetime.now(timezone.utc))
        task_id = await _make_task(owner_tg, user_id, _visit_title(v, name), v["cat_id"], tz, deadline=target, reminder_local=rem)
    await asyncio.to_thread(repo.set_visit_task, user_id, v["id"], task_id)
    return {**v, "task_id": task_id, "task_linked": task_id is not None}


async def add_visit(owner_tg: int, cat_id: int, title: str, visit_on: date, **kw: Any) -> Dict[str, Any]:
    user_id = await _owner(owner_tg)
    v = await asyncio.to_thread(repo.add_visit, user_id, cat_id, title, visit_on, **kw)
    return await _sync_visit(owner_tg, user_id, v)


async def update_visit(owner_tg: int, visit_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user_id = await _owner(owner_tg)
    before = await asyncio.to_thread(repo.get_visit, user_id, visit_id)
    if before is None:
        return None
    v = await asyncio.to_thread(repo.update_visit, user_id, visit_id, values)
    if v is None:
        return None
    if any(before[k] != v[k] for k in _TASK_KEYS) or (v["task_id"] is None and _visit_target(v) is not None):
        if before["status"] == "planned" and v["status"] == "done":
            await _close_task(v.get("task_id"), done=True)              # состоявшийся визит: его задача выполнена, новая — на следующий срок
            v = {**v, "task_id": None}
        return await _sync_visit(owner_tg, user_id, v)
    return {**v, "task_linked": v["task_id"] is not None}


async def delete_visit(owner_tg: int, visit_id: int) -> bool:
    user_id = await _owner(owner_tg)
    gone = await asyncio.to_thread(repo.delete_visit, user_id, visit_id)
    if gone is None:
        return False
    await _close_task(gone.get("task_id"), done=False)
    return True


# ── курсы лекарств ────────────────────────────────────────────────────────────────────────────────────────────────────

def _med_title(m: Dict[str, Any], name: str) -> str:
    t = f"💊 {name}: {m['name']}"
    if m.get("dose"):
        t += f" — {m['dose']}"
    if m["times_per_day"] > 1:
        t += f" ({m['times_per_day']} р/день)"
    return t


async def _sync_med(owner_tg: int, user_id: str, m: Dict[str, Any]) -> Dict[str, Any]:
    today, tz = await _today_tz(owner_tg)
    await _close_task(m.get("task_id"), done=(m["status"] == "done"))
    task_id: Optional[int] = None
    if m["status"] == "active" and (m["ends_on"] is None or m["ends_on"] >= today):
        rem = med_reminder(max(m["starts_on"], today), tz, datetime.now(timezone.utc))
        if m["ends_on"] is None or rem[:10] <= m["ends_on"].isoformat():
            name = await asyncio.to_thread(_cat_name, user_id, m["cat_id"])
            note = f"курс до {m['ends_on'].strftime('%d.%m')}" if m["ends_on"] else None
            task_id = await _make_task(owner_tg, user_id, _med_title(m, name), m["cat_id"], tz, deadline=None, reminder_local=rem, daily=True, note=note)
    await asyncio.to_thread(repo.set_med_task, user_id, m["id"], task_id)
    return {**m, "task_id": task_id, "task_linked": task_id is not None}


async def add_med(owner_tg: int, cat_id: int, name: str, starts_on: Optional[date] = None, **kw: Any) -> Dict[str, Any]:
    user_id = await _owner(owner_tg)
    today, _ = await _today_tz(owner_tg)
    m = await asyncio.to_thread(repo.add_med, user_id, cat_id, name, starts_on or today, **kw)
    return await _sync_med(owner_tg, user_id, m)


async def update_med(owner_tg: int, med_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user_id = await _owner(owner_tg)
    before = await asyncio.to_thread(repo.get_med, user_id, med_id)
    if before is None:
        return None
    m = await asyncio.to_thread(repo.update_med, user_id, med_id, values)
    if m is None:
        return None
    if any(before[k] != m[k] for k in _MED_KEYS) or (m["task_id"] is None and m["status"] == "active"):
        return await _sync_med(owner_tg, user_id, m)
    return {**m, "task_linked": m["task_id"] is not None}


async def delete_med(owner_tg: int, med_id: int) -> bool:
    user_id = await _owner(owner_tg)
    gone = await asyncio.to_thread(repo.delete_med, user_id, med_id)
    if gone is None:
        return False
    await _close_task(gone.get("task_id"), done=False)
    return True


async def finish_expired(owner_tg: int) -> int:
    """Закрыть курсы, которые закончились (конец раньше сегодняшнего дня), и их ежедневные задачи. Идемпотентно; вызывается при чтении."""
    user_id = await _owner(owner_tg)
    today, _ = await _today_tz(owner_tg)
    rows = await asyncio.to_thread(repo.expire_courses, user_id, today)
    for m in rows:
        await _close_task(m.get("task_id"), done=True)
        await asyncio.to_thread(repo.set_med_task, user_id, m["id"], None)
    return len(rows)
