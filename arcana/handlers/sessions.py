"""arcana/handlers/sessions.py"""
from __future__ import annotations

import base64
import html
import json
import logging
import traceback as tb
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.claude_client import ask_claude, ask_claude_vision
from core.notion_client import (
    _extract_text,
    client_find,
    finance_add,
    log_error,
    session_add,
    sessions_by_client,
    sessions_search,
)
from core.shared_handlers import get_user_tz

logger = logging.getLogger("arcana.sessions")

router = Router()

# ────────────────────────── Справочники ────────────────────────────────────

SPREAD_MAP = {
    "триплет":                     "🔺 Триплет",
    "3 карты":                     "🔺 Триплет",
    "три карты":                   "🔺 Триплет",
    "сфера":                       "🌐 Сфера жизни",
    "сфера жизни":                 "🌐 Сфера жизни",
    "кельтский":                   "✝️ Кельтский крест",
    "кельтский крест":             "✝️ Кельтский крест",
    "celtic cross":                "✝️ Кельтский крест",
    "воздействия":                 "⚡ Магические воздействия",
    "магические воздействия":      "⚡ Магические воздействия",
    "диагностика перед ритуалом":  "🔍 Диагностика перед ритуалом",
    "диагностика":                 "🔍 Диагностика перед ритуалом",
    "способности":                 "✨ Диагностика способностей",
    "диагностика способностей":    "✨ Диагностика способностей",
    "родовой":                     "🌳 Родовой узел",
    "родовой узел":                "🌳 Родовой узел",
}

PAYMENT_SOURCE_MAP = {
    "карта":     "💳 Карта",
    "наличные":  "💵 Наличные",
    "бартер":    "🔄 Бартер",
}

DECK_MAP = {
    "уэйт":         "Уэйт",
    "dark wood":    "Dark Wood",
    "дарк вуд":     "Dark Wood",
    "дарквуд":      "Dark Wood",
    "ленорман":     "Ленорман",
    "игральн":      "Игральные",
    "deviant moon": "Deviant Moon",
    "девиант мун":  "Deviant Moon",
}

AREA_VALUES = {"Отношения", "Финансы", "Работа", "Здоровье", "Род", "Общая ситуация"}
AREA_DEFAULT = "Общая ситуация"


def _match_spread(text: str) -> str:
    if not text:
        return ""
    low = text.strip().lower()
    if low in SPREAD_MAP:
        return SPREAD_MAP[low]
    for key, value in SPREAD_MAP.items():
        if key in low or low in key:
            return value
    return text.strip()


def _match_deck(text: str) -> str:
    """Нормализует название колоды: 'Уэйта'/'уэйту' → 'Уэйт' и т.п."""
    if not text:
        return ""
    low = text.strip().lower()
    for key, value in DECK_MAP.items():
        if low.startswith(key) or key in low:
            return value
    return text.strip()


def _normalize_area(text: str) -> str:
    """Нормализует область к одному из AREA_VALUES. Дефолт — 'Общая ситуация'."""
    if not text:
        return AREA_DEFAULT
    low = text.strip().lower()
    for value in AREA_VALUES:
        if low == value.lower() or value.lower() in low or low in value.lower():
            return value
    return AREA_DEFAULT


def _now_iso(tz: timezone) -> str:
    return datetime.now(tz).isoformat()


# ────────────────────────── Промпты ────────────────────────────────────────

PARSE_SESSION_SYSTEM = (
    "Извлеки данные о сеансе таро. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "spread_type": "тип расклада", '
    '"question": "конкретный вопрос", "cards": "карты через запятую или null", '
    '"bottom_card": "карта или null", '
    '"area": "Отношения|Финансы|Работа|Здоровье|Род|Общая ситуация", '
    '"deck": "Уэйт|Dark Wood|Ленорман|Игральные|Deviant Moon или null", '
    '"amount": число, "paid": число, '
    '"payment_source": "карта|наличные|бартер или null"}\n\n'
    "ОБЯЗАТЕЛЬНО: question — конкретный вопрос клиента, короткий (3-7 слов), "
    "с именами если есть. Примеры:\n"
    "- 'что думает Вадим обо мне' → 'Что думает Вадим'\n"
    "- 'будем ли вместе с Машей' → 'Будут ли отношения с Машей'\n"
    "- 'триплет уэйт отношения' (без вопроса) → 'Отношения — общий расклад'\n"
    "- 'что на работе ждёт' → 'Перспективы на работе'\n"
    "- 'расклад по здоровью мамы' → 'Здоровье мамы'\n\n"
    "ОБЯЗАТЕЛЬНО: area — одно из: Отношения, Финансы, Работа, Здоровье, Род, "
    "Общая ситуация. Если непонятно — 'Общая ситуация'. НЕ оставляй null.\n"
    "Колода (deck): канонические названия БЕЗ склонений — "
    "'Уэйт' (не 'Уэйта'/'Уэйту'), 'Dark Wood', 'Ленорман', 'Игральные', 'Deviant Moon'.\n"
    "Если в тексте есть упоминание 'дно', 'дно колоды', 'bottom' — "
    "выдели эту карту отдельно в поле bottom_card. Это НЕ позиция расклада, "
    "а фоновая карта, её нельзя включать в cards."
)

