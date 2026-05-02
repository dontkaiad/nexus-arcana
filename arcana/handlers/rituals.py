"""arcana/handlers/rituals.py"""
from __future__ import annotations

import json
import logging
import traceback as tb
from datetime import datetime, timezone, timedelta

from aiogram.types import Message
from core.claude_client import ask_claude
from core.notion_client import ritual_add, client_find, log_error, finance_add
from core.shared_handlers import get_user_tz

logger = logging.getLogger("arcana.rituals")

PAYMENT_SOURCE_MAP = {
    "карта": "💳 Карта",
    "наличные": "💵 Наличные",
    "бартер": "🔄 Бартер",
}

PARSE_RITUAL_SYSTEM = (
    "Извлеки данные ритуала. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "name": "название", '
    '"goal": "привлечение|защита|очищение|любовь|финансы|деструктив|развязка|приворот|другое или null", '
    '"place": "дома|лес|погост|перекрёсток|церковь|водоём|поле|другое или null", '
    '"consumables": "расходники строкой", "consumables_cost": число, '
    '"duration_min": число, "offerings": "подношения", "offerings_cost": число, '
    '"forces": "силы", "structure": "последовательность", '
    '"notes": "заметки или null", '
    '"amount": число, "paid": число, '
    '"payment_source": "карта|наличные|бартер или null", '
    '"needs_clarification": true|false}\n\n'
    "Толерантно к опечаткам в типах ритуалов:\n"
    "- 'финансковый', 'финансовое' → 'финансы'\n"
    "- 'люберый', 'любовное' → 'любовь'\n"
    "- 'очистка', 'чистка', 'почистить' → 'очищение'\n"
    "- 'развязать', 'разрыв' → 'развязка'\n"
    "- 'приворот', 'приворожить' → 'приворот'\n"
    "- 'защитить', 'оберег' → 'защита'\n"
    "- 'привлечь' → 'привлечение'\n"
    "- 'порча', 'наведение' → 'деструктив'\n\n"
    "Если ввод слишком короткий или неясный (нет описания структуры/сил/"
    "подробностей, только тема) — установи needs_clarification=true. "
    "name и goal попробуй извлечь даже из короткого ввода. "
    "Иначе needs_clarification=false."
)


CLARIFICATION_TEXT = (
    "🤔 Расскажи подробнее, что в ритуале:\n"
    "- какие силы используешь\n"
    "- структура (свечи, заговоры, время)\n"
    "- расходники, подношения\n"
    "- где делаешь\n\n"
    "Опиши одним сообщением — добавлю к уже понятому."
)


