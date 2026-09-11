"""zarya/handlers.py — Zarya's router, role middleware and flows.

Role comes from the shared `grants` table (`core.auth_grants.booking_role`),
keyed on the sender's tg_id. Nobody is blocked outright — a guest just gets
the public view and a nudge. Group chats: only replies to /commands, an
@mention or a reply to the bot (Telegram privacy mode does most of this).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, TelegramObject,
)

from core.auth_grants import booking_role
from core import login_tokens as login_tokens_mod
from core.booking.busy import busy_intervals, merge_intervals
from core.booking.linkage import link_booking, unlink_booking
from core.booking.repo import (
    create_booking, get_booking, list_bookings, set_booking_status,
)
from core.booking.slots import free_slots
from core.config import config
from zarya import scheduler
from zarya.formatting import (
    MSK, day_phrase, epoch, extract_asked_date, from_epoch, group_slots_by_day,
    slot_label, wants_slots,
)

logger = logging.getLogger("zarya.handlers")
router = Router()

_ZARYA_USERNAME = "heylark_booking_bot"
_SLOT_DAYS = 21
_MAX_SLOT_BUTTONS = 8
_FRIEND_HOURS = (1, 2, 3, 4)


class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else 0
        data["tg_id"] = tg_id
        try:
            data["role"] = await booking_role(tg_id) if tg_id else "guest"
        except Exception as e:
            logger.warning("role resolve failed for %s: %s", tg_id, e)
            data["role"] = "guest"
        return await handler(event, data)


def _ctx_for(role: str) -> str:
    return "friends" if role in ("friend", "admin") else "arcana"


# ── защита от добавления в чужие конфы ───────────────────────────────────────
# Заря теперь знает больше про Кай (#228) — её нельзя пускать в группу, куда
# добавила не сама Кай, иначе личный контекст утечёт кому попало.

@router.my_chat_member()
async def on_membership_changed(update: ChatMemberUpdated) -> None:
    if update.chat.type not in ("group", "supergroup"):
        return
    if update.new_chat_member.status not in ("member", "administrator"):
        return  # не "добавили", а что-то другое (кикнули/вышла и т.п.)
    adder_id = update.from_user.id if update.from_user else 0
    if adder_id in config.allowed_ids:
        return
    logger.warning("Zarya added to chat %s by non-owner tg_id=%s — leaving", update.chat.id, adder_id)
    try:
        await update.bot.send_message(update.chat.id, "Меня добавляет только Кай 💅 Ухожу.")
    except Exception:
        pass
    try:
        await update.bot.leave_chat(update.chat.id)
    except Exception as e:
        logger.warning("leave_chat(%s) failed: %s", update.chat.id, e)


async def _owner_user_id() -> Optional[str]:
    from core.user_manager import get_user_id
    for tg in config.allowed_ids:
        try:
            uid = await get_user_id(tg)
        except Exception:
            uid = None
        if uid:
            return uid
    return None


async def _dm(bot, tg_id: int, text: str, kb=None) -> bool:
    """Send a DM, swallowing 'chat not found' / blocked-bot errors."""
    if not tg_id:
        return False
    try:
        await bot.send_message(tg_id, text, reply_markup=kb, disable_web_page_preview=True)
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("dm to %s failed: %s", tg_id, e)
        return False


async def _notify_owner(text: str, bot=None) -> None:
    """Booking event → DM to every owner tg_id + an audit line in topic 1182."""
    if bot:
        for owner in config.allowed_ids:
            await _dm(bot, owner, text)
    try:
        from core.bot_notify import notify_booking_log
        await notify_booking_log(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("owner log-topic notify failed: %s", e)


def _cancel_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"z:cx:{booking_id}"),
    ]])


async def _confirm_flow(b, bot) -> None:
    """Shared: booking just became confirmed → link to task/work + arm reminders."""
    linked = await link_booking(b)
    scheduler.schedule(linked)


def _slots_kb(ctx: str, slots) -> InlineKeyboardMarkup:
    rows = []
    for s in slots[:_MAX_SLOT_BUTTONS]:
        rows.append([InlineKeyboardButton(
            text=slot_label(s), callback_data=f"z:slot:{ctx}:{epoch(s)}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── /start ──────────────────────────────────────────────────────────────────

def _login_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Войти", callback_data=f"z:login_ok:{token}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"z:login_no:{token}"),
    ]])


@router.message(Command("start"))
async def cmd_start(msg: Message, command: CommandObject = None, role: str = "guest") -> None:
    # #23 follow-up: /start login_<token> deep link from login.heylark.dev —
    # Заря подтверждает вход вместо номера телефона в Telegram Login Widget.
    payload = (command.args if command else None) or ""
    if payload.startswith("login_"):
        token = payload[len("login_"):]
        pending = await login_tokens_mod.get_pending(token)
        if pending is None:
            await msg.answer("⚠️ Ссылка на вход не найдена — попробуй войти заново на сайте.")
            return
        if pending["status"] == "expired":
            await msg.answer("⏱ Ссылка на вход устарела — обнови страницу логина и попробуй снова.")
            return
        if pending["status"] != "pending":
            await msg.answer("Эта ссылка уже использована.")
            return
        await msg.answer(
            "⭐ Привет! Я Заря — личный ассистент Кай Ларк.\n"
            "Жми кнопку ниже, чтобы войти в сервисы heylark.dev 👇",
            reply_markup=_login_kb(token),
        )
        return
    if role == "admin":
        await msg.answer(
            "⭐ Привет, это я! У Кай под контролем 💅\n\n"
            "/requests — заявки, которые ждут подтверждения\n"
            "/bookings — что уже подтверждено (можно отменить)\n"
            "/slots — глянуть свободное время\n"
            "/help — если забыла, что я умею"
        )
        return
    who = "друг" if role == "friend" else "гость"
    await msg.answer(
        f"⭐ Привет! Я Заря — личная ассистентка Кай.\n"
        f"Сейчас ты у меня как <b>{who}</b>.\n\n"
        "Спроси «когда у Кай окно» или напиши /slots — покажу свободное время "
        "и помогу записаться. /help — если что-то непонятно."
    )


@router.message(Command("help"))
async def cmd_help(msg: Message, role: str = "guest") -> None:
    if role == "admin":
        await msg.answer(
            "⭐ Вот что я умею:\n\n"
            "/requests — заявки на подтверждение (от гостей — из Арканы)\n"
            "/bookings — подтверждённые встречи, отмена по кнопке\n"
            "/slots — твоё свободное время как ты его увидят другие\n"
            "Или через браузер — <a href=\"https://booking.heylark.dev\">booking.heylark.dev</a> "
            "(там же настройки окон).\n\n"
            "Подтверждённым — напоминаю за сутки и за 2 часа, обеим сторонам. "
            "Отмена — кнопкой у брони, с обеих сторон.\n\n"
            "И вообще — можешь просто поболтать со мной, не только по делу 💅"
        )
        return
    who = "как другу" if role == "friend" else "как гостю"
    tail = (
        "\n\nИ можно просто поболтать 💅 не только по записи."
        if role == "friend" else ""
    )
    await msg.answer(
        f"⭐ Вот что я умею ({who}):\n\n"
        "/slots — покажу свободные окна у Кай\n"
        "Просто спроси «когда у Кай окно» — тоже сработает.\n"
        "Выбираешь слот → жмёшь кнопку → готово, я записала.\n\n"
        "Или удобнее через браузер — <a href=\"https://booking.heylark.dev\">booking.heylark.dev</a>, "
        "как тебе больше нравится.\n\n"
        "Захочешь отменить — кнопка «❌ Отменить бронь» будет прямо у записи."
        + tail
    )


# ── show slots ──────────────────────────────────────────────────────────────

async def _show_slots(msg: Message, role: str) -> None:
    uid = await _owner_user_id()
    if not uid:
        await msg.answer("Так, минутку — данные Кай сейчас недоступны. Загляни чуть позже 💅")
        return
    ctx = _ctx_for(role)
    today = date.today()
    # #235: «есть слоты на вторник» раньше игнорировался — отдавала общий
    # список на _SLOT_DAYS дней вперёд, даже если вторник туда не попадал.
    # Явно назван день → отвечаем прямо про него (да/нет), без общего дампа.
    asked = extract_asked_date(msg.text or "", today)
    if asked is not None:
        await _show_slots_for_day(msg, role, ctx, uid, asked)
        return
    # #229: free_slots() возвращает Slot(start, end) объекты, а весь
    # zarya/formatting.py (group_slots_by_day/slot_label/epoch) исторически
    # писан под голые datetime — .astimezone() падал с AttributeError. Раньше
    # сюда просто никогда не доходило (identity_repo падал раньше, #228).
    slots = [s.start for s in await free_slots(uid, ctx, day_from=today, day_to=today + timedelta(days=_SLOT_DAYS))]
    if not slots:
        # gib the opaque busy view as a fallback so the answer isn't empty
        now = datetime.now(timezone.utc)
        merged = merge_intervals(await busy_intervals(uid, now, now + timedelta(days=7)))
        if role == "guest":
            await msg.answer(
                "Свободных окон под запись сейчас нет 💅\n"
                "Публичная запись: <a href=\"https://booking.heylark.dev\">booking.heylark.dev</a>"
            )
        else:
            busy_txt = "\n".join(
                f"• занято {s.astimezone(MSK):%d.%m %H:%M}–{e.astimezone(MSK):%H:%M}"
                for s, e in merged[:6]
            ) or "• ничем не занята, но свободного часа в диапазоне не нашла — странно 🤔"
            await msg.answer("Забита под завязку в ближайшую неделю:\n" + busy_txt)
        return

    by_day = group_slots_by_day(slots)
    flat = [s for _, day in by_day for s in day]
    lines = ["🗓 <b>Свободно:</b>"]
    for header, day in by_day:
        lines.append(f"\n<b>{header}</b>: " + ", ".join(f"{s.astimezone(MSK):%H:%M}" for s in day))
    kb = _slots_kb(ctx, flat)
    tail = "\n\nВыбери слот 👇" if kb.inline_keyboard else ""
    await msg.answer("\n".join(lines) + tail, reply_markup=kb, disable_web_page_preview=True)


async def _show_slots_for_day(msg: Message, role: str, ctx: str, uid: str, asked) -> None:
    """#235: спросили про конкретный день — прямой да/нет, без общего дампа."""
    when = f"{day_phrase(asked)} ({asked:%d.%m})"
    slots = [s.start for s in await free_slots(uid, ctx, day_from=asked, day_to=asked)]
    if not slots:
        await msg.answer(f"Не, {when} не выйдет — плотно занята 💅")
        return
    times = ", ".join(f"{s.astimezone(MSK):%H:%M}" for s in slots)
    kb = _slots_kb(ctx, slots)
    tail = "\n\nВыбери слот 👇" if kb.inline_keyboard else ""
    await msg.answer(f"Да! {when} свободна: {times}{tail}", reply_markup=kb)


