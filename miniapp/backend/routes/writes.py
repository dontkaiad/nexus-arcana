"""miniapp/backend/routes/writes.py — POST-endpoints для wave 3.

Все эндпоинты требуют initData (current_user_id). Общий подход:
- проверяем, что страница принадлежит юзеру (по 🪪 Пользователи relation),
  иначе 404 (не выдаём подсказок о существовании чужих записей).
- принимаем только поля, известные из Notion-схемы. Всё остальное игнорируем.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import config
from core.notion_client import (
    client_add,
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
    new_status = "In progress" if repeat_time else "Done"
    ok = await update_task_status(task_id, new_status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
    now_utc = datetime.now(timezone.utc)
    try:
        await update_page(task_id, {"Время завершения": _date(now_utc.isoformat())})
    except Exception as e:
        logger.warning("could not set completion time: %s", e)
    return {"ok": True, "status": new_status}


@router.post("/tasks/{task_id}/reopen")
async def task_reopen(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(task_id, user_notion_id)
    ok = await update_task_status(task_id, "Not started")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update status")
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

    return {"ok": True, "new_date": new_date.isoformat(), "reminder": remind_iso}


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(
    task_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(task_id, user_notion_id)
    ok = await update_task_status(task_id, "Archived")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to cancel")
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
    await _load_owned_page(task_id, user_notion_id)
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(session_id, user_notion_id)

    # 5 MB limit
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload(content, file.filename or "upload.jpg")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")

    try:
        await update_page(session_id, {"Фото": {"url": url}})
    except Exception as e:
        logger.warning("Failed to set Фото URL in Notion: %s", e)

    return {"ok": True, "url": url}


class SummarizeBody(BaseModel):
    pass


@router.post("/arcana/sessions/{session_id}/summarize")
async def summarize_session(
    session_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    from core.claude_client import ask_claude
    from miniapp.backend._helpers import rich_text_plain

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(session_id, user_notion_id)

    # existing summary
    existing = rich_text_plain(page, "AI_Summary")
    if existing:
        return {"summary": existing, "cached": True}

    interp = rich_text_plain(page, "Трактовка")
    if not interp:
        raise HTTPException(status_code=400, detail="no interpretation to summarize")

    # Strip HTML tags
    import re
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
        await update_page(session_id, {"AI_Summary": _text(summary)})
    except Exception as e:
        logger.warning("Failed to save AI_Summary to Notion: %s", e)

    return {"summary": summary, "cached": False}


@router.post("/arcana/sessions/{session_id}/verify")
async def session_verify(
    session_id: str,
    body: VerifyBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.status not in _SESSION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_SESSION_STATUSES)}")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(session_id, user_notion_id)
    ok = await update_page_select(session_id, "Сбылось", body.status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update Сбылось")

    # Инвалидация кеша саммари сессии (если триплет в группе).
    try:
        from core.session_cache import cache_delete, session_summary_key
        from miniapp.backend._helpers import rich_text_plain, relation_ids_of
        sname = (rich_text_plain(page, "Сессия") or "").strip()
        if sname:
            cids = relation_ids_of(page, "👥 Клиенты")
            cid = cids[0] if cids else None
            cache_delete(session_summary_key(sname, cid))
    except Exception:
        pass
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(ritual_id, user_notion_id)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload_folder(content, file.filename or "ritual.jpg", "arcana-rituals")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")
    try:
        await update_page(ritual_id, {"Фото": {"url": url}})
    except Exception as e:
        logger.warning("Failed to set Фото URL on ritual: %s", e)
    return {"ok": True, "url": url}


from fastapi import Form


@router.post("/arcana/clients/{client_id}/object_photo")
async def upload_client_object_photo(
    client_id: str,
    file: UploadFile = FastAPIFile(...),
    note: str = Form(""),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Append «URL | note» в rich_text поле «Фото объектов» клиента."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(client_id, user_notion_id)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")

    url = await _cloudinary_upload_folder(content, file.filename or "object.jpg", "arcana-client-objects")
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")

    from miniapp.backend._helpers import rich_text_plain
    from core.client_object_photos import append as _append
    existing = rich_text_plain(page, "Фото объектов") or ""
    new_raw, items = _append(existing, url, note or "")
    try:
        await update_page(client_id, {"Фото объектов": _text(new_raw)})
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
    page = await _load_owned_page(client_id, user_notion_id)
    from miniapp.backend._helpers import rich_text_plain
    from core.client_object_photos import edit_note as _edit
    existing = rich_text_plain(page, "Фото объектов") or ""
    try:
        new_raw, items = _edit(existing, index, body.note or "")
    except IndexError:
        raise HTTPException(status_code=404, detail="object photo index out of range")
    await update_page(client_id, {"Фото объектов": _text(new_raw)})
    return {"ok": True, "photos": items}


@router.delete("/arcana/clients/{client_id}/object_photo/{index}")
async def delete_client_object_photo(
    client_id: str,
    index: int,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _load_owned_page(client_id, user_notion_id)
    from miniapp.backend._helpers import rich_text_plain
    from core.client_object_photos import delete as _delete
    existing = rich_text_plain(page, "Фото объектов") or ""
    try:
        new_raw, items = _delete(existing, index)
    except IndexError:
        raise HTTPException(status_code=404, detail="object photo index out of range")
    await update_page(client_id, {"Фото объектов": _text(new_raw)})
    return {"ok": True, "photos": items}


@router.post("/arcana/works/{work_id}/done")
async def arcana_work_done(
    work_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(work_id, user_notion_id)
    try:
        await update_page(work_id, {"Status": _status("Done")})
    except Exception as e:
        logger.error("arcana_work_done failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to update status")
    return {"ok": True, "status": "Done"}


@router.post("/arcana/works/{work_id}/cancel")
async def arcana_work_cancel(
    work_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(work_id, user_notion_id)
    try:
        await update_page(work_id, {"Status": _status("Archived")})
    except Exception as e:
        logger.error("arcana_work_cancel failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to cancel")
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
    return {"ok": True, "new_date": new_date.isoformat()}


@router.post("/arcana/rituals/{ritual_id}/result")
async def ritual_result(
    ritual_id: str,
    body: VerifyBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.status not in _RITUAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_RITUAL_STATUSES)}")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(ritual_id, user_notion_id)
    ok = await update_page_select(ritual_id, "Результат", body.status)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update Результат")
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
    page_id = await client_add(
        name=body.name,
        contact=body.contact,
        request=body.request,
        user_notion_id=user_notion_id,
        client_type=ctype,
    )
    if not page_id:
        raise HTTPException(status_code=500, detail="failed to create client")
    if body.status:
        await update_page_select(page_id, "Статус", body.status)
    extra: dict = {}
    if body.notes:
        extra["Заметки"] = _text(body.notes)
    if body.birthday:
        extra["День рождения"] = _date(body.birthday)
    if extra:
        try:
            await update_page(page_id, extra)
        except Exception as e:
            logger.warning("client_create extra fields write failed: %s", e)
    return {"ok": True, "id": page_id}


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
    page = await _load_owned_page(client_id, user_notion_id)
    # self-client (🌟 Self) — тип менять нельзя
    cur_type = (page.get("properties", {}).get("Тип клиента", {}) or {}).get("select") or {}
    cur_type_name = cur_type.get("name", "")
    is_self = cur_type_name == "🌟 Self"

    props: dict = {}
    if body.notes is not None:
        props["Заметки"] = _text(body.notes)
    if body.request is not None:
        props["Запрос"] = _text(body.request)
    if body.contact is not None:
        props["Контакт"] = _text(body.contact)
    if body.type is not None and not is_self:
        if body.type not in _CLIENT_TYPES_ALLOWED_EDIT:
            raise HTTPException(status_code=400, detail="invalid type")
        props["Тип клиента"] = _select(body.type)
    if body.birthday is not None:
        props["День рождения"] = _date(body.birthday) if body.birthday else {"date": None}

    if not props:
        return {"ok": True, "noop": True}

    try:
        await update_page(client_id, props)
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
    price: Optional[float] = None
    expires: Optional[str] = None


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

    props: dict = {
        "Название": _title(body.name),
        "Тип": _select(_LIST_TYPES[body.type]),
        "Статус": _status("Not started"),
        "Бот": _select(BOT_NEXUS),
    }
    if body.cat:
        props["Категория"] = _select(body.cat)
    if body.qty is not None:
        props["Количество"] = _number(float(body.qty))
    if body.note:
        props["Заметка"] = _text(body.note)
    if body.price is not None:
        props["Цена"] = _number(float(body.price))
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
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _load_owned_page(item_id, user_notion_id, allow_empty_owner=True)
    try:
        await update_page(item_id, {"Статус": _status("Done")})
    except Exception as e:
        logger.error("list_done failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to mark done")
    return {"ok": True}


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