CORRECTION_PARSE_SYSTEM = (
    "Пользователь правит данные сеанса таро. Извлеки ТОЛЬКО то, что явно меняется. "
    "Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "новое имя или null", '
    '"question": "новый вопрос или null", '
    '"area": "Отношения|Финансы|Работа|Здоровье|Род|Общая ситуация или null"}\n'
    "Если поле не упоминается в правке — ставь null. Только изменения."
)

SESSION_SEARCH_PARSE_SYSTEM = (
    "Пользователь ищет свои прошлые расклады. Извлеки ключевые слова для поиска "
    "в теме расклада. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"keywords": ["слово1", "слово2"]}\n\n'
    "Примеры:\n"
    "- 'что падало на Вадима' → {\"keywords\": [\"Вадим\"]}\n"
    "- 'расклады про работу' → {\"keywords\": [\"работа\"]}\n"
    "- 'расклады на Машу про отношения' → {\"keywords\": [\"Маша\", \"отношения\"]}\n"
    "- 'покажи расклад про здоровье мамы' → {\"keywords\": [\"здоровье\", \"мамы\"]}\n"
    "Имена — в именительном падеже если возможно. Максимум 3 ключевых слова."
)

TAROT_SYSTEM = (
    "Ты — ассистент-таролог. Трактуй строго по справочнику колоды.\n\n"
    "Правила:\n"
    "1. Значения карт — СТРОГО из справочника (ниже). Не придумывай своё.\n"
    "2. Каждая карта: Позиция → Название → значение В ЭТОЙ ПОЗИЦИИ применительно к вопросу (1-2 предложения).\n"
    "3. Если есть предыдущие расклады клиента — свяжи с ними: что изменилось, что подтвердилось, куда движется ситуация.\n"
    "4. Краткий вывод: 2-3 предложения, практическая суть. Учитывай дно колоды при формулировке общего вывода.\n"
    "5. БЕЗ поэзии, метафор, воды. Факты и структура.\n"
    "6. Привязывай значения к вопросу клиента.\n"
    "7. Если есть дно колоды (bottom_card) — после трактовки всех позиций добавь отдельный блок:\n"
    "   <b>🂠 Дно колоды → {карта} ({положение}):</b>\n"
    "   Скрытый фон расклада. Эта карта показывает неочевидный подтекст ситуации, "
    "то что влияет на всё но не на поверхности.\n"
    "   [трактовка в контексте всего расклада]\n\n"
    "ФОРМАТИРОВАНИЕ: используй HTML-теги Telegram, НЕ markdown:\n"
    "- жирный: <b>текст</b> (НЕ **текст**)\n"
    "- курсив: <i>текст</i> (НЕ *текст*)\n"
    "- разрешены только <b>, <i>, <u>, <s>, <code>, <pre>. Никаких других тегов.\n"
)

VISION_SYSTEM = (
    "Ты анализируешь фото расклада карт таро. "
    "Определи все карты, тип расклада и колоду. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"spread_type": "тип или Другой", "deck": "Уэйт|Dark Wood|Ленорман|Игральные|Deviant Moon", '
    '"cards": [{"position": "позиция", "card": "название"}], '
    '"bottom_card": "название карты дна колоды или null"}\n\n'
    "Порядок карт на фото: СЛЕВА НАПРАВО, СВЕРХУ ВНИЗ. "
    "Верни карты в этом порядке.\n"
    "Если карт БОЛЬШЕ чем нужно для расклада (например 4 карты для триплета) — "
    "ПОСЛЕДНЯЯ карта = дно колоды, положи её в bottom_card, "
    "а в cards оставь только карты самого расклада.\n"
    "Если на фото дно лежит отдельно от расклада (ниже, сбоку, рядом с колодой) — "
    "тоже в bottom_card.\n"
    "Колода — каноническое название без склонений."
)


