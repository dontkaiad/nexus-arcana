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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import config
from core.notion_client import (
    finance_add,
    get_page,
    page_create,
    update_page,
    update_page_select,
    update_task_deadline,
    update_task_status,
)
from core.notion_client import _title, _text, _select, _status, _number, _date, _relation
from core.user_manager import get_user_notion_id
from core.bot_notify import notify_user, clear_task_reminder

from arcana.repos.pg_rituals_repo import PgRitualsRepo as _PgRitualsRepoClass
_rituals_pg_repo = _PgRitualsRepoClass()

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

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    relation_ids_of,
    select_of,
    title_plain,
    today_user_tz,
)

logger = logging.getLogger("miniapp.writes")

router = APIRouter()


# ── Ownership check ─────────────────────────────────────────────────────────

async def _load_owned_page(page_id: str, user_notion_id: str, allow_empty_owner: bool = False) -> dict:
    """Читает страницу и проверяет владение. 404 если нет доступа.

    wave8.60: allow_empty_owner — для чеклистов/списков, которые создаются
    из Notion-UI без relation на пользователя (read side уже разрешает).
    """
    try:
        page = await get_page(page_id)
    except Exception as e:
        logger.warning("get_page failed for %s: %s", page_id[:8], e)
        raise HTTPException(status_code=404, detail="not found")
    if not page:
        raise HTTPException(status_code=404, detail="not found")
    if user_notion_id:
        owners = relation_ids_of(page, "🪪 Пользователи")
        if user_notion_id not in owners and not (allow_empty_owner and not owners):
            raise HTTPException(status_code=404, detail="not found")
    return page


