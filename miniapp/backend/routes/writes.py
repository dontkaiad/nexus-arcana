"""miniapp/backend/routes/writes.py — POST-endpoints для wave 3.

Все эндпоинты требуют initData (current_user_id). Общий подход:
- проверяем, что страница принадлежит юзеру (по 🪪 Пользователи relation),
  иначе 404 (не выдаём подсказок о существовании чужих записей).
- принимаем только поля, известные из Notion-схемы. Всё остальное игнорируем.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape as _esc
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from core.props import _title, _text, _select, _status, _number, _date, _relation
from core.repos.finance_repo import FinanceRepo
from core.user_manager import get_user_id
from core.bot_notify import notify_user, clear_task_reminder

from arcana.repos.pg_rituals_repo import PgRitualsRepo as _PgRitualsRepoClass
_rituals_pg_repo = _PgRitualsRepoClass()
from arcana.repos.pg_works_repo import PgWorksRepo as _PgWorksRepoClass
_works_pg_repo = _PgWorksRepoClass()

from arcana.repos.grimoire_repo import GrimoireRepo as _GrimoireRepoClass
_grimoire_repo = _GrimoireRepoClass()

# Emoji-приоритет из фронта (PRIOS) → лейбл для PgWorksRepo.create (#203)
_WORK_PRIO_LABEL = {"🔴": "Срочно", "🟡": "Важно", "⚪": "Можно потом"}
from core.repos.pg_nexus_lists_repo import (
    PgNexusListsRepo as _PgNexusListsRepoClass,
    PgArcanaInventoryRepo as _PgArcanaInventoryRepoClass,
)
_nexus_lists_repo = _PgNexusListsRepoClass()
_arcana_inv_repo = _PgArcanaInventoryRepoClass()

from arcana.repos.pg_sessions_repo import PgSessionsRepo as _PgSessionsRepoClass
_sessions_pg_repo = _PgSessionsRepoClass()

from arcana.repos.clients_repo import ClientsRepo as _ClientsRepoClass
from arcana.repos.pg_clients_repo import PgClientsRepo as _PgClientsRepoClass
_clients_repo = _ClientsRepoClass()
_pg_clients_repo = _PgClientsRepoClass()

# Notion label → PG type code for client create/edit
_CLIENT_TYPE_TO_CODE = {
    "🤝 Платный":   "paid",
    "🎁 Бесплатный": "free",
}

from nexus.repos.pg_tasks_repo import PgTasksRepo as _PgTasksRepoClass, Task as _PgTask
_tasks_pg_repo = _PgTasksRepoClass()

from core.repos.pg_memory_repo import PgMemoryRepo as _PgMemoryRepoClass
_memory_repo = _PgMemoryRepoClass()
_fin_repo = FinanceRepo()

from miniapp.backend.auth import current_user_id
from core.repos.idempotency_repo import idempotent
from miniapp.backend._helpers import (
    BOT_NEXUS,
    extract_time,
    today_user_tz,
)

logger = logging.getLogger("miniapp.writes")

router = APIRouter()


# ── Ownership check (PG tasks) ───────────────────────────────────────────────

async def _load_owned_task(task_id: str, user_id: str) -> _PgTask:
    """Загружает задачу из PG и проверяет владение. 404 если нет доступа."""
    try:
        task = await _tasks_pg_repo.retrieve_page(task_id)
    except Exception as e:
        logger.warning("retrieve_page failed for %s: %s", task_id[:8] if task_id else "?", e)
        raise HTTPException(status_code=404, detail="not found")
    if not task:
        raise HTTPException(status_code=404, detail="not found")
    if user_id and task.user_id and task.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    return task


async def _reschedule_task_reminder(
    task_id: str, tg_id: int, title: str, local_dt: str, tz_offset: int,
) -> None:
    """Перепланировать APScheduler-job напоминания задачи в текущем процессе.

    miniapp backend поднимается внутри процесса Nexus-бота (nexus_bot.py),
    поэтому write-эндпоинт дотягивается до общего AsyncIOScheduler и
    напоминание срабатывает без рестарта. `local_dt` — наивное локальное
    'YYYY-MM-DDTHH:MM'; `_schedule_reminder` сам клеит tz_offset и
    replace_existing'ит старый job. Никогда не бросает — провал
    планирования не должен валить write-действие (на худой конец job
    восстановится через restore_reminders_on_startup).
    """
    try:
        from nexus.handlers.tasks import _schedule_reminder
        await _schedule_reminder(tg_id, title, local_dt, task_id, tz_offset)
    except Exception as e:
        logger.warning("live reminder reschedule failed for %s: %s", (task_id or "?")[:8], e)


def _cancel_task_jobs(task_id: str) -> None:
    """Снять APScheduler jobs (reminder_/deadline_) задачи в общем процессе.

    Зеркалит то, что делает бот в task_complete/handle_task_cancel
    (nexus/handlers/tasks.py) — Mini App раньше этого не делала, из-за чего
    задача, закрытая ДО срабатывания напоминания/дедлайна, всё равно потом
    пинговала (#73 закрыл только уже-отправленную плашку в чате, сам job
    оставался живым). Per TASKS.md: reminder/deadline — projections, job
    disposable — можно смело снимать, при рестарте пересоберётся из колонок
    если что. Никогда не бросает.
    """
    try:
        from nexus.handlers.tasks import _remove_task_jobs
        _remove_task_jobs(task_id)
    except Exception as e:
        logger.warning("cancel scheduler jobs failed for %s: %s", (task_id or "?")[:8], e)


# ═══════════════════════════════════════════════════════════════
# TASKS
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/done")
async def task_done(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    task = await _load_owned_task(task_id, user_id)
    # Повторяющаяся задача (есть «Время повтора») → In progress, не Done.
    repeat_time = (task.repeat_time or "").strip()
    repeat_kind = task.repeat if task.repeat not in ("Нет", None) else ""
    is_repeating = bool(repeat_time or repeat_kind)
    new_status = "In progress" if repeat_time else "Done"
    ok = await _tasks_pg_repo.set_status(task_id, new_status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    now_utc = datetime.now(timezone.utc)
    try:
        await _tasks_pg_repo.set_props(task_id, {"Время завершения": _date(now_utc.isoformat())})
    except Exception as e:
        logger.warning("could not set completion time: %s", e)
    today_local, tz_offset = await today_user_tz(tg_id)
    if is_repeating:
        try:
            from core.task_streaks import update_task_streak
            update_task_streak(
                user_id=tg_id,
                task_id=task_id,
                task_title=task.title,
                repeat_kind=repeat_kind or "Каждый день",
                today_local=today_local.isoformat(),
            )
        except Exception as e:
            logger.warning("update_task_streak failed: %s", e)
    # #38 fix per TASKS_SPEC: глобальный дневной стрик инкрементируется
    # от ЛЮБОЙ Done-задачи (повторяющейся или нет).
    try:
        from nexus.handlers.streaks import update_streak
        await update_streak(
            tg_id, tz_offset,
            source="miniapp_task_done",
            task_id=task_id,
        )
    except Exception as e:
        logger.warning("update_streak failed: %s", e)
    verb = "🔄 Отметила" if is_repeating else "✅ Готово"
    await notify_user(tg_id, f"{verb}: <b>{_esc(task.title)}</b>", bot="nexus")
    # #73: погасить живую плашку-напоминание этой задачи в чате (если висит).
    await clear_task_reminder(task_id, bot="nexus")
    if not is_repeating:
        # Одноразовая задача реально завершена (Done) — снимаем ещё не
        # сработавшие reminder_/deadline_ jobs, иначе они всё равно
        # пингуют позже. Повторяющиеся (→ In progress) job не трогаем: это
        # штатное ожидание дедлайн-этапа (см. TASKS.md "complete").
        _cancel_task_jobs(task_id)
    return {"ok": True, "status": new_status}


@router.post("/tasks/{task_id}/reopen")
async def task_reopen(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    task = await _load_owned_task(task_id, user_id)
    ok = await _tasks_pg_repo.set_status(task_id, "Not started")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    await notify_user(tg_id, f"↩️ Снова активна: <b>{_esc(task.title)}</b>", bot="nexus")
    return {"ok": True}


class PostponeBody(BaseModel):
    days: Optional[int] = Field(default=None, ge=1, le=365)
    date: Optional[str] = None  # YYYY-MM-DD, абсолютная новая дата дедлайна
    time: Optional[str] = None  # HH:MM, время напоминания (локальное)


@router.post("/tasks/{task_id}/postpone")
async def task_postpone(
    task_id: str,
    body: PostponeBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    task = await _load_owned_task(task_id, user_id)
    today_date, tz_offset = await today_user_tz(tg_id)

    if body.date:
        try:
            new_date = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date, expected YYYY-MM-DD")
    else:
        base = None
        if task.deadline:
            try:
                base = datetime.fromisoformat(task.deadline.replace("Z", "+00:00")).date()
            except ValueError:
                base = None
        if not base:
            base = today_date
        shift_days = body.days if body.days is not None else 1
        new_date = base + timedelta(days=shift_days)

    try:
        await _tasks_pg_repo.set_props(task_id, {"Дедлайн": _date(new_date.isoformat())})
    except Exception as e:
        logger.warning("could not set deadline: %s", e)
        raise HTTPException(status_code=500, detail="failed to update deadline")

    remind_iso: Optional[str] = None
    if body.time:
        try:
            hh, mm = body.time.split(":")
            tz = timezone(timedelta(hours=tz_offset))
            remind_dt = datetime(new_date.year, new_date.month, new_date.day, int(hh), int(mm), tzinfo=tz)
            remind_iso = remind_dt.isoformat()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid time, expected HH:MM")
        try:
            await _tasks_pg_repo.set_props(task_id, {"Напоминание": _date(remind_iso)})
        except Exception as e:
            logger.warning("could not set reminder: %s", e)
        await _reschedule_task_reminder(
            task_id, tg_id, task.title,
            f"{new_date.isoformat()}T{int(hh):02d}:{int(mm):02d}", tz_offset,
        )

    await notify_user(
        tg_id,
        f"📅 Перенесла на {new_date.isoformat()}: <b>{_esc(task.title)}</b>",
        bot="nexus",
    )
    return {"ok": True, "new_date": new_date.isoformat(), "reminder": remind_iso}


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    task = await _load_owned_task(task_id, user_id)
    ok = await _tasks_pg_repo.set_status(task_id, "Archived")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to cancel")
    await notify_user(tg_id, f"❌ Отменила: <b>{_esc(task.title)}</b>", bot="nexus")
    # Отменённая задача не должна пинговать напоминанием/дедлайном позже
    # (симметрично боту, см. handle_task_cancel).
    await clear_task_reminder(task_id, bot="nexus")
    _cancel_task_jobs(task_id)
    return {"ok": True}


class TaskCreateBody(BaseModel):
    title: str
    cat: Optional[str] = None
    prio: Optional[str] = None
    date: Optional[str] = None


class TaskEditBody(BaseModel):
    title: Optional[str] = None
    cat: Optional[str] = None
    prio: Optional[str] = None
    date: Optional[str] = None           # YYYY-MM-DD — дедлайн
    deadline_time: Optional[str] = None  # HH:MM — время дедлайна (см. task_edit)
    time: Optional[str] = None           # HH:MM — время напоминания
    reminder_date: Optional[str] = None  # YYYY-MM-DD — дата напоминания (независимо от дедлайна)


@router.post("/tasks/{task_id}/edit")
async def task_edit(
    task_id: str,
    body: TaskEditBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    task = await _load_owned_task(task_id, user_id)
    _today_date, tz_offset = await today_user_tz(tg_id)

    props: dict = {}
    if body.title is not None and body.title.strip():
        props["Задача"] = _title(body.title.strip())
    if body.cat:
        props["Категория"] = _select(body.cat)
    if body.prio:
        props["Приоритет"] = _select(body.prio)
    if body.date:
        try:
            new_date = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date, expected YYYY-MM-DD")
        # Дедлайн в PG — TIMESTAMPTZ, "просто дата" не существует: любое
        # значение хранится с конкретным временем суток. Раньше сюда уходила
        # голая "YYYY-MM-DD" без offset'а — PgTasksRepo._parse_iso молча
        # трактовал полночь как UTC (тот же класс бага, что и в task_create
        # выше и в core/reply_update.py:_with_tz_suffix), а при чтении назад
        # эта UTC-полночь конвертировалась в локальный tz юзера — для
        # tz_offset=+5 полночь UTC показывалась как 05:00, и форма
        # редактирования эту "5 утра" не показывала и не давала поправить
        # (deadline_time вообще не было в контракте). Теперь: явное время
        # (deadline_time) → старое время текущего дедлайна (сохраняем,
        # если юзер поменял только дату) → 09:00 по умолчанию — и всегда
        # с явным offset'ом, чтобы _parse_iso не гадал.
        deadline_hhmm = body.deadline_time
        if not deadline_hhmm and task.deadline:
            deadline_hhmm = extract_time(task.deadline, tz_offset)
        deadline_hhmm = deadline_hhmm or "09:00"
        try:
            dl_h, dl_m = deadline_hhmm.split(":")
            dl_tz = timezone(timedelta(hours=tz_offset))
            dl_dt = datetime(new_date.year, new_date.month, new_date.day,
                              int(dl_h), int(dl_m), tzinfo=dl_tz)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid deadline_time, expected HH:MM")
        props["Дедлайн"] = _date(dl_dt.isoformat())

    # Напоминание редактируется независимо от дедлайна: дата берётся из
    # reminder_date, при его отсутствии — из дедлайна (обратная совместимость
    # со старым флоу «дата + время = напоминание на день дедлайна»).
    remind_anchor = body.reminder_date or body.date
    remind_local_dt: Optional[str] = None  # наивное локальное 'YYYY-MM-DDTHH:MM' для APScheduler
    if body.time and remind_anchor:
        try:
            rdate = datetime.strptime(remind_anchor, "%Y-%m-%d").date()
            hh, mm = body.time.split(":")
            tz = timezone(timedelta(hours=tz_offset))
            remind_dt = datetime(rdate.year, rdate.month, rdate.day, int(hh), int(mm), tzinfo=tz)
            props["Напоминание"] = _date(remind_dt.isoformat())
            remind_local_dt = f"{rdate.isoformat()}T{int(hh):02d}:{int(mm):02d}"
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid reminder, expected date YYYY-MM-DD + time HH:MM")

    if not props:
        return {"ok": True, "noop": True}

    try:
        await _tasks_pg_repo.set_props(task_id, props)
    except Exception as e:
        logger.error("task_edit set_props failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update task")
    new_title = (body.title or "").strip() or task.title
    # Перепланировать живой APScheduler-job: Nexus-бот и miniapp backend живут
    # в одном процессе, поэтому напоминание срабатывает сразу, без рестарта
    # (restore_reminders_on_startup нужен только для холодного старта).
    if remind_local_dt:
        await _reschedule_task_reminder(task_id, tg_id, new_title, remind_local_dt, tz_offset)
    await notify_user(tg_id, f"✏️ Изменила: <b>{_esc(new_title)}</b>", bot="nexus")
    return {"ok": True}


@router.post("/tasks")
async def task_create(
    body: TaskCreateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    # База задач — Nexus-only, поле "Бот" отсутствует в её схеме.
    props: dict = {
        "Задача": _title(body.title),
        "Статус": _status("Not started"),
    }
    if body.prio:
        props["Приоритет"] = _select(body.prio)
    if body.cat:
        props["Категория"] = _select(body.cat)
    if body.date:
        deadline_iso = body.date
        # TaskForm шлёт "YYYY-MM-DDTHH:MM:SS" (наивное локальное время юзера)
        # когда указаны и дата, и время — без явного offset'а PgTasksRepo.
        # _parse_iso молча трактует это как UTC (тот же класс бага, что и в
        # reply-правках, см. core/reply_update.py _with_tz_suffix).
        # Дата без времени ("YYYY-MM-DD") — offset не нужен, не трогаем.
        if "T" in deadline_iso and "+" not in deadline_iso and "Z" not in deadline_iso:
            _, tz_offset = await today_user_tz(tg_id)
            sign = "+" if tz_offset >= 0 else "-"
            deadline_iso = f"{deadline_iso}{sign}{abs(tz_offset):02d}:00"
        props["Дедлайн"] = _date(deadline_iso)
    if user_id:
        props["🪪 Пользователи"] = _relation(user_id)
    pg_id = await _tasks_pg_repo.create("", props)
    if not pg_id:
        raise HTTPException(status_code=500, detail="failed to create task")
    await notify_user(tg_id, f"➕ Создала задачу: <b>{_esc(body.title)}</b>", bot="nexus")
    return {"ok": True, "id": pg_id}


# ═══════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════

class ExpenseBody(BaseModel):
    amount: float = Field(gt=0)
    cat: str
    desc: str = ""
    bot: str = "nexus"


class FinanceBody(BaseModel):
    """Унифицированная форма финансов (wave5 §2.1).

    - type: expense | income | practice_income
    - amount: обязательное
    - cat: обязательно для expense; для income опционально ("Прочее" по умолчанию)
    - desc: опционально
    - bot: "nexus" | "arcana" (для practice_income всегда arcana)
    """
    type: str = Field(..., pattern="^(expense|income|practice_income)$")
    amount: float = Field(gt=0)
    cat: Optional[str] = None
    desc: str = ""
    bot: str = "nexus"


@router.post("/finance")
async def finance_create(
    body: FinanceBody,
    tg_id: int = Depends(current_user_id),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    today_date, _tz = await today_user_tz(tg_id)

    if body.type == "expense":
        if not body.cat:
            raise HTTPException(status_code=400, detail="cat is required for expense")
        type_label = "💸 Расход"
        category = body.cat
        bot_label = "🌒 Arcana" if body.bot == "arcana" else BOT_NEXUS
    elif body.type == "income":
        type_label = "💰 Доход"
        category = body.cat or "🏦 Прочее"
        bot_label = "🌒 Arcana" if body.bot == "arcana" else BOT_NEXUS
    else:  # practice_income
        type_label = "💰 Доход"
        category = body.cat or "🔮 Практика"
        bot_label = "🌒 Arcana"

    async def _run() -> dict:
        page_id = await _fin_repo.add(
            date=today_date.isoformat(),
            amount=body.amount,
            category=category,
            type_=type_label,
            source="💳 Карта",
            bot_label=bot_label,
            description=body.desc,
            user_id=user_id,
        )
        if not page_id:
            raise HTTPException(status_code=500, detail="failed to create finance entry")
        return {"ok": True, "id": page_id, "type": body.type}

    return await idempotent(tg_id, idempotency_key, _run)


# #123: 4 направления долгового движения → (kind в таблице debts, операция).
#   borrowed «заняла»      → i_owe,    создать обязательство
#   repaid   «вернула долг» → i_owe,    уменьшить/закрыть
#   lent     «дала в долг»  → they_owe, создать актив
#   received «мне вернули»  → they_owe, уменьшить/закрыть
# Все 4 живут в таблице `debts`, НЕ в финансовых транзакциях — в P&L и
# бюджет заёмные деньги не попадают (только i_owe active влияет на бюджет,
# core/budget.py:load_budget_data(kind="i_owe")).
_DEBT_DIRECTIONS = {
    "borrowed": ("i_owe", "create"),
    "repaid":   ("i_owe", "reduce"),
    "lent":     ("they_owe", "create"),
    "received": ("they_owe", "reduce"),
}


class DebtBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    deadline: str = ""
    direction: str = "borrowed"


class DebtCloseBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "i_owe"


@router.post("/finance/debt")
async def finance_debt_create(
    body: DebtBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Долговое движение из Mini App (#123). `direction` ∈ borrowed / repaid /
    lent / received. create → upsert в `debts`; reduce → частичное/полное
    погашение (404 если такого долга нет)."""
    from core.repos.pg_debts_repo import _repo as _debt_repo

    if body.direction not in _DEBT_DIRECTIONS:
        raise HTTPException(status_code=400, detail="bad direction")
    kind, op = _DEBT_DIRECTIONS[body.direction]
    user_id = (await get_user_id(tg_id)) or ""
    name = body.name.strip()
    amount = int(round(body.amount))

    try:
        if op == "create":
            await _debt_repo.upsert(
                user_id, name, kind,
                amount=float(amount), deadline=body.deadline.strip() or None,
            )
            result: dict[str, Any] = {"ok": True, "direction": body.direction, "kind": kind}
        else:
            res = await _debt_repo.reduce_amount(user_id, kind, name, float(amount))
            if res is None:
                raise HTTPException(status_code=404, detail="no such debt")
            new_amount, closed, overpaid = res
            result = {
                "ok": True, "direction": body.direction, "kind": kind,
                "closed": bool(closed),
                "remaining": int(round(max(0.0, new_amount))),
                "overpaid": int(round(overpaid)),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("finance_debt_create failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to save debt")

    a = f"{amount:,}".replace(",", " ")
    _MSG = {
        "borrowed": f"📋 Записала долг: <b>{_esc(name)} — {a}₽</b>",
        "lent":     f"🫴 Записала: <b>{_esc(name)}</b> должен(а) <b>{a}₽</b>",
        "repaid":   (f"🎉 Долг <b>{_esc(name)}</b> закрыт!" if result.get("closed")
                     else f"💰 Внесла {a}₽ за долг <b>{_esc(name)}</b>"),
        "received": (f"🎉 <b>{_esc(name)}</b> вернул(а) всё!" if result.get("closed")
                     else f"💰 <b>{_esc(name)}</b> вернул(а) {a}₽"),
    }[body.direction]
    await notify_user(tg_id, _MSG, bot="nexus")
    return result


@router.post("/finance/debt/close")
async def finance_debt_close(
    body: DebtCloseBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Закрыть долг целиком (#123): `is_active=False`. `kind` ∈ i_owe / they_owe."""
    from core.repos.pg_debts_repo import _repo as _debt_repo

    if body.kind not in ("i_owe", "they_owe"):
        raise HTTPException(status_code=400, detail="bad kind")
    user_id = (await get_user_id(tg_id)) or ""
    name = body.name.strip()
    try:
        found = await _debt_repo.deactivate(user_id, body.kind, name)
    except Exception as e:
        logger.error("finance_debt_close failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to close debt")
    if not found:
        raise HTTPException(status_code=404, detail="debt not found")
    verb = "Долг" if body.kind == "i_owe" else "Долг передо мной"
    await notify_user(tg_id, f"🎉 {verb} <b>{_esc(name)}</b> закрыт!", bot="nexus")
    return {"ok": True, "name": name, "kind": body.kind}


class GoalBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target: float = Field(gt=0)
    monthly: float = Field(default=0, ge=0)
    prev_name: Optional[str] = None  # передан при переименовании


@router.post("/finance/goal")
async def finance_goal_upsert(
    body: GoalBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Создать / изменить 🎯 цель (Mini App, #44/#205). Цель — строка таблицы
    `goals`; правка ежемесячного взноса влияет на бюджет через
    `core/budget.py:load_budget_data`."""
    from core.repos.pg_goals_repo import _repo as _goals_repo

    user_id = (await get_user_id(tg_id)) or ""
    name = body.name.strip()
    target = int(round(body.target))
    monthly = int(round(body.monthly))

    # Переименование: старую строку закрываем (dropped), заводим новую.
    prev = (body.prev_name or "").strip()
    renamed = bool(prev and prev.lower() != name.lower())
    try:
        if renamed:
            await _goals_repo.set_status(user_id, prev, "dropped")
        await _goals_repo.upsert(user_id, name, target=float(target), monthly=float(monthly))
    except Exception as e:
        logger.error("finance_goal_upsert failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to save goal")

    verb = "Переименовала" if renamed else "Сохранила"
    target_disp = f"{target:,}".replace(",", " ")
    await notify_user(tg_id, f"🎯 {verb} цель: <b>{_esc(name)} — {target_disp}₽</b>", bot="nexus")
    return {"ok": True, "name": name, "target": target, "monthly": monthly}


class GoalCloseBody(BaseModel):
    name: str = Field(min_length=1)
    achieved: bool = False


@router.post("/finance/goal/close")
async def finance_goal_close(
    body: GoalCloseBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Закрыть цель (#44/#205): status → achieved | dropped."""
    from core.repos.pg_goals_repo import _repo as _goals_repo

    user_id = (await get_user_id(tg_id)) or ""
    name = body.name.strip()
    try:
        found = await _goals_repo.set_status(
            user_id, name, "achieved" if body.achieved else "dropped"
        )
    except Exception as e:
        logger.error("finance_goal_close failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to close goal")
    if not found:
        raise HTTPException(status_code=404, detail="goal not found")

    msg = "🎉 Цель достигнута!" if body.achieved else "✅ Цель убрана."
    await notify_user(tg_id, msg, bot="nexus")
    return {"ok": True, "name": name, "achieved": body.achieved}


class GoalContributeBody(BaseModel):
    name: str = Field(min_length=1)
    amount: float = Field(gt=0)


@router.post("/finance/goal/contribute")
async def finance_goal_contribute(
    body: GoalContributeBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """#205: ручной взнос в накопление цели (`goals.saved += amount`).
    Достигли `target` → цель авто-переходит в `achieved`."""
    from core.repos.pg_goals_repo import _repo as _goals_repo

    user_id = (await get_user_id(tg_id)) or ""
    name = body.name.strip()
    amount = int(round(body.amount))
    try:
        new_saved = await _goals_repo.add_saved(user_id, name, float(amount))
    except Exception as e:
        logger.error("finance_goal_contribute failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to contribute")
    if new_saved is None:
        raise HTTPException(status_code=404, detail="goal not found")
    a = f"{amount:,}".replace(",", " ")
    await notify_user(tg_id, f"🎯 В цель «{_esc(name)}»: <b>+{a}₽</b>", bot="nexus")
    return {"ok": True, "name": name, "saved": int(round(new_saved))}


class CushionTargetBody(BaseModel):
    target: Optional[float] = Field(default=None, ge=0)


@router.post("/finance/cushion/target")
async def finance_cushion_set_target(
    body: CushionTargetBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Правка цели-ориентира подушки из Mini App. Баланс не трогается.

    target=null / 0 — снять цель.
    """
    from core.repos.pg_cushion_repo import _repo as _cushion_repo
    user_id = (await get_user_id(tg_id)) or ""
    target = body.target if (body.target and body.target > 0) else None
    try:
        await _cushion_repo.set_target(user_id, target)
    except Exception as e:
        logger.error("finance_cushion_set_target failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to set cushion target")
    return {"ok": True, "target": target}


class CushionDepositBody(BaseModel):
    amount: float = Field(gt=0)
    source: str = "manual"
    note: str = ""


@router.post("/finance/cushion/deposit")
async def finance_cushion_deposit(
    body: CushionDepositBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Пополнить баланс подушки из Mini App (инкремент + строка в
    cushion_transactions). Используется для переплаты по долгу (#123,
    `source="debt_overpaid"`) и вручную."""
    from core.repos.pg_cushion_repo import _repo as _cushion_repo
    user_id = (await get_user_id(tg_id)) or ""
    amount = int(round(body.amount))
    try:
        new_balance = await _cushion_repo.add_to_balance(
            user_id, float(amount),
            source=body.source or "manual", note=body.note or "",
        )
    except Exception as e:
        logger.error("finance_cushion_deposit failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to deposit")
    a = f"{amount:,}".replace(",", " ")
    await notify_user(tg_id, f"🛡️ В подушку: <b>+{a}₽</b>", bot="nexus")
    return {"ok": True, "amount": amount, "balance": int(round(new_balance))}


# DEPRECATED: use /api/finance instead (alias сохранён для обратной совместимости)
@router.post("/expenses")
async def expense_create(
    body: ExpenseBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    finance_body = FinanceBody(
        type="expense",
        amount=body.amount,
        cat=body.cat,
        desc=body.desc,
        bot=body.bot,
    )
    return await finance_create(finance_body, tg_id, idempotency_key=None)


# ═══════════════════════════════════════════════════════════════
# ARCANA — sessions verify, rituals result, clients add
# ═══════════════════════════════════════════════════════════════

_SESSION_STATUSES = {"✅ Да", "〰️ Частично", "❌ Нет", "⏳ Не проверено"}
_RITUAL_STATUSES = {"✅ Сработало", "〰️ Частично", "❌ Не сработало", "⏳ Не проверено"}


class VerifyBody(BaseModel):
    status: str


# ═══════════════════════════════════════════════════════════════
# Wave 6.7: фото расклада (Cloudinary) + AI-саммари трактовки
# ═══════════════════════════════════════════════════════════════


from core.cloudinary_client import cloudinary_upload as _cloudinary_upload_impl


async def _cloudinary_upload(file_bytes: bytes, filename: str) -> Optional[str]:
    """Тонкая обёртка над core.cloudinary_client (folder=arcana-sessions).

    Сохраняем имя для обратной совместимости с тестами в test_miniapp_wave3.py
    (моки делают patch на miniapp.backend.routes.writes._cloudinary_upload).
    """
    return await _cloudinary_upload_impl(file_bytes, filename, folder="arcana-sessions")


from fastapi import UploadFile, File as FastAPIFile


@router.post("/arcana/sessions/{session_id}/photo")
async def upload_session_photo(
    session_id: str,
    file: UploadFile = FastAPIFile(...),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    t = await _sessions_pg_repo.find_by_id(session_id)
    if not t:
        raise HTTPException(status_code=404, detail="session not found")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload(content, file.filename or "upload.jpg")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")

    try:
        await _sessions_pg_repo.set_photo_url(session_id, url)
    except Exception as e:
        logger.warning("Failed to set photo_url on session %s: %s", session_id, e)

    return {"ok": True, "url": url}


@router.post("/arcana/sessions/by-slug/{slug}/photo")
async def upload_session_photo_by_slug(
    slug: str,
    file: UploadFile = FastAPIFile(...),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Фото на уровне сессии — пишет URL в photo_url каждого триплета сессии."""
    user_id = (await get_user_id(tg_id)) or ""

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    matching = await _sessions_pg_repo.list_by_slug(slug, user_id)
    if not matching:
        # Fallback: treat slug as direct PG session id
        t = await _sessions_pg_repo.find_by_id(slug)
        if t:
            matching = [t]
    if not matching:
        raise HTTPException(status_code=404, detail="session not found")

    url = await _cloudinary_upload(content, file.filename or "upload.jpg")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")

    updated = 0
    for t in matching:
        try:
            await _sessions_pg_repo.set_photo_url(t.id, url)
            updated += 1
        except Exception as e:
            logger.warning("Failed to set photo_url on session %s: %s", t.id, e)

    return {"ok": True, "url": url, "updated_count": updated}


class SummarizeBody(BaseModel):
    pass


@router.post("/arcana/sessions/{session_id}/summarize")
async def summarize_session(
    session_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    import re
    from core.claude_client import ask_claude

    t = await _sessions_pg_repo.find_by_id(session_id)
    if not t:
        raise HTTPException(status_code=404, detail="session not found")

    if t.triplet_summary:
        return {"summary": t.triplet_summary, "cached": True}

    interp = t.interpretation or ""
    if not interp:
        raise HTTPException(status_code=400, detail="no interpretation to summarize")

    clean = re.sub(r"<[^>]+>", "", interp).strip()
    if len(clean) < 20:
        raise HTTPException(status_code=400, detail="interpretation too short")

    prompt = (
        f"Сделай короткое саммари этой трактовки в 2-3 предложения на русском. "
        f"Обращайся к Кай на ты, женский род. Только суть. "
        f"Output as plain Russian text, no formatting, no markdown, "
        f"no HTML tags, no emojis.\n\n"
        f"Трактовка:\n{clean}"
    )
    try:
        summary = await ask_claude(prompt, max_tokens=300,
                                    model="claude-haiku-4-5-20251001", temperature=0)
    except Exception as e:
        logger.error("Haiku summarize failed: %s", e)
        raise HTTPException(status_code=500, detail="summarize failed")

    from core.html_sanitize import sanitize_summary
    summary = sanitize_summary(summary or "")
    if not summary:
        raise HTTPException(status_code=500, detail="empty summary")

    try:
        await _sessions_pg_repo.update_summary(session_id, summary)
    except Exception as e:
        logger.warning("Failed to save summary to PG session %s: %s", session_id, e)

    return {"summary": summary, "cached": False}


# PG outcome code map for session verify
_SESSION_STATUS_TO_OUTCOME = {
    "✅ Да": "yes",
    "〰️ Частично": "partial",
    "❌ Нет": "no",
    "⏳ Не проверено": "unverified",
}


@router.post("/arcana/sessions/{session_id}/verify")
async def session_verify(
    session_id: str,
    body: VerifyBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.status not in _SESSION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_SESSION_STATUSES)}")

    t = await _sessions_pg_repo.find_by_id(session_id)
    if not t:
        raise HTTPException(status_code=404, detail="session not found")

    outcome_code = _SESSION_STATUS_TO_OUTCOME.get(body.status, "unverified")
    ok = await _sessions_pg_repo.set_outcome(session_id, outcome_code)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update Сбылось")

    # Инвалидация кеша саммари сессии (если триплет в группе).
    try:
        from core.session_cache import cache_delete, session_summary_key
        sname = t.session_name
        if sname:
            cache_delete(session_summary_key(sname, t.client_id))
    except Exception:
        pass

    _verdict_word = {
        "✅ Да": "сбылось ✅", "〰️ Частично": "частично 🌗",
        "❌ Нет": "не сбылось ❌", "⏳ Не проверено": "не проверено ⏳",
    }.get(body.status, body.status)
    q = t.question or "расклад"
    await notify_user(tg_id, f"🔮 {_esc(q)}: {_verdict_word}", bot="arcana")
    return {"ok": True, "status": body.status}


async def _cloudinary_upload_folder(file_bytes: bytes, filename: str, folder: str) -> Optional[str]:
    """Тонкая обёртка для аплоада в произвольную папку Cloudinary."""
    return await _cloudinary_upload_impl(file_bytes, filename, folder=folder)


@router.post("/arcana/rituals/{ritual_id}/photo")
async def upload_ritual_photo(
    ritual_id: str,
    file: UploadFile = FastAPIFile(...),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    ritual = await _rituals_pg_repo.find_by_id(ritual_id)
    if not ritual:
        raise HTTPException(status_code=404, detail="not found")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload_folder(content, file.filename or "ritual.jpg", "arcana-rituals")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")
    try:
        await _rituals_pg_repo.update_photo_url(ritual_id, url)
    except Exception as e:
        logger.warning("Failed to set photo URL on ritual %s: %s", ritual_id[:8], e)
    return {"ok": True, "url": url}


from fastapi import Form


@router.post("/arcana/clients/{client_id}/object_photo")
async def upload_client_object_photo(
    client_id: str,
    file: UploadFile = FastAPIFile(...),
    note: str = Form(""),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Append «URL | note» в поле object_photos клиента (PG text)."""
    user_id = (await get_user_id(tg_id)) or ""
    c = await _clients_repo.find_by_id(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="not found")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload_folder(content, file.filename or "object.jpg", "arcana-client-objects")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")

    from core.client_object_photos import append as _append
    existing = c.object_photos or ""
    new_raw, items = _append(existing, url, note or "")
    try:
        await _clients_repo.update_object_photos(client_id, new_raw)
    except Exception as e:
        logger.warning("Failed to append object photo: %s", e)
    return {"ok": True, "url": url, "note": (note or "").strip(), "photos": items}


class ObjectPhotoNoteBody(BaseModel):
    note: Optional[str] = ""


@router.patch("/arcana/clients/{client_id}/object_photo/{index}")
async def edit_client_object_photo_note(
    client_id: str,
    index: int,
    body: ObjectPhotoNoteBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    c = await _clients_repo.find_by_id(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    from core.client_object_photos import edit_note as _edit
    existing = c.object_photos or ""
    try:
        new_raw, items = _edit(existing, index, body.note or "")
    except IndexError:
        raise HTTPException(status_code=404, detail="object photo index out of range")
    await _clients_repo.update_object_photos(client_id, new_raw)
    return {"ok": True, "photos": items}


@router.delete("/arcana/clients/{client_id}/object_photo/{index}")
async def delete_client_object_photo(
    client_id: str,
    index: int,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    c = await _clients_repo.find_by_id(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    from core.client_object_photos import delete as _delete
    existing = c.object_photos or ""
    try:
        new_raw, items = _delete(existing, index)
    except IndexError:
        raise HTTPException(status_code=404, detail="object photo index out of range")
    await _clients_repo.update_object_photos(client_id, new_raw)
    return {"ok": True, "photos": items}


@router.post("/arcana/works/{work_id}/done")
async def arcana_work_done(
    work_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    w = await _works_pg_repo.find_by_id(work_id)
    if not w:
        raise HTTPException(status_code=404, detail="work not found")
    ok = await _works_pg_repo.set_status(work_id, "done")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    await notify_user(tg_id, f"✅ Готово: <b>{_esc(w.title)}</b>", bot="arcana")
    return {"ok": True, "status": "Done"}


@router.post("/arcana/works/{work_id}/cancel")
async def arcana_work_cancel(
    work_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    w = await _works_pg_repo.find_by_id(work_id)
    if not w:
        raise HTTPException(status_code=404, detail="work not found")
    ok = await _works_pg_repo.set_status(work_id, "archived")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to cancel")
    await notify_user(tg_id, f"❌ Отменила: <b>{_esc(w.title)}</b>", bot="arcana")
    return {"ok": True, "status": "Archived"}


@router.post("/arcana/works/{work_id}/postpone")
async def arcana_work_postpone(
    work_id: str,
    body: PostponeBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    w = await _works_pg_repo.find_by_id(work_id)
    if not w:
        raise HTTPException(status_code=404, detail="work not found")
    today_date, _tz_offset = await today_user_tz(tg_id)

    if body.date:
        try:
            new_date = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date, expected YYYY-MM-DD")
    else:
        base = w.deadline_dt.date() if w.deadline_dt else None
        if not base:
            base = today_date
        shift_days = body.days if body.days is not None else 1
        new_date = base + timedelta(days=shift_days)

    ok = await _works_pg_repo.set_deadline(work_id, new_date)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update deadline")
    await notify_user(
        tg_id,
        f"📅 Перенесла на {new_date.isoformat()}: <b>{_esc(w.title)}</b>",
        bot="arcana",
    )
    return {"ok": True, "new_date": new_date.isoformat()}


class ArcanaWorkCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: Optional[str] = None
    prio: str = "⚪"
    date: Optional[str] = None          # deadline YYYY-MM-DD или YYYY-MM-DDTHH:MM(:SS)
    deadline_time: Optional[str] = None  # HH:MM (если date без времени)
    reminder_date: Optional[str] = None  # YYYY-MM-DD
    reminder_time: Optional[str] = None  # HH:MM
    client_id: Optional[str] = None


@router.post("/arcana/works")
async def arcana_work_create(
    body: ArcanaWorkCreateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """#203: создать 🔮 Работу из Mini App (без Haiku — поля из формы).
    Плановый расклад/ритуал = открытая Работа нужной категории; при
    последующем сохранении расклада/ритуала core/work_relation её закроет."""
    user_id = (await get_user_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)

    deadline_dt: Optional[datetime] = None
    if body.date:
        raw = body.date
        if "T" not in raw and body.deadline_time:
            raw = f"{raw}T{body.deadline_time}"
        try:
            if "T" in raw:
                deadline_dt = datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
            else:
                deadline_dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date")

    work_id = await _works_pg_repo.create(
        title=body.title.strip(),
        priority=_WORK_PRIO_LABEL.get(body.prio, "Можно потом"),
        deadline=deadline_dt,
        category=body.category or None,
        client_id=body.client_id or None,
        user_id=user_id,
    )
    if not work_id:
        raise HTTPException(status_code=500, detail="failed to create work")

    # Напоминание: пользовательское, иначе (если есть дедлайн) — дефолт бота.
    reminder_iso: Optional[str] = None
    if body.reminder_date or body.reminder_time:
        rd = body.reminder_date or (deadline_dt.strftime("%Y-%m-%d") if deadline_dt else None)
        rt = body.reminder_time or "09:00"
        if rd:
            reminder_iso = f"{rd}T{rt}"
    if reminder_iso:
        try:
            from arcana.repos.works_tables import works as _t_works
            from core.db import get_engine as _get_engine
            import asyncio as _asyncio

            def _set_reminder() -> None:
                rdt = datetime.strptime(reminder_iso[:16], "%Y-%m-%dT%H:%M")
                with _get_engine().begin() as conn:
                    conn.execute(
                        _t_works.update()
                        .where(_t_works.c.id == int(work_id))
                        .values(reminder=rdt)
                    )
            await _asyncio.to_thread(_set_reminder)
            from arcana.bot import arcana_reminder_flow
            await arcana_reminder_flow.schedule_reminder(
                chat_id=tg_id,
                title=body.title.strip(),
                reminder_dt=reminder_iso,
                page_id=str(work_id),
                tz_offset=int(tz_offset),
            )
        except Exception as e:  # noqa: BLE001 — restore на старте подхватит
            logger.warning("arcana work reminder schedule failed: %s", e)

    await notify_user(tg_id, f"⚡ Создала работу: <b>{_esc(body.title.strip())}</b>", bot="arcana")
    return {"ok": True, "id": work_id, "reminder": reminder_iso}


class ArcanaGrimoireCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = "📝 Заметка"
    themes: Optional[str] = None   # запятые
    text: str = ""
    source: str = ""


@router.post("/arcana/grimoire")
async def arcana_grimoire_create(
    body: ArcanaGrimoireCreateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """#203: создать запись 📖 Гримуара из Mini App (структурная форма, без Haiku)."""
    user_id = (await get_user_id(tg_id)) or ""
    themes = [t.strip() for t in (body.themes or "").split(",") if t.strip()]
    entry_id = await _grimoire_repo.add(
        title=body.title.strip(),
        category=body.category or "📝 Заметка",
        themes=themes or None,
        text=body.text or "",
        source=body.source or "",
        user_id=user_id,
    )
    if not entry_id:
        raise HTTPException(status_code=500, detail="failed to create grimoire entry")
    await notify_user(tg_id, f"✍️ Записала в гримуар: <b>{_esc(body.title.strip())}</b>", bot="arcana")
    return {"ok": True, "id": entry_id}


@router.post("/arcana/rituals/{ritual_id}/result")
async def ritual_result(
    ritual_id: str,
    body: VerifyBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.status not in _RITUAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_RITUAL_STATUSES)}")
    ritual = await _rituals_pg_repo.find_by_id(ritual_id)
    if not ritual:
        raise HTTPException(status_code=404, detail="not found")
    ok = await _rituals_pg_repo.set_result(ritual_id, body.status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update Результат")
    _result_word = {
        "✅ Сработало": "сработало ✅", "〰️ Частично": "частично 🌗",
        "❌ Не сработало": "не сработало ❌", "⏳ Не проверено": "не проверено ⏳",
    }.get(body.status, body.status)
    name = ritual.name or "ритуал"
    await notify_user(tg_id, f"🕯 {_esc(name)}: {_result_word}", bot="arcana")
    return {"ok": True, "status": body.status}


class ClientBody(BaseModel):
    name: str
    contact: str = ""
    request: str = ""
    status: Optional[str] = None
    type: Optional[str] = None  # "🤝 Платный" | "🎁 Бесплатный"
    notes: Optional[str] = None
    birthday: Optional[str] = None  # YYYY-MM-DD


_CLIENT_TYPES_ALLOWED_CREATE = {"🤝 Платный", "🎁 Бесплатный"}
_CLIENT_TYPES_ALLOWED_EDIT = {"🤝 Платный", "🎁 Бесплатный"}  # 🌟 Self нельзя выставлять из UI


@router.post("/arcana/clients")
async def arcana_client_create(
    body: ClientBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    ctype = body.type if body.type in _CLIENT_TYPES_ALLOWED_CREATE else None
    pg_id_str = await _clients_repo.add(
        name=body.name,
        contact=body.contact,
        request=body.request,
        user_id=user_id,
        client_type=ctype,
    )
    if not pg_id_str:
        raise HTTPException(status_code=500, detail="failed to create client")
    if body.notes or body.birthday:
        try:
            await _clients_repo.update_profile(
                pg_id_str,
                notes=body.notes,
                birthday=body.birthday or None,
            )
        except Exception as e:
            logger.warning("client_create extra fields write failed: %s", e)
    return {"ok": True, "id": pg_id_str}


class ClientUpdateBody(BaseModel):
    notes: Optional[str] = None
    request: Optional[str] = None
    contact: Optional[str] = None
    type: Optional[str] = None  # "🤝 Платный" | "🎁 Бесплатный"
    birthday: Optional[str] = None  # YYYY-MM-DD; пустая строка = очистить


@router.post("/arcana/clients/{client_id}/edit")
async def arcana_client_edit(
    client_id: str,
    body: ClientUpdateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    c = await _clients_repo.find_by_id(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    # self-client (🌟 Self) — тип менять нельзя
    is_self = c.type_code == "self"

    type_code_update = None
    if body.type is not None and not is_self:
        if body.type not in _CLIENT_TYPES_ALLOWED_EDIT:
            raise HTTPException(status_code=400, detail="invalid type")
        type_code_update = _CLIENT_TYPE_TO_CODE.get(body.type)

    has_update = any([
        body.notes is not None,
        body.request is not None,
        body.contact is not None,
        body.birthday is not None,
        type_code_update is not None,
    ])
    if not has_update:
        return {"ok": True, "noop": True}

    try:
        await _clients_repo.update_profile(
            client_id,
            notes=body.notes,
            request=body.request,
            contact=body.contact,
            birthday=body.birthday if body.birthday else None,
            type_code=type_code_update,
        )
    except Exception as e:
        logger.error("arcana_client_edit failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update client")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# LISTS — create, done, delete (archive)
# ═══════════════════════════════════════════════════════════════

_LIST_TYPES = {
    "buy": "🛒 Покупки",
    "check": "📋 Чеклист",
    "inv": "📦 Инвентарь",
}


async def _get_list_item_pg(item_id: str, user_id: str):
    """Найти item в nexus_lists (first) или arcana_inventory. 404 если не найден.

    Returns (item, is_arcana: bool).
    Ownership: разрешаем legacy items без user_id (allow_empty_owner).
    """
    nx_item = await _nexus_lists_repo.get_by_id(item_id)
    if nx_item:
        if user_id and nx_item.user_id and nx_item.user_id != user_id:
            raise HTTPException(status_code=404, detail="not found")
        return nx_item, False
    ai_item = await _arcana_inv_repo.get_by_id(item_id)
    if ai_item:
        if user_id and ai_item.user_id and ai_item.user_id != user_id:
            raise HTTPException(status_code=404, detail="not found")
        return ai_item, True
    raise HTTPException(status_code=404, detail="not found")


class ListCreateBody(BaseModel):
    type: str  # buy|check|inv
    name: str
    cat: Optional[str] = None
    qty: Optional[float] = None
    note: Optional[str] = None
    price: Optional[float] = None         # факт-цена при покупке
    # v1.2 — планируемые покупки
    price_plan: Optional[float] = None
    source: Optional[str] = None
    stage: Optional[int] = None
    group: Optional[str] = None
    priority: Optional[str] = None
    expires: Optional[str] = None
    bot: Optional[str] = None  # nexus | arcana (default nexus)


@router.post("/lists")
async def list_create(
    body: ListCreateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.type not in _LIST_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_LIST_TYPES)}")
    user_id = (await get_user_id(tg_id)) or ""
    notion_type = _LIST_TYPES[body.type]
    is_arcana = (body.bot or "").lower() == "arcana"
    try:
        if is_arcana:
            item = await _arcana_inv_repo.add_item(
                name=body.name,
                list_type=notion_type,
                category=body.cat or "",
                quantity=body.qty,
                note=body.note or "",
                group_name=body.group or "",
                user_id=user_id,
            )
        else:
            item = await _nexus_lists_repo.add_item(
                name=body.name,
                list_type=notion_type,
                category=body.cat or "",
                quantity=body.qty,
                note=body.note or "",
                price_actual=body.price,
                price_plan=body.price_plan,
                store=body.source or "",
                stage=body.stage,
                group_name=body.group or "",
                priority=body.priority or "",
                expires_at=body.expires,
                user_id=user_id,
            )
    except Exception as e:
        logger.error("list_create PG failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to create list item")
    return {"ok": True, "id": item.id}


@router.post("/lists/{item_id}/done")
async def list_done(
    item_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Помечает Done. Не пишет в Финансы (для этого есть /checkout)."""
    user_id = (await get_user_id(tg_id)) or ""
    item, is_arcana = await _get_list_item_pg(item_id, user_id)
    try:
        if is_arcana:
            await _arcana_inv_repo.update_status(item_id, "Done")
        else:
            await _nexus_lists_repo.update_status(item_id, "Done")
    except Exception as e:
        logger.error("list_done failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to mark done")
    return {"ok": True}


# ── v1.2: /checkout — Done + автозапись в Финансы ────────────────────────────

class ListCheckoutBody(BaseModel):
    price: Optional[float] = None    # фактическая цена; если None — берём Цена план
    note: Optional[str] = None        # описание расхода


@router.post("/lists/{item_id}/checkout")
async def list_checkout(
    item_id: str,
    body: ListCheckoutBody,
    tg_id: int = Depends(current_user_id),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Помечает пункт Done и при наличии цены создаёт запись в 💰 Финансы.

    Логика факт-цены:
    1. body.price если передан;
    2. Цена план из самой записи (только для nexus_lists — у arcana_inventory поля нет);
    3. ничего → finance_created=False, расход не создаётся.

    Бесплатная идемпотентность: если айтем уже done — финзапись не создаётся повторно.
    """
    from core.list_manager import CATEGORY_TO_FINANCE

    user_id = (await get_user_id(tg_id)) or ""
    item, is_arcana = await _get_list_item_pg(item_id, user_id)

    name = item.name or ""
    category = item.category or "💳 Прочее"
    bot_label = "🌒 Arcana" if is_arcana else BOT_NEXUS
    price_plan = float(getattr(item, "price_plan", None) or 0)
    actual = body.price if body.price is not None else price_plan
    actual = float(actual or 0)

    # Free idempotency: item already done → skip finance write
    already_done = (item.status == "done")

    try:
        if is_arcana:
            await _arcana_inv_repo.update_status(item_id, "Done")
        else:
            update_fields: dict = {"status": "done"}
            if actual > 0:
                update_fields["price_actual"] = actual
            await _nexus_lists_repo.update(item_id, **update_fields)
    except Exception as e:
        logger.error("list_checkout: update failed for %s: %s", item_id[:8], e)
        raise HTTPException(status_code=500, detail="failed to mark done")

    if already_done:
        logger.info("list_checkout: item %s already done, skipping finance write", item_id[:8])
        return {"ok": True, "amount": actual, "finance_created": False, "finance_id": None}

    async def _write_finance() -> dict:
        finance_id = None
        if actual > 0:
            finance_cat = CATEGORY_TO_FINANCE.get(category, "💳 Прочее")
            today_iso, _tz = await today_user_tz(tg_id)
            try:
                finance_id = await _fin_repo.add(
                    date=today_iso.isoformat() if hasattr(today_iso, "isoformat") else str(today_iso),
                    amount=actual,
                    category=finance_cat,
                    type_="💸 Расход",
                    source="💳 Карта",
                    description=body.note or name or "покупка",
                    bot_label=bot_label,
                    user_id=user_id,
                )
            except Exception as e:
                logger.error("list_checkout: finance_add failed: %s", e)
        return {
            "ok": True,
            "amount": actual,
            "finance_created": bool(finance_id),
            "finance_id": finance_id,
        }

    return await idempotent(tg_id, idempotency_key, _write_finance)


@router.post("/lists/{item_id}/delete")
async def list_delete(
    item_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Soft delete — переводим в Archived, не удаляем физически."""
    user_id = (await get_user_id(tg_id)) or ""
    item, is_arcana = await _get_list_item_pg(item_id, user_id)
    try:
        if is_arcana:
            await _arcana_inv_repo.update_status(item_id, "Archived")
        else:
            await _nexus_lists_repo.update_status(item_id, "Archived")
    except Exception as e:
        logger.error("list_delete failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to archive")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# NOTES / MEMORY (минимум для FAB)
# ═══════════════════════════════════════════════════════════════

class NoteBody(BaseModel):
    text: str
    cat: Optional[str] = None


@router.post("/memory")
async def memory_create(
    body: NoteBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """FAB «В память» — как в боте: Haiku парсит текст (категория/связь/ключ),
    долги уходят в `debts`, алиас канонизируется, потом уведомление в Nexus
    (#6). `body.cat` из формы игнорируется — категорию ставит парсер."""
    user_id = (await get_user_id(tg_id)) or ""
    from core.memory import parse_and_store
    r = await parse_and_store(body.text, user_id, bot_label="☀️ Nexus")
    if r["kind"] == "error":
        raise HTTPException(status_code=500, detail="failed to create memory")
    if r["kind"] == "debt":
        await notify_user(tg_id, f"📋 Записала долг: <b>{_esc(r['fact'])}</b>", bot="nexus")
        return {"ok": True, "id": None, "kind": "debt"}
    await notify_user(tg_id, f"🧠 Запомнила: <b>{_esc(r['fact'])}</b>", bot="nexus")
    return {"ok": True, "id": r["memory_id"]}


class MemoryPatchBody(BaseModel):
    is_current: bool


@router.patch("/memory/{memory_id}")
async def memory_set_current(
    memory_id: str,
    body: MemoryPatchBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """#6: обратимая «неактуально» — toggle is_current (не archive/delete).
    Запись остаётся в БД и в поиске, помечена (не)актуальной."""
    user_id = (await get_user_id(tg_id)) or ""
    mem = await _memory_repo.get_by_id(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="not found")
    if user_id and mem.user_id and mem.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    n = await _memory_repo.set_current([memory_id], body.is_current)
    if not n:
        raise HTTPException(status_code=500, detail="failed to update memory")
    return {"ok": True, "is_current": body.is_current}


@router.delete("/memory/{memory_id}")
async def memory_delete(
    memory_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Удаление записи из 🧠 Память — жёсткий DELETE (не archive): строка
    физически уходит из Postgres вместе с колонкой embedding, что автоматом
    убирает факт и из RAG-поиска (core/memory_rag.py читает embedding из
    той же строки, отдельного vector store нет)."""
    user_id = (await get_user_id(tg_id)) or ""
    mem = await _memory_repo.get_by_id(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="not found")
    if user_id and mem.user_id and mem.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    ok = await _memory_repo.delete(memory_id)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to delete memory")
    return {"ok": True}