# ────────────────────────── Вспомогательные ────────────────────────────────

def _parse_json_safe(raw: str) -> Optional[dict]:
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return None


def _format_prev_sessions(sessions: List[dict]) -> str:
    """Форматирует предыдущие расклады клиента для вставки в промпт."""
    lines: List[str] = []
    for s in sessions[:5]:
        p = s.get("properties", {})
        date_prop = p.get("Дата и время") or p.get("Дата") or {}
        date_val = date_prop.get("date") or {}
        d = (date_val.get("start") or "")[:10]
        question = _extract_text(p.get("Тема") or {})
        cards = _extract_text(p.get("Карты") or {})
        interp = _extract_text(p.get("Трактовка") or {})
        if interp and len(interp) > 300:
            interp = interp[:300] + "..."
        parts: List[str] = [f"📅 {d}: {question}" if question else f"📅 {d}"]
        if cards:
            parts.append(f"  Карты: {cards[:150]}")
        if interp:
            parts.append(f"  Итог: {interp}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def _pending_keyboard(uid: int) -> InlineKeyboardMarkup:
    from core.utils import cancel_button, secondary_button
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"tarot_save:{uid}"),
        secondary_button("✏️ Поправить", f"tarot_edit:{uid}"),
        cancel_button("❌ Отмена", f"tarot_cancel:{uid}"),
    ]])


async def _send_reading(message: Message, state: dict) -> None:
    """Отправить трактовку с кнопками управления."""
    uid = message.from_user.id
    client_name = state.get("client_name") or ""
    is_personal = not client_name
    deck = html.escape(state.get("deck") or "Уэйта")
    spread = html.escape(state.get("spread_type") or "Расклад")
    question = html.escape(state.get("question") or "")
    cards_text = state.get("cards") or ""
    bottom_card = state.get("bottom_card") or ""
    interpretation = state.get("interpretation") or ""
    client_name_safe = html.escape(client_name)

    card_lines = "".join(
        f"  • {html.escape(c.strip())}\n" for c in cards_text.split(",") if c.strip()
    )
    header = (
        f"🃏 <b>{spread}</b> · {deck}\n"
        f"{'🔮 Личный' if is_personal else '👤 ' + client_name_safe}"
        + (f" · {question}" if question else "") + "\n"
    )
    bottom_line = (
        f"\n🂠 <b>Дно:</b> {html.escape(bottom_card)}\n" if bottom_card else ""
    )
    hint_line = (
        "\n💡 Заведи себя как клиента чтобы группировать личные расклады\n"
        if is_personal and state.get("self_client_missing")
        else ""
    )
    body = (
        f"{header}\n📍 <b>Карты:</b>\n{card_lines}"
        f"{bottom_line}{hint_line}"
        f"\n📝 <b>Трактовка:</b>\n{interpretation[:3500]}"
    )
    await message.answer(
        body, reply_markup=_pending_keyboard(uid), parse_mode="HTML"
    )
    if len(interpretation) > 3500:
        await message.answer(interpretation[3500:7000], parse_mode="HTML")


# ────────────────────────── Основной обработчик ────────────────────────────

