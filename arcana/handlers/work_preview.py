"""arcana/handlers/work_preview.py — preview-flow создания Работы (паритет с Nexus tasks).

Поток:
1. handle_add_work_preview парсит текст через Sonnet (PARSE_WORK_SYSTEM из works.py),
   НЕ пишет в Notion, создаёт pending в SQLite, шлёт превью карточки с
   [✅ Сохранить] [❌ Отмена].
2. Любое следующее сообщение от Кай (пока pending активен) парсится через
   Haiku → обновляет deadline / reminder / title в pending → редактирует превью.
3. work_save callback — создаёт запись в 🔮 Работы, ставит reminder через
   arcana_reminder_flow, удаляет pending, шлёт «⚡ Работа создана!».
4. work_cancel callback — удаляет pending, «❌ Отменено».
"""
from __future__ import annotations

import hashlib
import json
import logging
import os as _os
import re as _re
import sqlite3 as _sqlite3
import time as _time
import traceback as tb
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.claude_client import ask_claude
from core.notion_client import client_find, log_error, work_add
from core.shared_handlers import get_user_tz
from core.utils import cancel_button, react

logger = logging.getLogger("arcana.work_preview")

router = Router()

_PRIORITY_EMOJI = {"Срочно": "🔴", "Важно": "🟡", "Можно потом": "⚪"}

# ── SQLite pending store ──────────────────────────────────────────────────────
_PENDING_DB = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "../../pending_works.db"
)
_PENDING_TTL = 1800  # 30 минут — как в Nexus


def _pdb() -> _sqlite3.Connection:
    con = _sqlite3.connect(_PENDING_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending "
        "(uid INTEGER PRIMARY KEY, slug TEXT, data TEXT, ts REAL)"
    )
    con.commit()
    return con


def _make_slug(uid: int) -> str:
    raw = f"{uid}-{_time.time()}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _pending_set(uid: int, slug: str, data: dict) -> None:
    with _pdb() as con:
        con.execute(
            "INSERT OR REPLACE INTO pending (uid, slug, data, ts) VALUES (?,?,?,?)",
            (uid, slug, json.dumps(data, ensure_ascii=False), _time.time()),
        )


def _pending_get(uid: int) -> Optional[dict]:
    with _pdb() as con:
        row = con.execute(
            "SELECT slug, data, ts FROM pending WHERE uid=?", (uid,)
        ).fetchone()
    if not row:
        return None
    if _time.time() - row[2] > _PENDING_TTL:
        _pending_del(uid)
        return None
    data = json.loads(row[1])
    data["_slug"] = row[0]
    return data


def _pending_get_by_slug(slug: str) -> Optional[tuple]:
    """Вернуть (uid, data) или None."""
    with _pdb() as con:
        row = con.execute(
            "SELECT uid, data, ts FROM pending WHERE slug=?", (slug,)
        ).fetchone()
    if not row:
        return None
    if _time.time() - row[2] > _PENDING_TTL:
        with _pdb() as con:
            con.execute("DELETE FROM pending WHERE slug=?", (slug,))
        return None
    return row[0], json.loads(row[1])


def _pending_del(uid: int) -> None:
    with _pdb() as con:
        con.execute("DELETE FROM pending WHERE uid=?", (uid,))


def has_pending(uid: int) -> bool:
    return _pending_get(uid) is not None


def drop_pending(uid: int) -> None:
    _pending_del(uid)


# ── Эвристики «уточнение vs новое сообщение» ─────────────────────────────────

_DEADLINE_PATTERNS = _re.compile(
    r"\b("
    r"завтра|послезавтра|сегодня|вчера|на\s+неделе|без\s+срока|"
    r"через\s+\d+\s*(мин\w*|час\w*|ден\w*|дн\w*|недел\w*|месяц\w*)|"
    r"через\s+(полчаса|час)|за\s+(час|день|неделю)|"
    r"в\s+(понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|"
    r"воскресень\w*|пн|вт|ср|чт|пт|сб|вс)\b|"
    r"\d{1,2}[:.]\d{2}|\bв\s+\d{1,2}\b|"
    r"\b\d{1,2}\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|"
    r"сентябр|октябр|ноябр|декабр)\w*|"
    r"\d{4}-\d{2}-\d{2}|"
    r"утром|вечером|днём|ночью|после\s+обеда|до\s+обеда|"
    r"дедлайн|напомни|напомнить|без\s+напомин\w*|без\s+срока|без\s+дедлайн\w*"
    r")\b",
    _re.IGNORECASE,
)

