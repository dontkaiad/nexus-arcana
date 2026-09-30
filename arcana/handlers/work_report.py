"""arcana/handlers/work_report.py — пошаговый отчёт при закрытии Работы (#301).

Раньше «сделано» на открытую Работу категории ✨ Ритуал просто помечало её
Done — никакой Ритуал с деталями не создавался вообще (расходники/подношения/
силы/структура терялись безвозвратно, не через одноразовый Haiku-парсинг, а
потому что для них в принципе не было форм). Здесь бот вместо этого задаёт
вопросы по каждому полю таблицы `rituals` по очереди (кнопка «⏭ Пропустить»
на каждом шаге) и в конце создаёт полноценный Ритуал, привязывает и закрывает
Работу (core/work_relation.py) — паритет с `arcana/handlers/rituals.py`.

Расклады (🃏 Расклад) и прочие категории Работ отчёта не проходят — там для
детального разбора нужны реальные карты, это отдельный флоу (handle_add_session).
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from arcana.pending_tarot import get_pending, save_pending, delete_pending
from arcana.repos.rituals_repo import goal_label, place_label
from core.shared_handlers import get_user_tz

logger = logging.getLogger("arcana.work_report")
router = Router()

PAYMENT_SOURCE_MAP = {
    "карта": "💳 Карта",
    "наличные": "💵 Наличные",
    "бартер": "🔄 Бартер",
}

_GOAL_OPTIONS = [
    ("привлечение", "🚀 Привлечение"), ("защита", "🛡️ Защита"),
    ("очищение", "🧹 Очищение"), ("любовь", "💞 Любовь"),
    ("финансы", "💎 Финансы"), ("деструктив", "🖤 Деструктив/Возврат"),
    ("развязка", "⚔️ Развязка/Отсечение"), ("приворот", "🔗 Приворот/Присушка"),
    ("другое", "🌀 Другое"),
]
_PLACE_OPTIONS = [
    ("дома", "🏠 Дома"), ("лес", "🌲 Лес"), ("погост", "✝️ Погост"),
    ("перекрёсток", "🛤️ Перекрёсток"), ("церковь", "⛪ Церковь"),
    ("водоём", "💧 Водоём"), ("поле", "🌾 Поле"), ("другое", "🌍 Другое"),
]
_PAYMENT_OPTIONS = [
    ("карта", "💳 Карта"), ("наличные", "💵 Наличные"), ("бартер", "🔄 Бартер"),
]

# (key, kind, question, options) — kind: "choice" | "text" | "number".
STEPS = [
    ("goal", "choice", "🎯 Цель ритуала?", _GOAL_OPTIONS),
    ("place", "choice", "📍 Где проводила?", _PLACE_OPTIONS),
    ("consumables", "text", "🌿 Расходники (через запятую)? Или «-» если не было.", None),
    ("consumables_cost", "number", "💸 Стоимость расходников, ₽? Или 0.", None),
    ("duration_min", "number", "⏱ Сколько заняло, минут? Или 0.", None),
    ("offerings", "text", "🕯️ Подношения? Или «-» если не было.", None),
    ("offerings_cost", "number", "💸 Сумма подношений, ₽? Или 0.", None),
    ("forces", "text", "⚡ К каким силам обращалась? Или «-».", None),
    ("structure", "text", "📜 Структура (последовательность)? Или «-».", None),
    ("notes", "text", "📝 Заметки — что ещё важно? Или «-».", None),
    ("amount", "number", "💰 Цена ритуала для клиента, ₽? Или 0.", None),
    ("paid", "number", "✅ Сколько уже оплатили, ₽? Или 0.", None),
    ("payment_source", "choice", "💳 Источник оплаты?", _PAYMENT_OPTIONS),
]


def _slug(uid: int) -> str:
    return hashlib.sha1(f"wr-{uid}-{time.time()}".encode()).hexdigest()[:12]


def _step_kb(slug: str, idx: int) -> InlineKeyboardMarkup:
    _key, kind, _q, options = STEPS[idx]
    rows: list = []
    if kind == "choice":
        row: list = []
        for code, label in options:
            row.append(InlineKeyboardButton(text=label, callback_data=f"wrep:{slug}:{idx}:{code}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"wrep_skip:{slug}:{idx}"),
        InlineKeyboardButton(text="❌ Прервать", callback_data=f"wrep_cancel:{slug}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_step(message: Message, slug: str, idx: int, title: str) -> None:
    _key, _kind, question, _options = STEPS[idx]
    header = f"🕯️ Отчёт по «{title}» ({idx + 1}/{len(STEPS)})\n\n{question}"
    await message.answer(header, reply_markup=_step_kb(slug, idx))


async def start_work_report(message: Message, work, user_id: str) -> None:
    """Запускается из handle_work_done для Работы категории ✨ Ритуал."""
    uid = message.from_user.id
    slug = _slug(uid)
    await save_pending(uid, {
        "type": "work_report",
        "slug": slug,
        "work_id": work.id,
        "client_id": work.client_id,
        "title": work.title,
        "user_id": user_id,
        "step": 0,
        "answers": {},
    })
    await _send_step(message, slug, 0, work.title)


async def _finalize(message: Message, pending: dict) -> None:
    uid = message.from_user.id
    answers = pending.get("answers") or {}
    work_id = pending.get("work_id")
    client_id = pending.get("client_id")
    title = pending.get("title") or "Ритуал"
    user_id = pending.get("user_id") or ""
    tz_offset = await get_user_tz(uid)
    today = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d")

    goal = answers.get("goal")
    place = answers.get("place")
    notes = answers.get("notes")
    payment_source_raw = answers.get("payment_source")
    payment_source = PAYMENT_SOURCE_MAP.get(payment_source_raw or "", payment_source_raw)
    amount = float(answers.get("amount") or 0)
    paid = float(answers.get("paid") or 0)
    offerings_cost = float(answers.get("offerings_cost") or 0)

    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    result = await PgRitualsRepo().create(
        name=title,
        date=today,
        ritual_type="Личный" if not client_id else "Клиентский",
        consumables=answers.get("consumables") or "",
        consumables_cost=float(answers.get("consumables_cost") or 0),
        duration_min=float(answers.get("duration_min") or 0),
        offerings=answers.get("offerings") or "",
        forces=answers.get("forces") or "",
        structure=answers.get("structure") or "",
        amount=amount,
        paid=paid,
        client_id=client_id,
        user_id=user_id,
        goal=goal,
        place=place,
        notes=notes,
        payment_source=payment_source,
        offerings_cost=offerings_cost if offerings_cost > 0 else None,
    )
    await delete_pending(uid)

    if not result:
        await message.answer("⚠️ Отчёт заполнен, но не смогла записать Ритуал в БД.")
        return

    work_closed = False
    try:
        from core.work_relation import set_event_work_id, close_work_as_done
        work_closed = await set_event_work_id("ritual", result.id, work_id)
        if work_closed:
            await close_work_as_done(work_id)
    except Exception as e:
        logger.warning("work_report: link/close work failed: %s", e)

    if amount > 0:
        try:
            from core.repos.finance_repo import _repo as _finance_repo
            client_name = None
            if client_id:
                from arcana.repos.clients_repo import ClientsRepo
                client = await ClientsRepo().find_by_id(client_id)
                client_name = client.name if client else None
            await _finance_repo.add(
                date=today, amount=amount, category="🔮 Практика", type_="💰 Доход",
                source=payment_source or "💳 Карта", bot_label="🌒 Arcana",
                description=f"🕯️ {title}" + (f" — {client_name}" if client_name else ""),
                user_id=user_id,
            )
        except Exception as e:
            logger.warning("work_report: finance_add failed: %s", e)

    debt = max(0, amount - paid)
    goal_display = goal_label(goal or "") if goal else ""
    place_display = place_label(place or "") if place else ""
    goal_place = " · ".join(x for x in (goal_display, place_display) if x)

    lines = ["🔥 Работа выполнена — Ритуал записан!", f"🕯️ {title}"]
    if goal_place:
        lines.append(goal_place)
    lines.append(f"📅 {today}")
    if amount:
        money = f"💰 {int(amount)}₽"
        if debt > 0:
            money += f" · ⚠️ долг {int(debt)}₽"
        lines.append(money)
    lines.append("✅ Работа закрыта" if work_closed else "⚠️ Работа не была закрыта автоматически")
    confirm_msg = await message.answer("\n".join(lines))

    from core.message_pages import save_message_page
    await save_message_page(
        chat_id=confirm_msg.chat.id, message_id=confirm_msg.message_id,
        page_id=result.id, page_type="ritual", bot="arcana",
    )


async def _advance(message: Message, pending: dict) -> None:
    idx = pending["step"] + 1
    if idx >= len(STEPS):
        await _finalize(message, pending)
        return
    pending["step"] = idx
    await save_pending(message.from_user.id, pending)
    await _send_step(message, pending["slug"], idx, pending.get("title") or "Ритуал")


async def handle_pending_text(message: Message, text: str, user_id: str) -> bool:
    """route_message: свободный текст пока висит work_report pending."""
    uid = message.from_user.id
    pending = await get_pending(uid)
    if not pending or pending.get("type") != "work_report":
        return False

    idx = pending["step"]
    key, kind, question, options = STEPS[idx]
    raw = (text or "").strip()

    if kind == "choice":
        code = raw.lower()
        valid = {c for c, _l in options}
        if code not in valid:
            await message.answer("🤔 Выбери один из вариантов кнопкой или напиши точное слово.")
            return True
        pending["answers"][key] = code
    elif kind == "number":
        if raw in ("-", ""):
            value = 0.0
        else:
            try:
                value = float(raw.replace(",", "."))
            except ValueError:
                await message.answer("🤔 Нужно число (или «-»/0).")
                return True
        pending["answers"][key] = value
    else:  # text
        pending["answers"][key] = None if raw == "-" else raw

    await _advance(message, pending)
    return True


@router.callback_query(F.data.startswith("wrep:"))
async def cb_work_report_choice(call: CallbackQuery) -> None:
    _prefix, slug, idx_s, value = call.data.split(":", 3)
    idx = int(idx_s)
    uid = call.from_user.id
    pending = await get_pending(uid)
    if not pending or pending.get("type") != "work_report" or pending.get("slug") != slug:
        await call.answer("⏰ Отчёт устарел", show_alert=True)
        return
    if pending["step"] != idx:
        await call.answer()
        return
    key, _kind, _q, _options = STEPS[idx]
    pending["answers"][key] = value
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await call.answer()
    await _advance(call.message, pending)


@router.callback_query(F.data.startswith("wrep_skip:"))
async def cb_work_report_skip(call: CallbackQuery) -> None:
    _prefix, slug, idx_s = call.data.split(":", 2)
    idx = int(idx_s)
    uid = call.from_user.id
    pending = await get_pending(uid)
    if not pending or pending.get("type") != "work_report" or pending.get("slug") != slug:
        await call.answer("⏰ Отчёт устарел", show_alert=True)
        return
    if pending["step"] != idx:
        await call.answer()
        return
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await call.answer("⏭")
    await _advance(call.message, pending)


@router.callback_query(F.data.startswith("wrep_cancel:"))
async def cb_work_report_cancel(call: CallbackQuery) -> None:
    slug = call.data.split(":", 1)[1]
    uid = call.from_user.id
    pending = await get_pending(uid)
    if not pending or pending.get("type") != "work_report" or pending.get("slug") != slug:
        await call.answer("⏰ Отчёт устарел", show_alert=True)
        return
    work_id = pending.get("work_id")
    title = pending.get("title") or "Работа"
    await delete_pending(uid)
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    from arcana.repos.works_repo import WorksRepo
    await WorksRepo().mark_done(work_id)
    await call.answer("❌ Отчёт прерван")
    await call.message.answer(f"🔥 Работа выполнена (без отчёта)!\n🔮 {title}")