async def handle_add_session(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    try:
        tg_id = message.from_user.id
        tz_offset = await get_user_tz(tg_id)
        tz = timezone(timedelta(hours=tz_offset))

        # 1. Haiku парсит данные
        raw = await ask_claude(text, system=PARSE_SESSION_SYSTEM, max_tokens=300)
        data = _parse_json_safe(raw)
        if data is None:
            await log_error(text, "parse_error", bot_label="🌒 Arcana", error_code="–")
            await message.answer("⚠️ Не смог разобрать данные сеанса.")
            return

        client_name = data.get("client_name") or None
        client_id: Optional[str] = None
        self_client_missing = False
        if client_name:
            client = await client_find(client_name, user_notion_id=user_notion_id)
            if client:
                client_id = client["id"]
        else:
            # Личный расклад — привязать к клиенту-владельцу (если он заведён)
            from core.user_manager import get_user
            owner = await get_user(tg_id)
            owner_name = (owner or {}).get("name") or ""
            if owner_name:
                self_client = await client_find(owner_name, user_notion_id=user_notion_id)
                if self_client:
                    client_id = self_client["id"]
                else:
                    self_client_missing = True
            else:
                self_client_missing = True

        deck = _match_deck(data.get("deck") or "") or "Уэйт"
        cards_text = data.get("cards") or ""
        card_names: List[str] = [c.strip() for c in cards_text.split(",") if c.strip()]
        bottom_card = (data.get("bottom_card") or "").strip()
        area = _normalize_area(data.get("area") or "")
        question = data.get("question") or area

        # 2. Справочник — нужные карты + дно (если есть)
        from arcana.tarot_loader import get_cards_context
        ctx_cards = card_names + ([bottom_card] if bottom_card else [])
        cards_context = get_cards_context(deck, ctx_cards)

        # 3. Память
        memory_context = ""
        try:
            from core.memory import get_memories_for_context, extract_context_keywords
            keywords = extract_context_keywords(data, client_name)
            if keywords:
                memory_context = await get_memories_for_context(user_notion_id, keywords)
        except Exception:
            pass

        # 4. Предыдущие расклады клиента
        prev_context = ""
        if client_id:
            try:
                prev = await sessions_by_client(client_id, user_notion_id=user_notion_id)
                if prev:
                    prev_context = _format_prev_sessions(prev)
            except Exception:
                pass

        # 5. Трактовка через Sonnet
        interpretation = ""
        if card_names:
            system = TAROT_SYSTEM
            if cards_context:
                system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"
            if memory_context:
                system += f"\n\n--- ПАМЯТЬ ---\n{memory_context}"
            if prev_context:
                system += f"\n\n--- ПРЕДЫДУЩИЕ РАСКЛАДЫ КЛИЕНТА ---\n{prev_context}"

            user_prompt = (
                f"Расклад: {data.get('spread_type') or ''}\n"
                f"Вопрос: {question}\n"
                f"Карты: {cards_text}"
            )
            if bottom_card:
                user_prompt += f"\nДно колоды: {bottom_card}"
            interpretation = await ask_claude(
                user_prompt,
                system=system,
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
            )

        # 6. Сохранить в pending и показать с кнопками
        from arcana.pending_tarot import save_pending
        pending_state = {
            "client_name":    client_name,
            "client_id":      client_id,
            "spread_type":    data.get("spread_type") or "",
            "question":       question,
            "cards":          cards_text,
            "bottom_card":    bottom_card or None,
            "deck":           deck,
            "area":           area,
            "interpretation": interpretation,
            "amount":         float(data.get("amount") or 0),
            "paid":           float(data.get("paid") or 0),
            "payment_source": data.get("payment_source") or None,
            "user_notion_id": user_notion_id,
            "tz_offset":      tz_offset,
            "awaiting_edit":  False,
            "self_client_missing": self_client_missing,
        }
        await save_pending(tg_id, pending_state)
        await _send_reading(message, pending_state)

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_add_session error: %s", trace)
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
            traceback=trace, bot_label="🌒 Arcana", error_code=code,
        )
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        await message.answer(f"❌ {suffix} · {notion_status}")


# ────────────────────────── Callbacks ──────────────────────────────────────