_NEW_INTENT_PATTERNS = _re.compile(
    r"^\s*(сделать|сделай|сделала|сделай-ка|"
    r"создать|создай|добавь|добавить|"
    r"купить|купи|закупить|закажи|заказать|"
    r"позвонить|позвони|написать|напиши|отправить|отправь|"
    r"забрать|забери|починить|почини|"
    r"провести|провела|разложить|разложила|"
    r"клиент\w*|расклад\w*|ритуал\w*|сеанс\w*|"
    r"запис\w+|удали|удалить)\b",
    _re.IGNORECASE,
)


def looks_like_deadline_clarification(text: str) -> bool:
    """Текст похож на уточнение даты/времени для существующего pending."""
    return bool(_DEADLINE_PATTERNS.search(text or ""))


def looks_like_new_intent(text: str) -> bool:
    """Текст начинается как новое сообщение (новая задача/команда)."""
    return bool(_NEW_INTENT_PATTERNS.match(text or ""))


# ── Превью ────────────────────────────────────────────────────────────────────

def _format_preview(data: dict) -> str:
    title = data.get("title") or "Работа"
    category = data.get("category") or "—"
    priority = data.get("priority") or "Можно потом"
    pemoji = _PRIORITY_EMOJI.get(priority, "⚪")
    deadline = data.get("deadline")
    reminder = data.get("reminder")
    work_type = data.get("work_type") or "🌟 Личная"
    client_name = data.get("client_name")

    deadline_disp = (deadline or "не указан").replace("T", " ")
    reminder_disp = (reminder or "нет").replace("T", " ")

    lines = [
        f"📌 <b>{title}</b>",
        f"🏷 {category} · {pemoji} {priority}",
        f"📅 Дедлайн: {deadline_disp}",
        f"🔔 Напоминание: {reminder_disp}",
        f"👥 {work_type}" + (f" · {client_name}" if client_name else ""),
    ]

    if not deadline or not reminder:
        lines.append("")
        lines.append("❓ Уточни:")
        if not deadline:
            lines.append("— Когда сделать? («завтра», «1 июня», «через 2 дня»)")
        if not reminder:
            lines.append("— Напомнить? («в 10:00», «за час», «завтра в 15»)")
        lines.append("")
        lines.append("<i>Или нажми «Сохранить» как есть</i>")

    return "\n".join(lines)


def _kb(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"work_save:{slug}"),
        cancel_button("❌ Отмена", f"work_cancel:{slug}"),
    ]])


# ── Парсеры ───────────────────────────────────────────────────────────────────

async def _parse_work_text(text: str, tz_offset: int) -> dict:
    """Первичный парсинг через Haiku — короткая структура, Sonnet излишне.

    Промпт усилен few-shot примерами, чтобы Haiku стабильно отдавал JSON
    с правильными deadline-форматами и client_name.
    """
    from arcana.handlers.works import (
        PARSE_WORK_SYSTEM,
        WORK_CATEGORY_MAP,
        WORK_PRIORITY_MAP,
    )

    tz = timezone(timedelta(hours=tz_offset))
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    system = PARSE_WORK_SYSTEM + f"\n\nСейчас: {now_str} (UTC+{tz_offset})"
    raw = await ask_claude(
        text, system=system, max_tokens=300,
        model="claude-haiku-4-5-20251001",
    )
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(raw)

    title = data.get("title") or "Работа"
    deadline_raw = data.get("deadline") or None
    priority_raw = (data.get("priority") or "можно потом").lower()
    category_raw = (data.get("category") or "").lower()
    client_name = data.get("client_name") or None
    type_raw = (data.get("type") or "личная").lower()

    priority = WORK_PRIORITY_MAP.get(priority_raw, "Можно потом")
    category = WORK_CATEGORY_MAP.get(category_raw) if category_raw else None
    work_type = "🤝 Клиентская" if client_name or "клиент" in type_raw else "🌟 Личная"

    deadline = None
    if deadline_raw:
        deadline = deadline_raw.replace(" ", "T") if " " in deadline_raw else deadline_raw

    return {
        "title": title,
        "category": category,
        "priority": priority,
        "work_type": work_type,
        "client_name": client_name,
        "deadline": deadline,
        "reminder": None,
    }