async def handle_add_ritual(message: Message, text: str, user_notion_id: str = "") -> None:
    try:
        tg_id = message.from_user.id
        tz_offset = await get_user_tz(tg_id)
        tz = timezone(timedelta(hours=tz_offset))

        # Если есть pending clarification — мерджим старый ввод с новым.
        from arcana.pending_tarot import get_pending, save_pending, delete_pending
        pending = await get_pending(tg_id)
        accumulated_text = text
        if pending and pending.get("type") == "awaiting_ritual_clarification":
            accumulated_text = (pending.get("text") or "") + "\n" + text

        raw = await ask_claude(
            accumulated_text, system=PARSE_RITUAL_SYSTEM, max_tokens=600,
        )
        try:
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
        except Exception:
            await log_error(text, "parse_error", bot_label="🌒 Arcana", error_code="–")
            await message.answer("⚠️ Не смог разобрать ритуал. Опиши подробнее.")
            return

        # Если Sonnet говорит «слишком короткий ввод» — спрашиваем уточнение
        # и сохраняем накопленный текст в pending.
        if data.get("needs_clarification") is True and not pending:
            await save_pending(tg_id, {
                "type": "awaiting_ritual_clarification",
                "text": accumulated_text,
            })
            await message.answer(CLARIFICATION_TEXT)
            return
        # После уточнения чистим pending независимо от исхода.
        if pending and pending.get("type") == "awaiting_ritual_clarification":
            await delete_pending(tg_id)

        client_name = data.get("client_name")
        client_id = None
        if client_name:
            client = await client_find(client_name, user_notion_id=user_notion_id)
            if client:
                client_id = client["id"]

        goal = data.get("goal") or None
        place = data.get("place") or None
        notes = data.get("notes") or None
        offerings_cost = float(data.get("offerings_cost") or 0)
        payment_source_raw = data.get("payment_source") or None
        payment_source = PAYMENT_SOURCE_MAP.get((payment_source_raw or "").lower(), payment_source_raw) if payment_source_raw else None
        amount = float(data.get("amount") or 0)
        paid = float(data.get("paid") or 0)

        today = datetime.now(tz).strftime("%Y-%m-%d")
        result = await ritual_add(
            name=data.get("name", "Ритуал"),
            date=today,
            ritual_type="Личный" if not client_name else "Клиентский",
            consumables=data.get("consumables") or "",
            consumables_cost=float(data.get("consumables_cost") or 0),
            duration_min=float(data.get("duration_min") or 0),
            offerings=data.get("offerings") or "",
            forces=data.get("forces") or "",
            structure=data.get("structure") or "",
            amount=amount,
            paid=paid,
            client_id=client_id,
            user_notion_id=user_notion_id,
            goal=goal,
            place=place,
            notes=notes,
            payment_source=payment_source,
            offerings_cost=offerings_cost if offerings_cost > 0 else None,
        )
        if not result:
            await message.answer("⚠️ Ошибка записи в Notion.")
            return

        if amount > 0:
            await finance_add(
                date=today,
                amount=amount,
                category="🔮 Практика",
                type_="💰 Доход",
                source=payment_source or "💳 Карта",
                bot_label="🌒 Arcana",
                description=f"🕯️ {data.get('name') or 'Ритуал'}" + (f" — {client_name}" if client_name else ""),
                user_notion_id=user_notion_id,
            )

        debt = max(0, amount - paid)
        from core.notion_client import _RITUAL_GOAL_MAP, _RITUAL_PLACE_MAP
        goal_display = _RITUAL_GOAL_MAP.get((goal or "").lower(), goal or "")
        place_display = _RITUAL_PLACE_MAP.get((place or "").lower(), place or "")
        goal_place_parts = [x for x in (goal_display, place_display) if x]
        goal_place = " · ".join(goal_place_parts)

        lines = [
            "🕯️ Ритуал записан!",
            f"📌 {data.get('name', 'Ритуал')}",
        ]
        if goal_place:
            lines.append(goal_place)
        lines.append(f"📅 {today}")
        lines.append(f"👥 {'Клиентский · ' + client_name if client_name else 'Личный'}")
        if amount:
            money = f"💰 {int(amount)}₽"
            if debt > 0:
                money += f" · ⚠️ долг {int(debt)}₽"
            lines.append(money)
        lines.append("\n<i>↩️ Реплай чтобы дополнить</i>")

        # Если ритуал на 🤝 Платного клиента — сразу прикрепляем inline-оплату.
        pay_kb = None
        try:
            from core.notion_client import client_get_type, should_skip_payment
            client_type = await client_get_type(client_id) if client_id else None
            if client_id and not should_skip_payment(client_type):
                from arcana.handlers.payment import payment_keyboard
                pay_kb = payment_keyboard(result, "rituals")
                lines.append(f"\n💰 Как оплатил(а) {client_name or 'клиент(а)'}?")
        except Exception:
            pay_kb = None
        bot_msg = await message.answer(
            "\n".join(lines), parse_mode="HTML", reply_markup=pay_kb,
        )

        from core.message_pages import save_message_page
        await save_message_page(
            chat_id=bot_msg.chat.id,
            message_id=bot_msg.message_id,
            page_id=result,
            page_type="ritual",
            bot="arcana",
        )

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_add_ritual error: %s", trace)
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
