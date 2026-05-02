"""arcana/handlers/payment.py — inline-оплата для расклада/ритуала.

Клавиатура и callbacks: 💵 Деньгами / 🎁 В подарок / 📅 В долг / 🔄 Бартер.
Используется в single-flow cb_tarot_save и в ритуальном save-flow.

Pending state хранится в pending_tarot.db (та же таблица, что для правки
трактовок) — это не идеально семантически, но единый источник для всех
ожиданий ввода у пользователя.
"""
from __future__ import annotations

import html
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.payment import (
    parse_amount, write_payment, resolve_barter_received,
)

logger = logging.getLogger("arcana.payment")

router = Router()

# ── Keyboards ───────────────────────────────────────────────────────────────


def _short(page_id: str) -> str:
    """Короткий id (32 hex без дефисов) для callback_data."""
    return page_id.replace("-", "")[:32]


def payment_keyboard(page_id: str, target: str) -> InlineKeyboardMarkup:
    """Главные 4 кнопки оплаты после сохранения расклада/ритуала на 🤝 Платный."""
    sid = _short(page_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 Деньгами", callback_data=f"pay_money:{target}:{sid}"),
            InlineKeyboardButton(text="🎁 В подарок", callback_data=f"pay_gift:{target}:{sid}"),
        ],
        [
            InlineKeyboardButton(text="📅 В долг", callback_data=f"pay_debt:{target}:{sid}"),
            InlineKeyboardButton(text="🔄 Бартер", callback_data=f"pay_barter:{target}:{sid}"),
        ],
    ])


def _barter_status_kb(page_id: str, target: str) -> InlineKeyboardMarkup:
    sid = _short(page_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Уже получила", callback_data=f"barter_done:{target}:{sid}"),
        InlineKeyboardButton(text="⏳ Жду", callback_data=f"barter_wait:{target}:{sid}"),
    ]])


def _barter_pending_kb(page_id: str, target: str) -> InlineKeyboardMarkup:
    sid = _short(page_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Получила", callback_data=f"barter_resolve:{target}:{sid}"),
        InlineKeyboardButton(
            text="💰 Конвертировать в деньги",
            callback_data=f"barter_to_money:{target}:{sid}",
        ),
    ]])


# ── helpers ────────────────────────────────────────────────────────────────

def _label_for_target(target: str) -> str:
    return "Расклад" if target == "sessions" else "Ритуал"


async def _resolve_full_id(short_id: str, target: str, user_notion_id: str) -> Optional[str]:
    """short_id (32 hex) → full Notion page_id для нужной БД."""
    from core.config import config
    from core.notion_client import query_pages, _with_user_filter
    db_id = (config.arcana.db_sessions if target == "sessions"
             else config.arcana.db_rituals)
    try:
        pages = await query_pages(
            db_id, filters=_with_user_filter(None, user_notion_id), page_size=200
        )
    except Exception as e:
        logger.warning("payment: page lookup failed: %s", e)
        return None
    for p in pages:
        pid = p.get("id", "").replace("-", "")
        if pid.startswith(short_id) or short_id.startswith(pid[:32]):
            return p.get("id", "")
    return None


# ── Pending state (через pending_tarot.db) ─────────────────────────────────


async def _set_pending(uid: int, state: dict) -> None:
    from arcana.pending_tarot import save_pending
    await save_pending(uid, state)


async def _get_pending(uid: int) -> Optional[dict]:
    from arcana.pending_tarot import get_pending
    return await get_pending(uid)


async def _clear_pending(uid: int) -> None:
    from arcana.pending_tarot import delete_pending
    await delete_pending(uid)