async def _parse_clarification(text: str, tz_offset: int) -> dict:
    """Уточнение: парсим дедлайн/напоминание через Haiku (дёшево)."""
    tz = timezone(timedelta(hours=tz_offset))
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    is_night = datetime.now(tz).hour < 5
    night_note = (
        "ВАЖНО: сейчас ночь (до 05:00) — 'завтра' = СЕГОДНЯ (тот же календарный день)!"
        if is_night else ""
    )
    system = (
        "Пользователь уточняет когда сделать работу и/или когда напомнить.\n"
        "Верни ТОЛЬКО JSON без markdown:\n"
        '{"deadline": "YYYY-MM-DD или YYYY-MM-DDTHH:MM или null", '
        '"reminder": "YYYY-MM-DDTHH:MM или null"}\n\n'
        "Правила:\n"
        "- 'завтра' / 'в пятницу' / 'через 2 дня' → deadline\n"
        "- 'в 10:00' / 'за час' / 'завтра в 15' → reminder\n"
        "- 'дедлайн X' → deadline=X\n"
        "- 'напомни X' → reminder=X\n"
        f"{night_note}\n\nСейчас: {now_str} (UTC+{tz_offset})"
    )
    raw = await ask_claude(
        text, system=system, max_tokens=120,
        model="claude-haiku-4-5-20251001",
    )
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = json.loads(raw)
    out = {}
    dl = parsed.get("deadline")
    rm = parsed.get("reminder")
    if dl:
        out["deadline"] = dl.replace(" ", "T") if " " in dl else dl
    if rm:
        out["reminder"] = rm.replace(" ", "T") if " " in rm else rm
    return out


