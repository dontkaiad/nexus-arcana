"""arcana/handlers/base.py"""
from __future__ import annotations

import logging
import traceback as tb

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from arcana.handlers.reactions import reaction_for
from core.claude_client import ask_claude
from core.notion_client import log_error
from core.utils import react

router = Router()
logger = logging.getLogger("arcana.base")

_clarify: dict = {}  # user_id → original_text
_pending_unknown: dict = {}  # user_id → (text, user_notion_id, ts)

ROUTER_SYSTEM = """Сначала исправь опечатки, потом определи тип. Ответь ТОЛЬКО одним словом.

Примеры исправлений: расклд→расклад, ртуал→ритуал, клент→клиент, сенс→сеанс, расклд→расклад, клиент→клиент, ртуал→ритуал.

Типы:
new_client   — явное создание клиента: «создай», «добавь клиента», «новый клиент»
client_info  — поиск/просмотр клиента: просто «клиент Имя», «что у Ани», «покажи клиента»
session_search — поиск прошлых раскладов: «что падало на X», «расклады на X», «расклады про X», «покажи расклад про»
session      — новый сеанс/расклад/таро (с картами или описанием вопроса)
ritual       — ритуал
debt         — долги клиентов
tarot_interp — трактовка таро
delete       — удалить записи («удали», «удалить», «убери»)
work         — работа ПО ПРАКТИКЕ: «работа:», «расклад для X», «ритуал для X», «подготовить колоду», «закупить свечи», дедлайн по клиенту/ритуалу. НЕ бытовые дела.
work_done    — работа (по практике) сделана, выполнил работу
work_list    — список работ, что делать по практике
nexus        — финансы, расходы, доходы, заметки, покупки, а также БЫТОВЫЕ ЗАДАЧИ: «задача …», «таск …», «напомни купить», «сделать по дому», «позвонить маме». Если слово «задача» — всегда nexus, НЕ work.
finance      — финансы практики, сколько заработала, расходы, прибыль
grimoire_add    — записать в гримуар (заговор, рецепт, комбинация, заметка)
grimoire        — открыть гримуар, посмотреть записи
grimoire_search — поиск в гримуаре
verify       — отметить что расклад/ритуал сбылся/не сбылся
stats        — статистика, процент сбывшихся
unknown      — остальное"""


@router.message(Command("tz"))
async def cmd_tz(message: Message, user_notion_id: str = "") -> None:
    """Установить часовой пояс. /tz UTC+5 или /tz Екатеринбург"""
    from core.shared_handlers import handle_tz_command
    await handle_tz_command(message, user_notion_id)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🌒 <b>Привет! Я Arcana — твой цифровой гримуар и CRM практики.</b>\n\n"
        "Я умею:\n"
        "👥 <b>Клиенты</b> — вести базу, историю, долги\n"
        "🃏 <b>Расклады</b> — записывать, трактовать, статистика сбывшегося\n"
        "🕯️ <b>Ритуалы</b> — структурировать, отслеживать результаты\n"
        "💰 <b>Финансы</b> — доходы/расходы практики\n"
        "🗒️ <b>Списки</b> — расходники, инвентарь, чеклисты\n\n"
        "Пиши текстом или <code>/help</code> для команд."
    )


@router.message(Command("help"))
async def cmd_help(message: Message, user_notion_id: str = "") -> None:
    await message.answer(
        "🌒 <b>Arcana</b> — цифровой гримуар\n\n"

        "<b>Клиенты:</b>\n"
        "• «клиент Анна» — создать/найти\n"
        "• «что у Анны?» — досье\n"
        "• «сколько мне должны?» — долги\n\n"

        "<b>Расклады:</b>\n"
        "• «триплет, уэйт — шут, маг, жрица»\n"
        "• Фото расклада → трактовка\n"
        "• ✏️ Поправить → дополнить текстом\n\n"

        "<b>Ритуалы:</b>\n"
        "• «ритуал: очищение, дома, свечи»\n\n"

        "<b>Работы:</b>\n"
        "• «работа: расклад для Анны»\n\n"

        "<b>Поиск:</b>\n"
        "• «что падало на Вадима»\n"
        "• «расклады про отношения»\n\n"

        "<b>Ещё:</b>\n"
        "/list — расходники\n"
        "/finance — аналитика\n"
        "/stats — точность\n"
        "/grimoire — гримуар\n"
        "/tz UTC+3 — часовой пояс\n\n"

        "↩️ Реплай на любой ответ = дополнить\n\n"

        "Задачи/финансы/заметки → ☀️ @nexus_kailark_bot",
        parse_mode="HTML",
    )