# ═══════════════════════════════════════════════════════════════
# TASKS
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/done")
async def task_done(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    from miniapp.backend._helpers import rich_text_plain
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(task_id, user_notion_id)
    # Повторяющаяся задача (есть «Время повтора») → In progress, не Done.
    # Так она остаётся в списке до дедлайна и не улетает в архив.
    repeat_time = rich_text_plain(page, "Время повтора").strip()
    repeat_kind = select_of(page, "Повтор") or ""
    is_repeating = bool(repeat_time or repeat_kind)
    new_status = "In progress" if repeat_time else "Done"
    ok = await update_task_status(task_id, new_status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    now_utc = datetime.now(timezone.utc)
    try:
        await update_page(task_id, {"Время завершения": _date(now_utc.isoformat())})
    except Exception as e:
        logger.warning("could not set completion time: %s", e)
    today_local, tz_offset = await today_user_tz(tg_id)
    if is_repeating:
        try:
            from core.task_streaks import update_task_streak
            update_task_streak(
                user_id=tg_id,
                task_id=task_id,
                task_title=title_plain(page, "Задача"),
                repeat_kind=repeat_kind or "Каждый день",
                today_local=today_local.isoformat(),
            )
        except Exception as e:
            logger.warning("update_task_streak failed: %s", e)
    # #38 fix per TASKS_SPEC: глобальный дневной стрик инкрементируется
    # от ЛЮБОЙ Done-задачи (повторяющейся или нет). Раньше Mini App-toggle
    # не дёргал update_streak вообще — счётчик не двигался.
    try:
        from nexus.handlers.streaks import update_streak
        await update_streak(
            tg_id, tz_offset,
            source="miniapp_task_done",
            task_id=task_id,
        )
    except Exception as e:
        logger.warning("update_streak failed: %s", e)
    task_title = title_plain(page, "Задача")
    verb = "🔄 Отметила" if is_repeating else "✅ Готово"
    await notify_user(tg_id, f"{verb}: <b>{_esc(task_title)}</b>", bot="nexus")
    # #73: погасить живую плашку-напоминание этой задачи в чате (если висит).
    await clear_task_reminder(task_id, bot="nexus")
    return {"ok": True, "status": new_status}


@router.post("/tasks/{task_id}/reopen")
async def task_reopen(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(task_id, user_notion_id)
    ok = await update_task_status(task_id, "Not started")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    await notify_user(tg_id, f"↩️ Снова активна: <b>{_esc(title_plain(page, 'Задача'))}</b>", bot="nexus")
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(task_id, user_notion_id)
    today_date, tz_offset = await today_user_tz(tg_id)

    if body.date:
        try:
            new_date = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date, expected YYYY-MM-DD")
    else:
        deadline_raw = (page.get("properties", {}).get("Дедлайн", {}).get("date") or {}).get("start", "")
        base = None
        if deadline_raw:
            try:
                base = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).date()
            except ValueError:
                base = None
        if not base:
            base = today_date
        shift_days = body.days if body.days is not None else 1
        new_date = base + timedelta(days=shift_days)

    ok = await update_task_deadline(task_id, new_date.isoformat())
    if not ok:
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
            await update_page(task_id, {"Напоминание": _date(remind_iso)})
        except Exception as e:
            logger.warning("could not set reminder: %s", e)

    await notify_user(
        tg_id,
        f"📅 Перенесла на {new_date.isoformat()}: <b>{_esc(title_plain(page, 'Задача'))}</b>",
        bot="nexus",
    )
    return {"ok": True, "new_date": new_date.isoformat(), "reminder": remind_iso}


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(task_id, user_notion_id)
    ok = await update_task_status(task_id, "Archived")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to cancel")
    await notify_user(tg_id, f"❌ Отменила: <b>{_esc(title_plain(page, 'Задача'))}</b>", bot="nexus")
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
    date: Optional[str] = None           # YYYY-MM-DD
    time: Optional[str] = None           # HH:MM (reminder)


@router.post("/tasks/{task_id}/edit")
async def task_edit(
    task_id: str,
    body: TaskEditBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(task_id, user_notion_id)
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
        props["Дедлайн"] = _date(new_date.isoformat())
        if body.time:
            try:
                hh, mm = body.time.split(":")
                tz = timezone(timedelta(hours=tz_offset))
                remind_dt = datetime(new_date.year, new_date.month, new_date.day,
                                     int(hh), int(mm), tzinfo=tz)
                props["Напоминание"] = _date(remind_dt.isoformat())
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="invalid time, expected HH:MM")

    if not props:
        return {"ok": True, "noop": True}

    try:
        await update_page(task_id, props)
    except Exception as e:
        logger.error("task_edit update_page failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update task")
    new_title = (body.title or "").strip() or title_plain(page, "Задача")
    await notify_user(tg_id, f"✏️ Изменила: <b>{_esc(new_title)}</b>", bot="nexus")
    return {"ok": True}


@router.post("/tasks")
async def task_create(
    body: TaskCreateBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    db_id = config.nexus.db_tasks
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
        props["Дедлайн"] = _date(body.date)
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    page_id = await page_create(db_id, props)
    if not page_id:
        raise HTTPException(status_code=500, detail="failed to create task")
    await notify_user(tg_id, f"➕ Создала задачу: <b>{_esc(body.title)}</b>", bot="nexus")
    return {"ok": True, "id": page_id}


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
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
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

    page_id = await finance_add(
        date=today_date.isoformat(),
        amount=body.amount,
        category=category,
        type_=type_label,
        source="💳 Карта",
        bot_label=bot_label,
        description=body.desc,
        user_notion_id=user_notion_id,
    )
    if not page_id:
        raise HTTPException(status_code=500, detail="failed to create finance entry")
    return {"ok": True, "id": page_id, "type": body.type}


class DebtBody(BaseModel):
    name: str = Field(min_length=1)
    amount: float = Field(gt=0)
    deadline: str = ""


@router.post("/finance/debt")
async def finance_debt_create(
    body: DebtBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Новый долг → запись в Памяти с категорией 📋 Долги (как в боте)."""
    from nexus.handlers.finance import _save_debt
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    name = body.name.strip()
    try:
        await _save_debt(name, int(body.amount), body.deadline.strip(), user_notion_id)
    except Exception as e:
        logger.error("finance_debt_create failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to save debt")
    return {"ok": True, "name": name, "amount": int(body.amount)}


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
    return await finance_create(finance_body, tg_id)


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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    matching = await _sessions_pg_repo.list_by_slug(slug, user_notion_id)
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
                                    model="claude-haiku-4-5-20251001")
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(work_id, user_notion_id)
    try:
        await update_page(work_id, {"Status": _status("Done")})
    except Exception as e:
        logger.error("arcana_work_done failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update status")
    await notify_user(tg_id, f"✅ Готово: <b>{_esc(title_plain(page, 'Работа'))}</b>", bot="arcana")
    return {"ok": True, "status": "Done"}


@router.post("/arcana/works/{work_id}/cancel")
async def arcana_work_cancel(
    work_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(work_id, user_notion_id)
    try:
        await update_page(work_id, {"Status": _status("Archived")})
    except Exception as e:
        logger.error("arcana_work_cancel failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to cancel")
    await notify_user(tg_id, f"❌ Отменила: <b>{_esc(title_plain(page, 'Работа'))}</b>", bot="arcana")
    return {"ok": True, "status": "Archived"}


@router.post("/arcana/works/{work_id}/postpone")
async def arcana_work_postpone(
    work_id: str,
    body: PostponeBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(work_id, user_notion_id)
    today_date, _tz_offset = await today_user_tz(tg_id)

    if body.date:
        try:
            new_date = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date, expected YYYY-MM-DD")
    else:
        deadline_raw = (page.get("properties", {}).get("Дедлайн", {}).get("date") or {}).get("start", "")
        base = None
        if deadline_raw:
            try:
                base = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).date()
            except ValueError:
                base = None
        if not base:
            base = today_date
        shift_days = body.days if body.days is not None else 1
        new_date = base + timedelta(days=shift_days)

    try:
        await update_page(work_id, {"Дедлайн": _date(new_date.isoformat())})
    except Exception as e:
        logger.error("arcana_work_postpone failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update deadline")
    await notify_user(
        tg_id,
        f"📅 Перенесла на {new_date.isoformat()}: <b>{_esc(title_plain(page, 'Работа'))}</b>",
        bot="arcana",
    )
    return {"ok": True, "new_date": new_date.isoformat()}


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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    ctype = body.type if body.type in _CLIENT_TYPES_ALLOWED_CREATE else None
    pg_id_str = await _clients_repo.add(
        name=body.name,
        contact=body.contact,
        request=body.request,
        user_notion_id=user_notion_id,
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
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
    db_id = config.db_lists
    if not db_id:
        raise HTTPException(status_code=500, detail="lists DB not configured")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    bot_label = "🌒 Arcana" if (body.bot or "").lower() == "arcana" else BOT_NEXUS
    props: dict = {
        "Название": _title(body.name),
        "Тип": _select(_LIST_TYPES[body.type]),
        "Статус": _status("Not started"),
        "Бот": _select(bot_label),
    }
    if body.cat:
        props["Категория"] = _select(body.cat)
    if body.qty is not None:
        props["Количество"] = _number(float(body.qty))
    if body.note:
        props["Заметка"] = _text(body.note)
    if body.price is not None:
        props["Цена"] = _number(float(body.price))
    # v1.2: новые поля
    if body.price_plan is not None:
        props["Цена план"] = _number(float(body.price_plan))
    if body.source:
        props["Магазин"] = _text(body.source)
    if body.stage is not None:
        props["Этап"] = _number(int(body.stage))
    if body.group:
        props["Группа"] = _text(body.group)
    if body.priority:
        props["Приоритет"] = _select(body.priority)
    if body.expires:
        props["Срок годности"] = _date(body.expires)
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)

    page_id = await page_create(db_id, props)
    if not page_id:
        raise HTTPException(status_code=500, detail="failed to create list item")
    return {"ok": True, "id": page_id}


@router.post("/lists/{item_id}/done")
async def list_done(
    item_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """v1.1 (legacy): просто помечает Done. Не пишет в Финансы.

    v1.2 предпочитает /checkout который создаёт расход. Endpoint оставлен
    для обратной совместимости (используется существующим UI чеклистов).
    """
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(item_id, user_notion_id, allow_empty_owner=True)
    try:
        await update_page(item_id, {"Статус": _status("Done")})
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
) -> dict[str, Any]:
    """Помечает пункт Done и при наличии цены создаёт запись в 💰 Финансы.

    Логика факт-цены:
    1. body.price если передан;
    2. Цена план из самой записи;
    3. ничего → finance_created=False, расход не создаётся.
    """
    from core.list_manager import CATEGORY_TO_FINANCE
    from core.notion_client import _extract_number, _extract_select

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(item_id, user_notion_id, allow_empty_owner=True)
    props = page.get("properties", {})

    name = title_plain(props.get("Название", {}))
    category = _extract_select(props.get("Категория", {})) or "💳 Прочее"
    price_plan = _extract_number(props.get("Цена план", {})) or 0.0
    bot_label = _extract_select(props.get("Бот", {})) or BOT_NEXUS

    actual = body.price if body.price is not None else price_plan
    actual = float(actual or 0)

    # wave9 (#46): если запись с Цена-полем недоступна или Notion ругается на
    # пэйлоад с «Цена» (legacy items без этой колонки) — повторяем без цены,
    # чтобы хотя бы Done проставился. Так чек на «иглах для машинки» не
    # ломает UX, а реальная причина пишется в лог.
    update_props: dict = {"Статус": _status("Done")}
    if actual > 0:
        update_props["Цена"] = _number(actual)
    try:
        await update_page(item_id, update_props)
    except Exception as e:
        logger.error("list_checkout: update_page (with price) failed for %s: %s", item_id[:8], e)
        if "Цена" in update_props:
            try:
                await update_page(item_id, {"Статус": _status("Done")})
            except Exception as e2:
                logger.error("list_checkout: status-only update also failed for %s: %s", item_id[:8], e2)
                raise HTTPException(status_code=500, detail=f"failed to mark done: {e2}")
        else:
            raise HTTPException(status_code=500, detail=f"failed to mark done: {e}")

    finance_id = None
    if actual > 0:
        finance_cat = CATEGORY_TO_FINANCE.get(category, "💳 Прочее")
        today_iso, _tz = await today_user_tz(tg_id)
        try:
            finance_id = await finance_add(
                date=today_iso.isoformat() if hasattr(today_iso, "isoformat") else str(today_iso),
                amount=actual,
                category=finance_cat,
                type_="💸 Расход",
                source="💳 Карта",
                description=body.note or name or "покупка",
                bot_label=bot_label,
                user_notion_id=user_notion_id,
            )
        except Exception as e:
            logger.error("list_checkout: finance_add failed: %s", e)

    return {
        "ok": True,
        "amount": actual,
        "finance_created": bool(finance_id),
        "finance_id": finance_id,
    }


@router.post("/lists/{item_id}/delete")
async def list_delete(
    item_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Soft delete — переводим в Archived, не удаляем физически."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(item_id, user_notion_id, allow_empty_owner=True)
    try:
        await update_page(item_id, {"Статус": _status("Archived")})
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
    db_id = config.nexus.db_memory
    if not db_id:
        raise HTTPException(status_code=500, detail="memory DB not configured")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    props: dict = {
        "Текст": _title(body.text),
        "Актуально": {"checkbox": True},
    }
    if body.cat:
        props["Категория"] = _select(body.cat)
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    page_id = await page_create(db_id, props)
    if not page_id:
        raise HTTPException(status_code=500, detail="failed to create memory")
    return {"ok": True, "id": page_id}