def _auto_reminder(deadline: str) -> str:
    """Дефолтное напоминание = дедлайн - 1 день (паттерн Nexus)."""
    iso = deadline if "T" in deadline else f"{deadline}T09:00"
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
    except Exception:
        return iso
    return (dt - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")


# ── Partial pending (нет деталей — ждём дополнения) ─────────────────────────

async def _save_partial_pending(
    message: Message, text: str, user_notion_id: str = "", parsed: dict = None
) -> None:
    """Сохраняем фрагмент как pending типа 'partial', шлём вопрос-уточнение.
    Следующее сообщение от Кай сольётся с фрагментом и пойдёт в обычный
    parse_work_text. В ⚠️ Ошибки НЕ пишем."""
    uid = message.from_user.id
    slug = _make_slug(uid)
    data = {
        "_partial": True,
        "fragment": text,
        "title": (parsed or {}).get("title") or "",
        "category": (parsed or {}).get("category"),
        "priority": (parsed or {}).get("priority") or "Можно потом",
        "work_type": (parsed or {}).get("work_type") or "🌟 Личная",
        "client_name": (parsed or {}).get("client_name"),
        "client_id": None,
        "deadline": (parsed or {}).get("deadline"),
        "reminder": None,
        "user_notion_id": user_notion_id,
        "msg_id": None,
        "chat_id": message.chat.id,
    }
    _pending_set(uid, slug, data)
    await message.answer(
        "❓ Что за работа? Опиши коротко:\n"
        "• категория (расклад / ритуал / соцсети / расходники)\n"
        "• для кого (если клиент)\n\n"
        "<i>Или /cancel чтобы отменить</i>"
    )


# ── Главные хендлеры ──────────────────────────────────────────────────────────

async def handle_add_work_preview(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    """Создать pending + показать превью. НЕ пишет в Notion."""
    try:
        uid = message.from_user.id
        tz_offset = await get_user_tz(uid)

        try:
            data = await _parse_work_text(text, tz_offset)
        except json.JSONDecodeError:
            # Короткий/неоднозначный ввод без структуры — сохраняем фрагмент
            # как pending и просим Кай дописать детали. В ⚠️ Ошибки НЕ пишем,
            # это нормальный пользовательский кейс, не сбой.
            await _save_partial_pending(message, text, user_notion_id)
            return
        # Парсер вернул JSON, но без сути (дефолтный title + нет категории/клиента/дедлайна)
        # — фрагментарный ввод, просим уточнить.
        is_partial = (
            (data.get("title") or "").strip() in ("", "Работа")
            and not data.get("category")
            and not data.get("client_name")
            and not data.get("deadline")
        )
        if is_partial:
            await _save_partial_pending(message, text, user_notion_id, parsed=data)
            return

        # Резолвим клиента сразу (чтобы при save не делать ещё запрос)
        client_id = None
        if data.get("client_name"):
            try:
                c = await client_find(data["client_name"], user_notion_id=user_notion_id)
                if c:
                    client_id = c["id"]
            except Exception:
                pass
        data["client_id"] = client_id
        data["user_notion_id"] = user_notion_id

        slug = _make_slug(uid)
        text_content = _format_preview(data)
        msg = await message.answer(
            text_content, parse_mode="HTML", reply_markup=_kb(slug)
        )
        data["msg_id"] = msg.message_id
        data["chat_id"] = msg.chat.id
        _pending_set(uid, slug, data)

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_add_work_preview error: %s", trace)
        await log_error(
            (message.text or "")[:200], "processing_error",
            traceback=trace, bot_label="🌒 Arcana", error_code="–",
        )
        await message.answer("❌ что-то сломалось · пусть Кай правит код")


async def handle_work_clarification(message: Message) -> bool:
    """Если у юзера активен pending — парсим уточнение, обновляем превью.
    Возвращает True если обработано."""
    uid = message.from_user.id
    pending = _pending_get(uid)
    if not pending:
        return False

    text = (message.text or "").strip()
    if not text:
        return False

    # Partial pending: склеиваем фрагмент с новым текстом и парсим заново.
    if pending.get("_partial"):
        combined = f"{pending.get('fragment', '')} {text}".strip()
        drop_pending(uid)
        await handle_add_work_preview(
            message, combined, pending.get("user_notion_id") or "",
        )
        return True

    is_deadline = looks_like_deadline_clarification(text)
    is_new = looks_like_new_intent(text)

    # Однозначно новое сообщение → дропаем pending, отдаём классификатору
    if is_new and not is_deadline:
        logger.info("work_pending dropped: new intent text=%r", text[:60])
        drop_pending(uid)
        return False

    # Ни то, ни другое → переспросить
    if not is_deadline and not is_new:
        from arcana.handlers.intent_resolve import ask_clarify_or_new
        await ask_clarify_or_new(
            message, text, pending.get("title") or "Работа",
        )
        return True

    tz_offset = await get_user_tz(uid)
    try:
        upd = await _parse_clarification(text, tz_offset)
    except Exception as e:
        logger.warning("parse_clarification failed: %s", e)
        upd = {}

    if not upd.get("deadline") and not upd.get("reminder"):
        await react(message, "🤔")
        await message.answer(
            "🤔 Не поняла уточнение. Примеры: <code>завтра</code>, "
            "<code>через 2 дня</code>, <code>в 10:00</code>"
        )
        return True

    if upd.get("deadline"):
        pending["deadline"] = upd["deadline"]
    if upd.get("reminder"):
        pending["reminder"] = upd["reminder"]

    slug = pending.pop("_slug")
    _pending_set(uid, slug, pending)

    text_content = _format_preview(pending)
    msg_id = pending.get("msg_id")
    try:
        await message.bot.edit_message_text(
            chat_id=pending.get("chat_id") or message.chat.id,
            message_id=msg_id,
            text=text_content,
            parse_mode="HTML",
            reply_markup=_kb(slug),
        )
    except Exception:
        new_msg = await message.answer(
            text_content, parse_mode="HTML", reply_markup=_kb(slug)
        )
        pending["msg_id"] = new_msg.message_id
        pending["chat_id"] = new_msg.chat.id
        _pending_set(uid, slug, pending)

    await react(message, "⚡")
    return True


# ── Callbacks ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("work_save:"))
async def cb_work_save(call: CallbackQuery) -> None:
    slug = call.data.split(":", 1)[1]
    found = _pending_get_by_slug(slug)
    if not found:
        await call.answer("⏰ Превью устарело", show_alert=True)
        try:
            await call.message.edit_reply_markup()
        except Exception:
            pass
        return
    uid, data = found
    if uid != call.from_user.id:
        await call.answer("Не твой превью.", show_alert=True)
        return

    try:
        result = await work_add(
            title=data.get("title") or "Работа",
            date=data.get("deadline") or "",
            priority=data.get("priority") or "Можно потом",
            category=data.get("category"),
            work_type=data.get("work_type") or "🌟 Личная",
            client_id=data.get("client_id"),
            user_notion_id=data.get("user_notion_id") or "",
        )
    except Exception as e:
        logger.error("work_add failed: %s", e)
        result = None

    if not result:
        await call.answer("⚠️ Ошибка записи в Notion", show_alert=True)
        return

    _pending_del(uid)

    # Reminder = указанное юзером ИЛИ deadline - 1 день
    reminder = data.get("reminder")
    if not reminder and data.get("deadline"):
        reminder = _auto_reminder(data["deadline"])

    tz_offset = await get_user_tz(uid)
    if reminder:
        try:
            from arcana.bot import arcana_reminder_flow
            from core.notion_client import update_page

            await arcana_reminder_flow.schedule_reminder(
                chat_id=call.message.chat.id,
                title=data.get("title") or "Работа",
                reminder_dt=reminder,
                page_id=result,
                tz_offset=int(tz_offset),
            )
            await update_page(result, {"Напоминание": {"date": {"start": reminder}}})
        except Exception as e:
            logger.warning("schedule reminder on save failed: %s", e)

    deadline_disp = (data.get("deadline") or "без даты").replace("T", " ")
    reminder_disp = (reminder or "нет").replace("T", " ")
    priority = data.get("priority") or "Можно потом"
    pemoji = _PRIORITY_EMOJI.get(priority, "⚪")
    category = data.get("category") or "—"
    title = data.get("title") or "Работа"
    work_type = data.get("work_type") or "🌟 Личная"
    client_name = data.get("client_name")

    text_content = (
        f"⚡ <b>Работа создана!</b>\n"
        f"📌 {title}\n"
        f"🏷 {category} · {pemoji} {priority}\n"
        f"📅 Дедлайн: {deadline_disp}\n"
        f"🔔 Напоминание: {reminder_disp}\n"
        f"👥 {work_type}"
        + (f" · {client_name}" if client_name else "")
    )

    if category == "✨ Ритуал":
        text_content += (
            "\n\n<i>Когда сделаешь — напиши «провела ритуал …» с деталями.</i>"
        )
    elif category == "🃏 Расклад":
        text_content += (
            "\n\n<i>Когда разложишь — напиши «расклад: карты …».</i>"
        )

    # Inline-кнопки: предложить разбить на подзадачи (паритет с Nexus)
    _id_prefix = result.replace("-", "")[:24]
    save_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📋 Подзадачи",
            callback_data=f"task_subtask_work_{_id_prefix}",
        ),
        InlineKeyboardButton(text="👌 Ок", callback_data="work_ok"),
    ]])

    try:
        await call.message.edit_text(
            text_content, parse_mode="HTML", reply_markup=save_kb,
        )
    except Exception:
        await call.message.answer(text_content, parse_mode="HTML", reply_markup=save_kb)

    # message_pages mapping для reply-обновлений
    try:
        from core.message_pages import save_message_page
        await save_message_page(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            page_id=result,
            page_type="work",
            bot="arcana",
        )
    except Exception:
        pass

    await call.answer("⚡ Работа создана")


@router.callback_query(F.data == "work_ok")
async def cb_work_ok(call: CallbackQuery) -> None:
    """«👌 Ок» — убрать клавиатуру под «Работа создана»."""
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("work_cancel:"))
async def cb_work_cancel(call: CallbackQuery) -> None:
    slug = call.data.split(":", 1)[1]
    found = _pending_get_by_slug(slug)
    if found:
        uid, _ = found
        if uid != call.from_user.id:
            await call.answer("Не твой превью.", show_alert=True)
            return
        _pending_del(uid)
    try:
        await call.message.edit_text("❌ Отменено", reply_markup=None)
    except Exception:
        pass
    await call.answer("😈")
    await react(call, "😈")
