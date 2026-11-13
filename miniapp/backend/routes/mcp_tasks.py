"""miniapp/backend/routes/mcp_tasks.py — запись задач для MCP-сервера kai-mcp (ADR-0039).

Два НОВЫХ маршрута под шлюзом `mcp_write_gate` (Bearer обязателен): создать задачу и перенести срок. Существующие
`POST /api/tasks`, `/api/tasks/{id}/edit`, `/postpone` не трогаются и по этому токену закрыты.

Задача создаётся и планируется так же, как ботом (`_do_save_task` в nexus/handlers/tasks.py): запись в PG через PgTasksRepo,
затем напоминание в APScheduler, а если напоминания нет, дедлайн-пинг. Владелец всегда MCP_WRITE_OWNER_TG_ID, for_sitter
ложь, повтора нет. Перенос меняет только срок (дата, время) и напоминание; название, заметку, статус и остальное эти маршруты
не умеют менять, а удалять не умеют совсем (тело запроса с лишними полями отклоняется: extra=forbid).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from core import mcp_tasks as rules
from core.bot_notify import notify_log_group
from core.config import config
from core.props import _date, _relation, _select, _status, _title
from core.user_manager import get_user_id
from miniapp.backend._helpers import today_user_tz
from miniapp.backend.auth import current_user_id
from miniapp.backend.mcp_write_auth import mcp_write_gate
from nexus.repos import pg_tasks_repo
from nexus.repos.pg_tasks_repo import PgTasksRepo

logger = logging.getLogger("miniapp.mcp_tasks")

router = APIRouter()
_repo = PgTasksRepo()
_create_limiter = rules.DailyLimiter(rules.CREATE_LIMIT_PER_DAY)
_reschedule_limiter = rules.DailyLimiter(rules.RESCHEDULE_LIMIT_PER_DAY)

_CLOSED_STATUSES = ("Done", "Complete", "Archived")


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=rules.TITLE_MAX * 2)
    deadline_date: str
    deadline_time: Optional[str] = None
    reminder: Optional[str] = None
    prio: str = rules.DEFAULT_PRIO
    category: str = rules.DEFAULT_CATEGORY


class RescheduleBody(BaseModel):
    """Только срок и напоминание. Любое другое поле (название, заметка, статус, …) даёт 422."""
    model_config = ConfigDict(extra="forbid")
    deadline_date: str
    deadline_time: Optional[str] = None
    reminder: Optional[str] = None


def _http(err: rules.McpTaskError) -> HTTPException:
    return HTTPException(status_code=err.status, detail=err.detail())


def _limited(limiter: rules.DailyLimiter, what: str) -> None:
    wait = limiter.reserve()
    if wait:
        raise HTTPException(status_code=429, detail={"code": "daily_limit", "message": f"daily limit of {limiter.limit} {what} reached"},
                            headers={"Retry-After": str(wait)})


def _now_local(tz_offset: int) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).replace(tzinfo=None, microsecond=0)


def _aware(naive: datetime, tz_offset: int) -> str:
    return naive.replace(tzinfo=timezone(timedelta(hours=tz_offset))).isoformat()


def _local_naive(iso: str, tz_offset: int) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=tz_offset))).replace(tzinfo=None, second=0, microsecond=0)


async def _schedule(tg_id: int, title: str, task_id: str, tz_offset: int, *, reminder_local: Optional[str],
                    deadline_local: Optional[str], duration_min: Optional[int] = None) -> None:
    """Как бот: снять старые jobs, затем напоминание, а если его нет, дедлайн-пинг. Сбой планирования не валит запись:
    колонки в БД первичны, restore_reminders_on_startup пересоберёт jobs. Никогда не бросает."""
    try:
        from nexus.handlers.tasks import _remove_task_jobs, _schedule_deadline_check, _schedule_reminder
        _remove_task_jobs(task_id)
        if reminder_local:
            await _schedule_reminder(tg_id, title, reminder_local, task_id, tz_offset)
        elif deadline_local:
            await _schedule_deadline_check(tg_id, title, deadline_local, task_id, tz_offset, duration_min=duration_min)
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp tasks: scheduling failed for %s: %s", (task_id or "?")[:8], e)


async def _alert(text: str) -> None:
    """В топик лог-группы тем же общим лог-ботом, что и остальные алерты. Не бросает."""
    try:
        await notify_log_group(text, config.log_thread_kaimcp or config.log_thread_nexus)
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp tasks: alert failed: %s", e)


def _task_values(task: Any, tz_offset: int) -> dict:
    """Срок и напоминание задачи в виде, который видит владелец: дата, время (None для срока «на весь день»), напоминание."""
    dl = _local_naive(task.deadline, tz_offset)
    rem = _local_naive(task.reminder, tz_offset)
    all_day = bool(getattr(task, "deadline_all_day", False))
    return {
        "deadline": dl.date().isoformat() if dl else None,
        "deadline_time": (dl.strftime("%H:%M") if dl and not all_day else None),
        "reminder": rem.strftime("%Y-%m-%dT%H:%M") if rem else None,
    }


# ── POST /api/mcp/tasks ──────────────────────────────────────────────────────

@router.post("/mcp/tasks", status_code=201, dependencies=[Depends(mcp_write_gate)])
async def mcp_create_task(body: CreateBody, tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_id = await get_user_id(tg_id)
    if not user_id:
        # tasks с пустым user_id видны всем пользователям (busy-калькулятор берёт user_id IN (…, '')): без владельца не пишем
        raise HTTPException(status_code=409, detail={"code": "owner_unknown", "message": "the owner has no user id"})
    today_date, tz_offset = await today_user_tz(tg_id)
    now_local = _now_local(tz_offset)
    try:
        title = rules.clean_title(body.title)
        d = rules.parse_date(body.deadline_date)
        rules.check_date_window(d, today_date)
        hhmm = rules.parse_time(body.deadline_time)
        rem = rules.parse_reminder(body.reminder)
        if rem is not None:
            rules.check_reminder(rem, d, hhmm, now_local, must_be_future=True)
        await asyncio.to_thread(pg_tasks_repo._ensure_lookups)
        category = rules.pick_category(body.category, list(pg_tasks_repo._category_id))
        priority = rules.pick_priority(body.prio, list(pg_tasks_repo._priority_id))
    except rules.McpTaskError as e:
        raise _http(e)

    deadline_iso = _aware(datetime(d.year, d.month, d.day, int(hhmm[:2]), int(hhmm[3:])), tz_offset) if hhmm else d.isoformat()
    props: dict = {
        "Задача": _title(title),
        "Статус": _status("Not started"),
        "Приоритет": _select(priority),
        "Категория": _select(category),
        "Дедлайн": _date(deadline_iso),
        "🪪 Пользователи": _relation(user_id),
    }
    if rem is not None:
        props["Напоминание"] = _date(_aware(rem, tz_offset))

    _limited(_create_limiter, "created tasks")
    try:
        task_id = await _repo.create("", props)
    except Exception:
        _create_limiter.release()
        raise
    if not task_id:
        _create_limiter.release()
        raise HTTPException(status_code=500, detail={"code": "create_failed", "message": "failed to create the task"})

    deadline_local = f"{d.isoformat()}T{hhmm}" if hhmm else f"{d.isoformat()}T09:00"
    await _schedule(tg_id, title, task_id, tz_offset,
                    reminder_local=rem.strftime("%Y-%m-%dT%H:%M") if rem else None, deadline_local=deadline_local)
    await _alert(rules.alert_created(title, d.isoformat(), hhmm))
    return {
        "id": task_id,
        "title": title,
        "deadline": d.isoformat(),
        "deadline_time": hhmm,
        "reminder": rem.strftime("%Y-%m-%dT%H:%M") if rem else None,
        "prio": body.prio.replace("\ufe0f", ""),
        "category": category,
    }


# ── POST /api/mcp/tasks/{id}/reschedule ──────────────────────────────────────

@router.post("/mcp/tasks/{task_id}/reschedule", dependencies=[Depends(mcp_write_gate)])
async def mcp_reschedule_task(
    body: RescheduleBody,
    task_id: str = Path(..., pattern=r"^\d{1,18}$"),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = await get_user_id(tg_id)
    if not user_id:
        raise HTTPException(status_code=409, detail={"code": "owner_unknown", "message": "the owner has no user id"})
    task = await _repo.retrieve_page(task_id)
    if task is None or task.user_id != user_id:                      # чужая и несуществующая выглядят одинаково
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "task not found"})
    if task.status in _CLOSED_STATUSES or getattr(task, "archived", False):
        raise HTTPException(status_code=409, detail={"code": "task_closed", "message": "only active or overdue tasks can be moved"})
    if getattr(task, "for_sitter", False):
        raise HTTPException(status_code=409, detail={"code": "sitter_task", "message": "tasks of the sitter schedule are moved in the app"})
    if (task.repeat or "Нет").strip() not in ("", "Нет"):
        raise HTTPException(status_code=409, detail={"code": "recurring_task", "message": "recurring tasks cannot be moved from here"})
    if getattr(task, "cat_id", None) is not None:
        raise HTTPException(status_code=409, detail={"code": "cat_task", "message": "tasks of the cat module are moved in the app"})
    try:
        from core.booking.repo import get_booking_by_link
        linked = await get_booking_by_link(nexus_task_id=task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp tasks: booking link check failed for %s: %s", task_id[:8], e)
        raise HTTPException(status_code=503, detail={"code": "booking_check_failed", "message": "could not check the booking link, try later"})
    if linked is not None:
        # перенос задачи из бота двигает бронь и пишет другу через Zarya; отсюда этого не делаем
        raise HTTPException(status_code=409, detail={"code": "booking_task", "message": "a task linked to a booking is moved in the app"})

    today_date, tz_offset = await today_user_tz(tg_id)
    now_local = _now_local(tz_offset)
    old = _task_values(task, tz_offset)
    old_all_day = bool(getattr(task, "deadline_all_day", False)) or not old["deadline_time"]
    try:
        d = rules.parse_date(body.deadline_date)
        rules.check_date_window(d, today_date)
        # время не названо: у задачи со временем остаётся прежнее время суток, у задачи «на весь день» срок остаётся на весь день
        hhmm = rules.parse_time(body.deadline_time) or (None if old_all_day else old["deadline_time"])
        rem_new = rules.parse_reminder(body.reminder)
        if rem_new is not None:
            rules.check_reminder(rem_new, d, hhmm, now_local, must_be_future=True)
            rem_final = rem_new
        else:
            rem_final = _local_naive(task.reminder, tz_offset)
            if rem_final is not None:                              # прежнее напоминание не должно оказаться позже нового срока
                rules.check_reminder(rem_final, d, hhmm, now_local, must_be_future=False)
    except rules.McpTaskError as e:
        raise _http(e)

    deadline_iso = _aware(datetime(d.year, d.month, d.day, int(hhmm[:2]), int(hhmm[3:])), tz_offset) if hhmm else d.isoformat()
    props: dict = {"Дедлайн": _date(deadline_iso)}
    if rem_new is not None:
        props["Напоминание"] = _date(_aware(rem_new, tz_offset))

    _limited(_reschedule_limiter, "reschedules")
    try:
        await _repo.set_props(task_id, props)
    except Exception:
        _reschedule_limiter.release()
        raise HTTPException(status_code=500, detail={"code": "update_failed", "message": "failed to update the task"})

    future_rem = rem_final if (rem_final is not None and rem_final > now_local) else None
    deadline_local = f"{d.isoformat()}T{hhmm}" if hhmm else f"{d.isoformat()}T09:00"
    await _schedule(tg_id, task.title, task_id, tz_offset,
                    reminder_local=future_rem.strftime("%Y-%m-%dT%H:%M") if future_rem else None,
                    deadline_local=deadline_local, duration_min=task.duration_min)
    new = {"deadline": d.isoformat(), "deadline_time": hhmm,
           "reminder": rem_final.strftime("%Y-%m-%dT%H:%M") if rem_final else None}
    await _alert(rules.alert_rescheduled(task_id, task.title,
                                          (old["deadline"], old["deadline_time"]), (new["deadline"], new["deadline_time"])))
    return {"id": task_id, "title": task.title, "old": old, "new": new}
