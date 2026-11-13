"""miniapp/backend/routes/mcp_practice.py — создание личной практики для MCP-сервера kai-mcp (ADR-0040).

Один НОВЫЙ маршрут под шлюзом `mcp_practice_gate` (Bearer обязателен): создать работу Arcana БЕЗ клиента. Только создание: ни
переноса, ни правки, ни удаления. Существующие `POST /api/arcana/works` и остальные маршруты работ не трогаются и по этому токену
закрыты.

Что работа НЕ получает: клиента (метод репозитория `create_personal` не принимает client_id), заметок, повтора, напоминания.
Что НЕ происходит: нет сообщения в чат (ни «создала работу», ни напоминания, ни дедлайн-пинга), планировщик не трогается. Алерт
уходит только в топик лог-группы. Слот в Заре занимают только расклад и ритуал с явным временем (правило `_slot_fields`, то же,
что у бота); работа с датой без времени не занимает ничего. Если слот занят бронью друга, в ответе есть `conflicts` (только
дата и время), а в закрытый топик уходит предупреждение с именем (ADR-0039 D2, только чтение).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from arcana.repos.pg_clients_repo import PgClientsRepo
from arcana.repos.pg_works_repo import DEFAULT_SLOT_MINUTES, PgWorksRepo, _slot_fields
from core import mcp_practice as rules
from core.bot_notify import notify_log_group
from core.config import config
from core.date_extract import wall_to_aware
from core.user_manager import get_user_id
from miniapp.backend._helpers import today_user_tz
from miniapp.backend.auth import current_user_id
from miniapp.backend.mcp_practice_auth import mcp_practice_gate
from miniapp.backend.routes.mcp_tasks import _find_conflicts, _http, _limited, _public

logger = logging.getLogger("miniapp.mcp_practice")

router = APIRouter()
_works_repo = PgWorksRepo()
_clients_repo = PgClientsRepo()
_create_limiter = rules.DailyLimiter(rules.CREATE_LIMIT_PER_DAY)


class PracticeBody(BaseModel):
    """Ровно пять полей. Клиента, заметки, повтора, напоминания и всего остального в теле нет: лишнее поле даёт 422."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=rules.TITLE_MAX * 2)
    deadline_date: str
    deadline_time: Optional[str] = None
    category: str = rules.DEFAULT_CATEGORY
    prio: str = rules.DEFAULT_PRIO


async def _alert(text: str) -> None:
    """В топик лог-группы тем же общим лог-ботом, что и остальные алерты. Не бросает."""
    try:
        await notify_log_group(text, config.log_thread_kaimcp or config.log_thread_nexus)
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp practice: alert failed: %s", e)


async def _client_names(user_id: str) -> list:
    """Имена клиентов владельца, кроме её собственной карточки «Я» (личная практика может её называть). Сбой чтения → 503:
    без проверки имён не пишем."""
    try:
        return [c.name for c in await _clients_repo.list_all(user_id) if (c.type_code or "") != "self" and c.name]
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp practice: client list failed: %s", e)
        raise HTTPException(status_code=503, detail={"code": "client_check_failed",
                                                     "message": "could not check the title against client names, try later"})


@router.post("/mcp/practice", status_code=201, dependencies=[Depends(mcp_practice_gate)])
async def mcp_create_practice(body: PracticeBody, tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_id = await get_user_id(tg_id)
    if not user_id:
        # работа с пустым user_id видна всем в busy-калькуляторе Зари: без владельца не пишем
        raise HTTPException(status_code=409, detail={"code": "owner_unknown", "message": "the owner has no user id"})
    today_date, tz_offset = await today_user_tz(tg_id)
    try:
        title = rules.clean_title(body.title)
        d = rules.parse_date(body.deadline_date)
        rules.check_date_window(d, today_date)
        hhmm = rules.parse_time(body.deadline_time)
        category = rules.pick_category(body.category)
        priority = rules.pick_priority(body.prio)
        rules.check_title_has_no_client(title, await _client_names(user_id))
    except rules.McpTaskError as e:
        if e.code == "client_name_in_title":
            logger.warning("mcp practice: title rejected, matches a client name")     # ни названия, ни имени в лог не пишем
        raise _http(e)

    deadline = wall_to_aware(f"{d.isoformat()}T{hhmm}" if hhmm else d.isoformat(), int(tz_offset))   # без времени: полночь локального дня
    _limited(_create_limiter, "created practices")
    try:
        work_id = await _works_repo.create_personal(
            title=title, priority=priority, deadline=deadline, category=category, user_id=user_id, tz_offset=int(tz_offset))
    except Exception:
        _create_limiter.release()
        raise
    if not work_id:
        _create_limiter.release()
        raise HTTPException(status_code=500, detail={"code": "create_failed", "message": "failed to create the practice"})

    await _alert(rules.alert_created(title, d.isoformat(), hhmm))
    # Слот и пересечение: только если работа действительно заняла слот (то же правило, что и при записи). Нет слота, нет пересечения.
    conflicts: Optional[list] = []
    if _slot_fields(category, deadline, int(tz_offset)):
        found = await _find_conflicts(user_id, int(tz_offset), d, hhmm, DEFAULT_SLOT_MINUTES)
        if found:
            await _alert(rules.alert_conflict(work_id, title, found))
        conflicts = _public(found)
    return {
        "id": work_id,
        "title": title,
        "deadline": d.isoformat(),
        "deadline_time": hhmm,
        "category": category,
        "prio": body.prio.replace("️", ""),
        "conflicts": conflicts,
    }