async def _handle_tarot_correction(
    message: Message, correction_text: str, pending: dict, user_notion_id: str
) -> None:
    """Юзер правит трактовку — Claude корректирует по справочнику."""
    uid = message.from_user.id
    from arcana.handlers.sessions import (
        CORRECTION_PARSE_SYSTEM,
        TAROT_SYSTEM,
        _normalize_area,
        _parse_json_safe,
    )
    from arcana.pending_tarot import save_pending
    from arcana.tarot_loader import get_cards_context
    from core.claude_client import ask_claude
    from core.notion_client import client_find
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # ── Извлечь обновления полей (имя/вопрос/область) из правки ───────
    try:
        upd_raw = await ask_claude(
            correction_text, system=CORRECTION_PARSE_SYSTEM, max_tokens=200
        )
        upd = _parse_json_safe(upd_raw) or {}
    except Exception:
        upd = {}

    new_client_name = (upd.get("client_name") or "").strip() or None
    new_question = (upd.get("question") or "").strip() or None
    new_area = (upd.get("area") or "").strip() or None

    if new_client_name:
        pending["client_name"] = new_client_name
        pending["self_client_missing"] = False
        try:
            c = await client_find(new_client_name, user_notion_id=user_notion_id)
            pending["client_id"] = c["id"] if c else None
        except Exception:
            pending["client_id"] = None
    if new_question:
        pending["question"] = new_question
    if new_area:
        pending["area"] = _normalize_area(new_area)

    deck = pending.get("deck") or "Уэйт"
    card_names = [c.strip() for c in (pending.get("cards") or "").split(",") if c.strip()]
    bottom_card = pending.get("bottom_card") or ""
    ctx_cards = card_names + ([bottom_card] if bottom_card else [])
    cards_context = get_cards_context(deck, ctx_cards)

    system = (
        "Ты — ассистент-таролог. Пользователь правит трактовку.\n"
        "Скорректируй трактовку согласно замечанию. Остальное оставь как было.\n"
        "Ответь ПОЛНОЙ исправленной трактовкой.\n"
        "ВЫВОДИ ТОЛЬКО HTML с тегами <h3>, <b>, <i>, <p>. Никакого markdown "
        "(никаких **, __, ##, *, _). Структура: <h3>заголовок блока</h3>"
        "<p>текст с <b>выделениями</b></p>.\n"
    )
    if cards_context:
        system += f"\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"

    old_interp = pending.get("interpretation") or ""
    prompt = (
        f"Предыдущая трактовка:\n{old_interp}\n\n"
        f"Замечание: {correction_text}\n\n"
        f"Карты: {pending.get('cards')}\n"
        + (f"Дно колоды: {bottom_card}\n" if bottom_card else "")
        + f"Вопрос: {pending.get('question')}\n"
        f"Дай исправленную трактовку целиком."
    )

    new_interp = await ask_claude(
        prompt, system=system,
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
    )

    from core.html_sanitize import sanitize_interpretation
    from core.html_for_telegram import html_to_telegram
    new_interp = sanitize_interpretation(new_interp)
    new_interp_tg = html_to_telegram(new_interp)

    pending["interpretation"] = new_interp
    pending["awaiting_edit"] = False
    await save_pending(uid, pending)

    from core.utils import cancel_button, secondary_button
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"tarot_save:{uid}"),
        secondary_button("✏️ Поправить ещё", f"tarot_edit:{uid}"),
        cancel_button("❌ Отмена", f"tarot_cancel:{uid}"),
    ]])
    await message.answer(
        f"✏️ <b>Исправленная трактовка:</b>\n\n{new_interp_tg[:3500]}",
        reply_markup=kb,
        parse_mode="HTML",
    )
    if len(new_interp_tg) > 3500:
        await message.answer(new_interp_tg[3500:7000], parse_mode="HTML")


