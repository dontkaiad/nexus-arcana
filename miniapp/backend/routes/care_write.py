"""miniapp/backend/routes/care_write.py — ситтер выполняет цикл расписания / переносит его (отдельный write-токен).

• POST /api/care/tasks/{id}/done      {cycle_due, done_by} — закрыть ТЕКУЩИЙ цикл: запись в task_completions (идемпотентно по
                                       (task_id, cycle_due)), серия сдвигается от исходного срока (якорь), стрик задачи.
• POST /api/care/tasks/{id}/postpone  {cycle_due, new_due} — разовый перенос одного цикла без сдвига базы серии
                                       (task_overrides), один раз на цикл, не дальше начала следующего цикла и конца периода.
Работает только пока идёт период ухода и только для задач владельца с for_sitter. Токен открывает только эти два маршрута.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import care
from core.location import get_user_tz
from core.props import _date
from core.recurrence import next_cycle_anchored, parse_repeat_time
from core.user_manager import get_user_id
from miniapp.backend.care_write_auth import care_write_gate
from nexus.repos.pg_tasks_repo import PgTasksRepo, Task, _set_status_sync

logger = logging.getLogger("miniapp.care_write")

router = APIRouter()
_repo = PgTasksRepo()
TOLERANCE = timedelta(seconds=1)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _is_repeating(task: Task) -> bool:
    return (task.repeat or "Нет") not in ("Нет", "")


async def _load(task_id: int, owner_tg: int) -> Tuple[str, int, Task, datetime]:
    """Задача владельца в расписании ситтера, открытая, период идёт. Иначе 404 / 409."""
    user_id = (await get_user_id(owner_tg)) or ""
    task = await _repo.retrieve_page(str(task_id)) if user_id else None
    if not task or task.user_id != user_id or not task.for_sitter or task.status in ("Done", "Archived") or not task.reminder:
        raise HTTPException(status_code=404, detail="not found")
    tz = await get_user_tz(owner_tg)
    if not await asyncio.to_thread(care.is_active, user_id, tz):
        raise HTTPException(status_code=409, detail="care period is not active")
    return user_id, tz, task, _utc(datetime.fromisoformat(task.reminder))


class DoneBody(BaseModel):
    cycle_due: datetime
    done_by: str = Field("ситтер", max_length=40)


class PostponeBody(BaseModel):
    cycle_due: datetime
    new_due: datetime


async def _advance(task: Task, owner_tg: int, tz: int, cycle: datetime) -> Optional[datetime]:
    """Сдвинуть серию от исходного срока цикла (идемпотентно: результат зависит только от cycle). Для одноразовой задачи — Done."""
    if not _is_repeating(task):
        await asyncio.to_thread(_set_status_sync, task.id, "Done")
        try:
            from nexus.handlers.tasks import _remove_task_jobs
            _remove_task_jobs(task.id)
        except Exception:
            logger.warning("care: could not remove jobs for %s", task.id[:8], exc_info=True)
        return None
    _, ivl = parse_repeat_time(task.repeat_time or "")
    local = timezone(timedelta(hours=tz))
    nxt = next_cycle_anchored(cycle.astimezone(local), task.repeat, ivl, datetime.now(timezone.utc)).replace(microsecond=0)
    # Без микросекунд: PgTasksRepo._parse_iso их не понимает и молча записал бы NULL (срок задачи пропал бы).
    await _repo.set_props(task.id, {"Напоминание": _date(nxt.isoformat()),
                                    "Время завершения": _date(datetime.now(local).replace(microsecond=0).isoformat())})
    await _repo.set_in_progress(task.id)
    if task.deadline:
        try:
            old_dl = datetime.fromisoformat(task.deadline.replace("Z", "+00:00"))
            await _repo.set_props(task.id, {"Дедлайн": _date((old_dl + (nxt - cycle.astimezone(local))).replace(microsecond=0).isoformat())})
        except Exception:
            logger.warning("care: could not shift deadline for %s", task.id[:8], exc_info=True)
    try:
        from miniapp.backend.routes.writes import _reschedule_task_reminder
        await _reschedule_task_reminder(task.id, owner_tg, task.title, nxt.strftime("%Y-%m-%dT%H:%M"), tz)
    except Exception:
        logger.warning("care: reschedule failed for %s", task.id[:8], exc_info=True)
    try:
        from core.task_streaks import update_task_streak
        update_task_streak(user_id=owner_tg, task_id=task.id, task_title=task.title, repeat_kind=task.repeat,
                           today_local=datetime.now(local).date().isoformat(), interval_days=ivl)
    except Exception:
        logger.warning("care: streak update failed for %s", task.id[:8], exc_info=True)
    return nxt


@router.post("/care/tasks/{task_id}/done")
async def care_done(task_id: int, body: DoneBody, owner_tg: int = Depends(care_write_gate)) -> Dict[str, Any]:
    _, tz, task, cur = await _load(task_id, owner_tg)
    cycle = _utc(body.cycle_due)
    if abs(cur - cycle) > TOLERANCE:
        # серия уже ушла вперёд: повтор того же нажатия — не ошибка, но и не двойное выполнение
        if await asyncio.to_thread(care.cycle_done_exists, task_id, cycle):
            return {"ok": True, "dup": True, "next_due": _iso(cur)}
        raise HTTPException(status_code=409, detail="stale cycle")
    inserted = await asyncio.to_thread(care.record_cycle_done, task_id, body.done_by, cur)
    nxt = await _advance(task, owner_tg, tz, cur)      # и при дубле: если прошлая попытка не успела сдвинуть серию, доводим
    return {"ok": True, "dup": not inserted, "next_due": _iso(nxt)}


@router.post("/care/tasks/{task_id}/postpone")
async def care_postpone(task_id: int, body: PostponeBody, owner_tg: int = Depends(care_write_gate)) -> Dict[str, Any]:
    user_id, tz, task, cur = await _load(task_id, owner_tg)
    cycle, new_due = _utc(body.cycle_due), _utc(body.new_due)
    if abs(cur - cycle) > TOLERANCE or await asyncio.to_thread(care.cycle_done_exists, task_id, cur):
        raise HTTPException(status_code=409, detail="stale cycle")
    now = datetime.now(timezone.utc)
    if new_due <= now or new_due <= cur:
        raise HTTPException(status_code=400, detail="new_due must be in the future and later than the cycle")
    if _is_repeating(task):
        _, ivl = parse_repeat_time(task.repeat_time or "")
        limit = next_cycle_anchored(cur.astimezone(timezone(timedelta(hours=tz))), task.repeat, ivl, cur)
    else:
        limit = cur + timedelta(hours=24)
    period = await asyncio.to_thread(care.get_period, user_id)
    period_end = datetime(period["ends_on"].year, period["ends_on"].month, period["ends_on"].day,
                          tzinfo=timezone(timedelta(hours=tz))) + timedelta(days=1)
    if new_due >= limit or new_due >= period_end:
        raise HTTPException(status_code=400, detail="new_due must be before the next cycle and inside the care period")
    if not await asyncio.to_thread(care.add_override, task_id, cur, new_due, "ситтер"):
        raise HTTPException(status_code=409, detail="already postponed")
    return {"ok": True, "cycle_due": _iso(cur), "due_at": _iso(new_due)}