# ── Callbacks ──────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay_money:"))
async def cb_pay_money(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    await _set_pending(call.from_user.id, {
        "type": "awaiting_payment_amount", "target": target, "short_id": sid,
    })
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("💵 Сколько в рублях?")


@router.callback_query(F.data.startswith("pay_gift:"))
async def cb_pay_gift(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    from core.user_manager import get_user_notion_id
    uid = call.from_user.id
    user_notion_id = (await get_user_notion_id(uid)) or ""
    page_id = await _resolve_full_id(sid, target, user_notion_id)
    if not page_id:
        await call.message.answer("⚠️ Запись не найдена.")
        return
    try:
        await write_payment(page_id, target, "gift")
    except Exception:
        await call.message.answer("⚠️ Не получилось сохранить оплату.")
        return
    label = _label_for_target(target)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"🎁 {label} в подарок · бесплатно")


@router.callback_query(F.data.startswith("pay_debt:"))
async def cb_pay_debt(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    await _set_pending(call.from_user.id, {
        "type": "awaiting_debt_amount", "target": target, "short_id": sid,
    })
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("📅 Сколько должен(на)?")


@router.callback_query(F.data.startswith("pay_barter:"))
async def cb_pay_barter(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    await _set_pending(call.from_user.id, {
        "type": "awaiting_barter_what", "target": target, "short_id": sid,
    })
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("🔄 На что меняешь? Опиши коротко.")


@router.callback_query(F.data.startswith("barter_done:"))
async def cb_barter_done(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    pending = await _get_pending(call.from_user.id) or {}
    barter_what = pending.get("barter_what") or ""
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    page_id = await _resolve_full_id(sid, target, user_notion_id)
    if not page_id:
        await call.message.answer("⚠️ Запись не найдена.")
        return
    try:
        await write_payment(page_id, target, "barter_done", barter_what=barter_what)
    except Exception:
        await call.message.answer("⚠️ Не получилось сохранить.")
        return
    await _clear_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"🔄 Бартер «{html.escape(barter_what)}» получен", parse_mode="HTML")


@router.callback_query(F.data.startswith("barter_wait:"))
async def cb_barter_wait(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    pending = await _get_pending(call.from_user.id) or {}
    barter_what = pending.get("barter_what") or ""
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    page_id = await _resolve_full_id(sid, target, user_notion_id)
    if not page_id:
        await call.message.answer("⚠️ Запись не найдена.")
        return
    try:
        await write_payment(page_id, target, "barter_wait", barter_what=barter_what)
    except Exception:
        await call.message.answer("⚠️ Не получилось сохранить.")
        return
    await _clear_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        f"⏳ Жду бартер «{html.escape(barter_what)}»",
        parse_mode="HTML",
        reply_markup=_barter_pending_kb(page_id, target),
    )


@router.callback_query(F.data.startswith("barter_resolve:"))
async def cb_barter_resolve(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    page_id = await _resolve_full_id(sid, target, user_notion_id)
    if not page_id:
        return
    try:
        await resolve_barter_received(page_id, target)
    except Exception:
        await call.message.answer("⚠️ Не получилось закрыть бартер.")
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("🔄 Бартер получен ✓")


@router.callback_query(F.data.startswith("barter_to_money:"))
async def cb_barter_to_money(call: CallbackQuery) -> None:
    await call.answer()
    _, target, sid = call.data.split(":", 2)
    await _set_pending(call.from_user.id, {
        "type": "awaiting_barter_money", "target": target, "short_id": sid,
    })
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("💰 Сколько в рублях за бартер?")


# ── Text dispatcher (вызывается из base.route_message) ─────────────────────


async def handle_payment_text(
    message: Message, text: str, pending: dict, user_notion_id: str
) -> bool:
    """True если pending обработан и можно завершать; False если не наш."""
    ptype = pending.get("type") or ""
    if ptype not in (
        "awaiting_payment_amount", "awaiting_debt_amount",
        "awaiting_barter_what", "awaiting_barter_money",
    ):
        return False

    target = pending.get("target") or "sessions"
    sid = pending.get("short_id") or ""
    page_id = await _resolve_full_id(sid, target, user_notion_id)
    label = _label_for_target(target)

    if ptype == "awaiting_payment_amount":
        n = parse_amount(text)
        if n is None:
            await message.answer("Не поняла сумму, напиши число.")
            return True
        if not page_id:
            await _clear_pending(message.from_user.id)
            await message.answer("⚠️ Запись не найдена.")
            return True
        try:
            await write_payment(page_id, target, "money", amount=n)
        except Exception:
            await message.answer("⚠️ Не получилось записать оплату.")
            return True
        await _clear_pending(message.from_user.id)
        await message.answer(f"✅ Оплата {n}₽ за {label.lower()} сохранена")
        return True

    if ptype == "awaiting_debt_amount":
        n = parse_amount(text)
        if n is None:
            await message.answer("Не поняла сумму, напиши число.")
            return True
        if not page_id:
            await _clear_pending(message.from_user.id)
            await message.answer("⚠️ Запись не найдена.")
            return True
        try:
            await write_payment(page_id, target, "debt", amount=n)
        except Exception:
            await message.answer("⚠️ Не получилось записать.")
            return True
        await _clear_pending(message.from_user.id)
        await message.answer(f"📅 Долг {n}₽ за {label.lower()} зафиксирован")
        return True

    if ptype == "awaiting_barter_what":
        # Сохраняем барtre_what и спрашиваем статус.
        pending["barter_what"] = text.strip()
        await _set_pending(message.from_user.id, pending)
        if not page_id:
            await message.answer("⚠️ Запись не найдена.")
            await _clear_pending(message.from_user.id)
            return True
        await message.answer(
            "Уже получила или ещё ждёшь?",
            reply_markup=_barter_status_kb(page_id, target),
        )
        return True

    if ptype == "awaiting_barter_money":
        n = parse_amount(text)
        if n is None:
            await message.answer("Не поняла сумму, напиши число.")
            return True
        if not page_id:
            await _clear_pending(message.from_user.id)
            await message.answer("⚠️ Запись не найдена.")
            return True
        try:
            await write_payment(page_id, target, "barter_to_money", amount=n)
        except Exception:
            await message.answer("⚠️ Не получилось записать.")
            return True
        await _clear_pending(message.from_user.id)
        await message.answer(f"✅ Бартер конвертирован в {n}₽")
        return True

    return False