@router.message(Command("slots"))
async def cmd_slots(msg: Message, role: str = "guest") -> None:
    await _show_slots(msg, role)


@router.message(F.text.func(lambda t: wants_slots(t or "")))
async def nl_slots(msg: Message, role: str = "guest") -> None:
    # #228: с выключенным Group Privacy бот видит ВСЮ переписку группы — обычная
    # реплика ("открой окно", "у меня свободное время сегодня") легко матчит
    # regex без всякого обращения к боту. Тот же гейт, что и в on_unrecognized.
    if not _bot_addressed(msg):
        return
    await _show_slots(msg, role)


# ── slot picked ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("z:slot:"))
async def on_slot(call: CallbackQuery, role: str = "guest") -> None:
    _, _, ctx, ep = call.data.split(":", 3)
    if ctx == "friends" and role not in ("friend", "admin"):
        await call.answer("Для этого нужен доступ друга — напиши Кай 🙂", show_alert=True)
        return
    if ctx == "friends":
        rows = [[InlineKeyboardButton(text=f"{h} ч", callback_data=f"z:book:{ctx}:{ep}:{h}")]
                for h in _FRIEND_HOURS]
        await call.message.edit_text(
            f"Отлично! На сколько часов тебя занять? ({slot_label(from_epoch(ep))})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    else:
        await _do_book(call, ctx, ep, 1.0, role)
    await call.answer()


@router.callback_query(F.data.startswith("z:book:"))
async def on_book(call: CallbackQuery, role: str = "guest") -> None:
    parts = call.data.split(":")
    ctx, ep = parts[2], parts[3]
    hours = float(parts[4]) if len(parts) > 4 else 1.0
    await _do_book(call, ctx, ep, hours, role)
    await call.answer()


async def _do_book(call: CallbackQuery, ctx: str, ep: str, hours: float, role: str) -> None:
    uid = await _owner_user_id()
    start = from_epoch(ep)
    end = start + timedelta(hours=hours)
    if start <= datetime.now(timezone.utc):
        await call.message.edit_text("Этот слот уже пролетел 💅")
        return
    clash = await busy_intervals(uid, start, end)
    if any(iv.overlaps(start, end) for iv in clash):
        await call.message.edit_text("Слот только что увели из-под носа. Спроси окна ещё раз — /slots 💅")
        return

    status = "confirmed" if ctx == "friends" else "pending"
    # #237: имя, которое Кай сама вписала (people.display_name) — вместо
    # ника/имени в Telegram, чтобы не путать друзей.
    from core.auth_grants import get_display_name
    override_name = await get_display_name(call.from_user.id)
    name = override_name or (call.from_user.full_name or "").strip() or f"tg:{call.from_user.id}"
    b = await create_booking(
        user_id=uid, context=ctx, start_at=start, end_at=end, status=status,
        hours=hours, requester_tg_id=call.from_user.id, requester_name=name,
        source="tg_group" if call.message.chat.type in ("group", "supergroup") else "tg_dm",
    )
    when = slot_label(start, hours=hours)
    in_group = call.message.chat.type in ("group", "supergroup")
    if status == "confirmed":
        await call.message.edit_text(
            f"✅ Записала: {when}\nНапомню за сутки и за 2 часа 💫",
            reply_markup=_cancel_kb(b.id),
        )
        await _confirm_flow(b, call.bot)
        await _notify_owner(f"⭐ <b>{name}</b> записался · {when}", bot=call.bot)
        if in_group:  # requester booked in a group chat → confirm in DM too
            await _dm(
                call.bot, call.from_user.id,
                f"✅ Записала тебя к Кай: {when} (МСК). Напомню заранее.",
                _cancel_kb(b.id),
            )
    else:
        await call.message.edit_text(
            f"📝 Заявка на {when} принята! Как только Кай подтвердит — сразу напишу тебе."
        )
        await _notify_owner(
            f"🃏 <b>Заявка</b>: {name} · {when}\n"
            f"/requests чтобы подтвердить (#{b.id})",
            bot=call.bot,
        )


# ── admin cockpit ───────────────────────────────────────────────────────────

@router.message(Command("requests"))
async def cmd_requests(msg: Message, role: str = "guest") -> None:
    if role != "admin":
        await msg.answer("Это только для Кай 🙂")
        return
    uid = await _owner_user_id()
    pend = await list_bookings(uid or "", statuses=("pending",), upcoming_only=True)
    if not pend:
        await msg.answer("Заявок на подтверждение нет ✨")
        return
    for b in pend:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"z:ok:{b.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"z:no:{b.id}"),
        ]])
        await msg.answer(
            f"🃏 #{b.id} · <b>{b.requester_name}</b> · {slot_label(b.start_at, hours=b.hours or 1)}"
            + (f"\n💬 {b.note}" if b.note else ""),
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("z:ok:"))
async def on_approve(call: CallbackQuery, role: str = "guest") -> None:
    if role != "admin":
        await call.answer("Это не тебе 🙂", show_alert=True)
        return
    b = await set_booking_status(int(call.data.split(":")[2]), "confirmed")
    if not b:
        await call.answer("Не нашла заявку", show_alert=True)
        return
    when = slot_label(b.start_at, hours=b.hours or 1)
    await call.message.edit_text(f"✅ #{b.id} подтверждена — {b.requester_name} · {when}")
    await _confirm_flow(b, call.bot)
    await _dm(
        call.bot, b.requester_tg_id or 0,
        f"✅ Кай подтвердила твою запись: {when} (МСК).\n"
        f"Напомню за сутки и за 2 часа 💫",
        _cancel_kb(b.id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("z:no:"))
async def on_reject(call: CallbackQuery, role: str = "guest") -> None:
    if role != "admin":
        await call.answer("Это не тебе 🙂", show_alert=True)
        return
    b = await set_booking_status(int(call.data.split(":")[2]), "declined")
    await call.message.edit_text(f"❌ #{b.id} отклонена — {b.requester_name}")
    if b:
        when = slot_label(b.start_at, hours=b.hours or 1)
        await _dm(
            call.bot, b.requester_tg_id or 0,
            f"К сожалению, {when} не выйдет 🙏\nПосмотри другие окна: /slots",
        )
    await call.answer()


@router.callback_query(F.data.startswith("z:cx:"))
async def on_cancel(call: CallbackQuery, role: str = "guest", tg_id: int = 0) -> None:
    bid = int(call.data.split(":")[2])
    b = await get_booking(booking_id=bid)
    if not b:
        await call.answer("Бронь не найдена", show_alert=True)
        return
    if b.status not in ("pending", "confirmed"):
        await call.answer("Бронь уже неактуальна", show_alert=True)
        return
    is_owner = role == "admin"
    is_requester = bool(tg_id) and tg_id == b.requester_tg_id
    if not (is_owner or is_requester):
        await call.answer("Это не твоя бронь", show_alert=True)
        return
    status = "cancelled_by_owner" if is_owner else "cancelled_by_requester"
    b2 = await set_booking_status(bid, status) or b
    scheduler.cancel(bid)
    await unlink_booking(b2)
    when = slot_label(b.start_at, hours=b.hours or 1)
    try:
        await call.message.edit_text(f"❌ Бронь #{bid} отменена — {when}")
    except Exception:  # noqa: BLE001 — message may be too old to edit
        pass
    if is_owner:
        await _dm(
            call.bot, b.requester_tg_id or 0,
            f"❌ Кай отменила встречу {when} (МСК). Извини!\nМожно выбрать другое: /slots",
        )
    else:
        await _notify_owner(
            f"❌ <b>{b.requester_name}</b> отменил(а) бронь · {when}", bot=call.bot
        )
    await call.answer("Отменено")


# ── /start login_<token> confirm/deny ────────────────────────────────────────

@router.callback_query(F.data.startswith("z:login_ok:"))
async def on_login_confirm(call: CallbackQuery, tg_id: int = 0) -> None:
    token = call.data.split(":", 2)[2]
    ok = await login_tokens_mod.approve(token, tg_id)
    if not ok:
        await call.message.edit_text("⚠️ Ссылка уже неактуальна.")
        await call.answer()
        return
    await call.message.edit_text("✅ Вход подтверждён! Возвращайся на сайт — там уже открыто.")
    await call.answer()


@router.callback_query(F.data.startswith("z:login_no:"))
async def on_login_deny(call: CallbackQuery, tg_id: int = 0) -> None:
    token = call.data.split(":", 2)[2]
    await login_tokens_mod.deny(token, tg_id)
    await call.message.edit_text("❌ Вход отклонён.")
    await call.answer()


@router.message(Command("bookings"))
async def cmd_bookings(msg: Message, role: str = "guest") -> None:
    if role != "admin":
        await msg.answer("Это только для Кай 🙂")
        return
    uid = await _owner_user_id()
    up = await list_bookings(uid or "", statuses=("confirmed",), upcoming_only=True)
    if not up:
        await msg.answer("Подтверждённых встреч впереди нет ✨")
        return
    for b in up:
        when = slot_label(b.start_at, hours=b.hours or 1)
        await msg.answer(
            f"✅ #{b.id} · <b>{b.requester_name}</b> · {when}"
            + (f"\n💬 {b.note}" if b.note else ""),
            reply_markup=_cancel_kb(b.id),
        )


# ── fallback: любой текст, что не подошёл ни под одно из выше — ЗАРЕГИСТРИРОВАН
# ПОСЛЕДНИМ (aiogram матчит хендлеры по порядку сверху вниз, первый матч выигрывает).
# Без этого в группе (где Telegram и так фильтрует по privacy-mode — сюда доходит
# только команда/@упоминание/реплай боту) любое сообщение мимо wants_slots()
# просто пропадало без ответа. #226.
#
# #226 follow-up: ОДИН Haiku-вызов (zarya/classifier.py) только для того, что
# уже не поймал бесплатный regex/команды — обычный трафик по-прежнему стоит
# ноль токенов. Haiku либо роутит в существующий детерминированный хендлер
# (slots/help), либо сама пишет короткий ответ в характере (intent=chat).
def _bot_addressed(msg: Message) -> bool:
    """В группе — только явное обращение (тег/реплай боту), иначе она читает
    ВСЁ (Group Privacy теперь выключен, #226) и лезла бы с «не поняла» на
    любую реплику между людьми. В ЛС — всегда обращаются к ней."""
    if msg.chat.type not in ("group", "supergroup"):
        return True
    reply = getattr(msg, "reply_to_message", None)
    if reply and getattr(reply, "from_user", None) and reply.from_user.id == msg.bot.id:
        return True
    text_low = (msg.text or "").lower()
    for ent in msg.entities or []:
        if ent.type == "mention":
            mention = text_low[ent.offset:ent.offset + ent.length]
            if mention == f"@{_ZARYA_USERNAME}":
                return True
    return False


@router.message(F.text)
async def on_unrecognized(msg: Message, role: str = "guest") -> None:
    if not _bot_addressed(msg):
        return
    try:
        from zarya.classifier import classify_zarya
        result = await classify_zarya(msg.text or "", role=role)
    except Exception as e:
        logger.warning("zarya classify failed: %s", e)
        result = {"intent": "chat", "reply": ""}

    if result["intent"] == "slots":
        await _show_slots(msg, role)
    elif result["intent"] == "help":
        await cmd_help(msg, role=role)
    elif result["reply"]:
        await msg.answer(result["reply"])
    else:
        await msg.answer("Не поняла 💅 Спроси «когда у Кай окно» или напиши /help — покажу что умею.")