@router.message()
async def route_message(message: Message, user_notion_id: str = "", _text: str = "") -> None:
    _final_emoji = "⚡"
    try:
        # Начальная реакция «вижу сообщение»
        await react(message, "👀")

        # ── Reply на сообщение бота = дополнение записи ──────────────────
        if (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
            and (message.text or message.caption)
        ):
            from arcana.handlers.reply_update import handle_reply_update
            handled = await handle_reply_update(message, user_notion_id=user_notion_id)
            if handled:
                return

        if message.photo and not _text:
            from arcana.handlers.sessions import handle_tarot_photo
            await handle_tarot_photo(message, user_notion_id)
            _final_emoji = reaction_for("session")
            await react(message, _final_emoji)
            return

        from core.layout import maybe_convert
        text = _text or maybe_convert((message.text or message.caption or "").strip())
        if not text:
            await message.answer("Отправь текст или фото расклада.")
            await react(message, "🤔")
            return

        # reply-контекст
        if message.reply_to_message and message.reply_to_message.text:
            prev = maybe_convert(message.reply_to_message.text.strip())
            text = f"[контекст: {prev[:100]}]\n{text}"

        uid = message.from_user.id

        # ── Pending: режим сбора инфы о клиенте ─────────────────────────
        from arcana.pending_clients import get_pending_client
        pending_client = await get_pending_client(uid)
        if pending_client and pending_client.get("step") == "collecting":
            from arcana.handlers.clients import _handle_collecting
            await _handle_collecting(message, text, pending_client, user_notion_id)
            await react(message, reaction_for("new_client"))
            return

        # ── Pending: поиск в гримуаре ────────────────────────────────────
        from arcana.handlers.grimoire import check_pending_search
        if await check_pending_search(message, text):
            await react(message, reaction_for("grimoire_search"))
            return

        # ── Pending: ввод суммы оплаты / бартера ──────────────────────────
        from arcana.pending_tarot import get_pending
        pending = await get_pending(uid)
        _PAYMENT_PENDING_TYPES = {
            "awaiting_payment_amount", "awaiting_debt_amount",
            "awaiting_barter_what", "awaiting_barter_money",
        }
        if pending and (pending.get("type") or "") in _PAYMENT_PENDING_TYPES:
            from arcana.handlers.payment import handle_payment_text
            handled = await handle_payment_text(message, text, pending, user_notion_id)
            if handled:
                await react(message, "💰")
                return

        # ── Pending: правка трактовки ─────────────────────────────────────
        if pending and pending.get("awaiting_triplet_edit"):
            from arcana.handlers.sessions import handle_triplet_correction
            await handle_triplet_correction(message, text, pending, user_notion_id)
            await react(message, reaction_for("session"))
            return
        if pending and pending.get("awaiting_edit"):
            await _handle_tarot_correction(message, text, pending, user_notion_id)
            await react(message, reaction_for("session"))
            return

        # ── Флоу переспроса ──────────────────────────────────────────────────
        if uid in _clarify:
            original = _clarify.pop(uid)
            combined = f"{original}\nУточнение: {text}"
            intent2 = (await ask_claude(combined, system=ROUTER_SYSTEM, max_tokens=10)).strip().lower()

            if intent2 not in ("unknown", ""):
                text = combined
                intent = intent2
            else:
                logged = await log_error(combined, "unknown_type", bot_label="🌒 Arcana", error_code="–")
                notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
                await message.answer(f"🌒 Так и не поняла · {notion_status}")
                return
        else:
            intent = (await ask_claude(text, system=ROUTER_SYSTEM, max_tokens=10)).strip().lower()

        logger.info("intent=%s | %s", intent, text[:60])

        from arcana.handlers.clients import handle_add_client, handle_client_info, handle_debts
        from arcana.handlers.sessions import handle_add_session, handle_session_search, handle_tarot_interpret
        from arcana.handlers.rituals import handle_add_ritual
        from arcana.handlers.delete import handle_delete
        from arcana.handlers.works import handle_add_work, handle_work_done, handle_works_list
        from arcana.handlers.stats import handle_verify, handle_stats
        from arcana.handlers.finance import handle_arcana_finance
        from arcana.handlers.grimoire import handle_grimoire_add, handle_grimoire_menu, handle_grimoire_search, check_pending_search

        dispatch = {
            "new_client":   lambda: handle_add_client(message, text, user_notion_id),
            "session":        lambda: handle_add_session(message, text, user_notion_id),
            "session_search": lambda: handle_session_search(message, text, user_notion_id),
            "ritual":       lambda: handle_add_ritual(message, text, user_notion_id),
            "client_info":  lambda: handle_client_info(message, text, user_notion_id),
            "debt":         lambda: handle_debts(message, user_notion_id),
            "tarot_interp": lambda: handle_tarot_interpret(message, text),
            "delete":       lambda: handle_delete(message, text),
            "work":         lambda: handle_add_work(message, text, user_notion_id),
            "work_done":    lambda: handle_work_done(message, text, user_notion_id),
            "work_list":    lambda: handle_works_list(message, user_notion_id),
            "finance":         lambda: handle_arcana_finance(message, user_notion_id, text),
            "grimoire_add":    lambda: handle_grimoire_add(message, text, user_notion_id),
            "grimoire":        lambda: handle_grimoire_menu(message, user_notion_id),
            "grimoire_search": lambda: handle_grimoire_search(message, text, user_notion_id),
            "verify":          lambda: handle_verify(message, text, user_notion_id),
            "stats":        lambda: handle_stats(message, user_notion_id),
        }

        handler = dispatch.get(intent)
        if handler:
            await handler()
            _final_emoji = reaction_for(intent)
        elif intent == "nexus":
            await message.answer(
                "☀️ Задачи, финансы и заметки — в Nexus: @nexus_kailark_bot"
            )
            _final_emoji = reaction_for("nexus")
        elif intent in ("unknown", "") or not intent:
            # Первый раз не поняла — показать кнопки
            import time as _time
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            _pending_unknown[uid] = (text, user_notion_id, _time.time())
            short = text[:60]
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔮 Сеанс", callback_data=f"aunk_session_{uid}"),
                    InlineKeyboardButton(text="✨ Ритуал", callback_data=f"aunk_ritual_{uid}"),
                ],
                [
                    InlineKeyboardButton(text="👤 Клиент", callback_data=f"aunk_client_{uid}"),
                    InlineKeyboardButton(text="🃏 Расклад", callback_data=f"aunk_tarot_{uid}"),
                ]
            ])
            await message.answer(
                f"🤔 Не поняла «<b>{short}</b>»\nЧто сделать?",
                reply_markup=kb,
            )
            _final_emoji = reaction_for("unknown")
        else:
            logged = await log_error(text, "parse_error", bot_label="🌒 Arcana", error_code="–")
            notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
            await message.answer(f"❌ Не так ответил Claude · пусть Кай правит промпт · {notion_status}")
            _final_emoji = reaction_for("parse_error")

        await react(message, _final_emoji)

    except Exception as e:
        trace = tb.format_exc()
        logger.error("route_message error: %s", trace)
        err_str = str(e)
        if "529" in err_str:
            code, suffix = "529", "серверная ошибка Anthropic · попробуй позже"
        elif any(x in err_str for x in ("500", "502", "503")):
            code, suffix = "5xx", "серверная ошибка · попробуй позже"
        elif "timeout" in err_str.lower():
            code, suffix = "timeout", "запрос завис · попробуй ещё раз"
        elif any(x in err_str for x in ("401", "403", "404")):
            code, suffix = "4xx", "ошибка конфигурации · пусть Кай правит код"
        else:
            code, suffix = "–", "что-то сломалось · пусть Кай правит код"
        logged = await log_error(
            (message.text or "")[:200], "processing_error",
            traceback=trace, bot_label="🌒 Arcana", error_code=code
        )
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        await message.answer(f"❌ {suffix} · {notion_status}")
        await react(message, reaction_for("error"))