@router.callback_query(F.data.startswith("tarot_save:"))
async def cb_tarot_save(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    if uid != call.from_user.id:
        return

    from arcana.pending_tarot import delete_pending, get_pending
    state = await get_pending(uid)
    if not state:
        await call.message.edit_text("⏱ Сессия истекла. Отправь расклад заново.")
        return

    try:
        tz_offset = float(state.get("tz_offset") or 3)
        tz = timezone(timedelta(hours=tz_offset))
        user_notion_id = state.get("user_notion_id") or ""

        spread = _match_spread(state.get("spread_type") or "")
        payment_source_raw = state.get("payment_source") or None
        payment_source = (
            PAYMENT_SOURCE_MAP.get((payment_source_raw or "").lower(), payment_source_raw)
            if payment_source_raw else None
        )
        amount = float(state.get("amount") or 0)
        paid = float(state.get("paid") or 0)
        client_name = state.get("client_name") or None
        is_personal = not client_name

        bottom_card = state.get("bottom_card") or None
        area = _normalize_area(state.get("area") or "")
        deck = _match_deck(state.get("deck") or "") or None
        question = state.get("question") or ""
        interpretation = state.get("interpretation") or ""

        # Дно — в конец трактовки (если Claude не добавил блок 🂠)
        if bottom_card and "🂠" not in interpretation:
            interpretation = (
                interpretation.rstrip()
                + f"\n\n🂠 Дно: {bottom_card}"
            )

        session_page_id = await session_add(
            date=_now_iso(tz),
            spread_type=spread,
            title=question,
            question=question,
            cards=state.get("cards") or "",
            interpretation=interpretation,
            amount=amount,
            paid=paid,
            session_type="Личный" if is_personal else "Клиентский",
            client_id=state.get("client_id") or None,
            user_notion_id=user_notion_id,
            area=area,
            deck=deck,
            payment_source=payment_source,
        )

        if amount > 0:
            await finance_add(
                date=datetime.now(tz).strftime("%Y-%m-%d"),
                amount=amount,
                category="🔮 Практика",
                type_="💰 Доход",
                source=payment_source or "💳 Карта",
                bot_label="🌒 Arcana",
                description=(
                    f"🃏 {state.get('spread_type') or 'Расклад'}"
                    + (f" — {client_name}" if client_name else "")
                ),
                user_notion_id=user_notion_id,
            )

        await delete_pending(uid)
        debt = max(0, amount - paid)
        ok_text = "✅ Расклад сохранён в Notion" + (
            f"\n⚠️ Долг: {int(debt)}₽" if debt > 0 else ""
        ) + "\n\n<i>↩️ Реплай чтобы дополнить</i>"
        saved_msg = await call.message.edit_text(ok_text, parse_mode="HTML")

        if session_page_id:
            from core.message_pages import save_message_page
            target = saved_msg if hasattr(saved_msg, "message_id") else call.message
            await save_message_page(
                chat_id=target.chat.id,
                message_id=target.message_id,
                page_id=session_page_id,
                page_type="session",
                bot="arcana",
            )

    except Exception as e:
        trace = tb.format_exc()
        logger.error("cb_tarot_save error: %s", trace)
        await call.message.edit_text("❌ Ошибка при сохранении · пусть Кай правит код")


@router.callback_query(F.data.startswith("tarot_edit:"))
async def cb_tarot_edit(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    if uid != call.from_user.id:
        return

    from arcana.pending_tarot import update_pending
    await update_pending(uid, {"awaiting_edit": True})
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("✏️ Напиши что поправить — скорректирую трактовку.")


@router.callback_query(F.data.startswith("tarot_cancel:"))
async def cb_tarot_cancel(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    from arcana.pending_tarot import delete_pending
    await delete_pending(uid)
    await call.message.edit_text("❌ Расклад не сохранён.")


# ────────────────────────── Фото расклада ──────────────────────────────────

async def handle_tarot_photo(message: Message, user_notion_id: str = "") -> None:
    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        bio = await message.bot.download_file(file.file_path)
        image_b64 = base64.standard_b64encode(bio.read()).decode()

        await message.answer("🔍 Распознаю карты...")

        raw = await ask_claude_vision(
            "Определи все карты в раскладе, колоду и тип расклада.",
            image_b64,
            system=VISION_SYSTEM,
        )
        vision_data = _parse_json_safe(raw)
        if vision_data is None:
            await message.answer("⚠️ Не смог распознать карты. Опиши текстом.")
            return

        cards = vision_data.get("cards") or []
        spread_type = vision_data.get("spread_type") or "Другой"
        deck = _match_deck(vision_data.get("deck") or "") or "Уэйт"
        bottom_card = (vision_data.get("bottom_card") or "").strip()

        if not cards:
            await message.answer("⚠️ Карты не определены. Опиши текстом.")
            return

        cards_text = ", ".join(
            f"{c.get('position', '')}: {c.get('card', '')}" for c in cards
        )
        card_names: List[str] = [c.get("card", "") for c in cards if c.get("card")]
        question = message.caption or "общий расклад"

        # Загрузить справочник
        from arcana.tarot_loader import get_cards_context
        ctx_cards = card_names + ([bottom_card] if bottom_card else [])
        cards_context = get_cards_context(deck, ctx_cards)

        system = TAROT_SYSTEM
        if cards_context:
            system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"

        user_prompt = f"Расклад: {spread_type}\nВопрос: {question}\nКарты: {cards_text}"
        if bottom_card:
            user_prompt += f"\nДно колоды: {bottom_card}"
        interpretation = await ask_claude(
            user_prompt,
            system=system,
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
        )

        tg_id = message.from_user.id
        tz_offset = await get_user_tz(tg_id)

        # Личный расклад с фото — привязать к клиенту-владельцу (если он заведён)
        self_client_id: Optional[str] = None
        self_client_missing = False
        from core.user_manager import get_user
        owner = await get_user(tg_id)
        owner_name = (owner or {}).get("name") or ""
        if owner_name:
            self_client = await client_find(owner_name, user_notion_id=user_notion_id)
            if self_client:
                self_client_id = self_client["id"]
            else:
                self_client_missing = True
        else:
            self_client_missing = True

        from arcana.pending_tarot import save_pending
        pending_state = {
            "client_name":    None,
            "client_id":      self_client_id,
            "spread_type":    spread_type,
            "question":       question,
            "cards":          cards_text,
            "bottom_card":    bottom_card or None,
            "deck":           deck,
            "area":           AREA_DEFAULT,
            "interpretation": interpretation,
            "amount":         0.0,
            "paid":           0.0,
            "payment_source": None,
            "user_notion_id": user_notion_id,
            "tz_offset":      tz_offset,
            "awaiting_edit":  False,
            "from_photo":     True,
            "self_client_missing": self_client_missing,
        }
        await save_pending(tg_id, pending_state)
        await _send_reading(message, pending_state)

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_tarot_photo error: %s", trace)
        await message.answer("❌ Ошибка при анализе фото.")


# ────────────────────────── Поиск раскладов ────────────────────────────────

async def handle_session_search(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    """Поиск прошлых раскладов по ключевым словам в Теме."""
    try:
        raw = await ask_claude(
            text, system=SESSION_SEARCH_PARSE_SYSTEM, max_tokens=150
        )
        data = _parse_json_safe(raw) or {}
        keywords = [
            k.strip() for k in (data.get("keywords") or []) if isinstance(k, str) and k.strip()
        ]
        if not keywords:
            await message.answer("🔍 Не поняла что искать. Напиши имя или тему яснее.")
            return

        results = await sessions_search(
            keywords, user_notion_id=user_notion_id, limit=10
        )
        kw_display = html.escape(", ".join(keywords))
        if not results:
            await message.answer(f"🔍 По «{kw_display}» раскладов не нашла.")
            return

        lines: List[str] = [f"🔍 <b>Расклады по «{kw_display}»</b>:"]
        shown = results[:5]
        for s in shown:
            p = s.get("properties", {})
            date_prop = p.get("Дата") or p.get("Дата и время") or {}
            date_val = date_prop.get("date") or {}
            d = (date_val.get("start") or "")[:10]
            theme = _extract_text(p.get("Тема") or {}) or "—"

            spread_prop = p.get("Тип расклада") or {}
            spread_items = spread_prop.get("multi_select") or []
            spread_name = spread_items[0].get("name", "") if spread_items else ""

            area_prop = p.get("Область") or {}
            area_items = area_prop.get("multi_select") or []
            if area_items:
                area_name = area_items[0].get("name", "")
            else:
                area_name = (area_prop.get("select") or {}).get("name", "")

            cards_text = _extract_text(p.get("Карты") or {})
            cards_short = (cards_text[:80] + "…") if len(cards_text) > 80 else cards_text

            meta_parts = [x for x in (spread_name, area_name) if x]
            meta = " · ".join(html.escape(x) for x in meta_parts)
            block = f"\n📅 <b>{d}</b> · {html.escape(theme)}"
            if meta:
                block += f"\n   {meta}"
            if cards_short:
                block += f"\n   🃏 {html.escape(cards_short)}"
            lines.append(block)

        if len(results) > len(shown):
            lines.append(f"\n… и ещё {len(results) - len(shown)}")

        await message.answer("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_session_search error: %s", trace)
        await message.answer("❌ Не удалось найти расклады.")


# ────────────────────────── Быстрая трактовка ──────────────────────────────

async def handle_tarot_interpret(message: Message, text: str) -> None:
    interpretation = await ask_claude(
        f"Карты/расклад: {text}",
        system=TAROT_SYSTEM,
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
    )
    await message.answer(
        f"🔮 <b>Трактовка:</b>\n\n{interpretation[:4000]}", parse_mode="HTML"
    )
    if len(interpretation) > 4000:
        await message.answer(interpretation[4000:8000], parse_mode="HTML")