_UNKNOWN_TTL = 300  # 5 min


@router.callback_query(lambda c: c.data and c.data.startswith("aunk_"))
async def on_arcana_unknown(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Handle arcana unknown text → user chose action type."""
    import time as _time

    uid = query.from_user.id
    pending = _pending_unknown.pop(uid, None)
    if not pending or _time.time() - pending[2] > _UNKNOWN_TTL:
        await query.answer("⏰ Время истекло, отправь текст ещё раз")
        return

    original_text, stored_uid, _ = pending
    notion_id = stored_uid or user_notion_id

    # Parse action: aunk_session_123, aunk_ritual_123, etc.
    action = query.data.split("_")[1]  # session, ritual, client, tarot

    from arcana.handlers.clients import handle_add_client, handle_client_info
    from arcana.handlers.sessions import handle_add_session, handle_tarot_interpret
    from arcana.handlers.rituals import handle_add_ritual

    if action == "session":
        await handle_add_session(query.message, original_text, notion_id)
        await query.answer("🔮 Записываю сеанс")
    elif action == "ritual":
        await handle_add_ritual(query.message, original_text, notion_id)
        await query.answer("✨ Записываю ритуал")
    elif action == "client":
        await handle_add_client(query.message, original_text, notion_id)
        await query.answer("👤 Добавляю клиента")
    elif action == "tarot":
        await handle_tarot_interpret(query.message, original_text)
        await query.answer("🃏 Трактую расклад")
