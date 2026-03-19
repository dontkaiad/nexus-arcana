"""arcana/handlers/base.py"""
from __future__ import annotations

import logging
import traceback as tb

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.claude_client import ask_claude
from core.notion_client import log_error

router = Router()
logger = logging.getLogger("arcana.base")

_clarify: dict = {}  # user_id → original_text

ROUTER_SYSTEM = """Сначала исправь опечатки, потом определи тип. Ответь ТОЛЬКО одним словом.

Примеры исправлений: расклд→расклад, ртуал→ритуал, клент→клиент.

Типы:
new_client   — новый клиент
session      — сеанс, расклад, таро
ritual       — ритуал
client_info  — инфо о клиенте
debt         — долги клиентов
tarot_interp — трактовка таро
delete       — удалить записи («удали», «удалить», «убери»)
nexus        — финансы, расходы, доходы, задачи, заметки, покупки
unknown      — остальное"""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🌒 <b>Arcana</b> — цифровой гримуар и CRM практики.\n\n"
        "• «клиент Анна, кельтский крест, 3000р» → карточка + сеанс\n"
        "• Фото расклада → распознаю карты и дам трактовку\n"
        "• «что у Анны?» → полная история\n"
        "• «сколько должны?» → долги клиентов\n"
        "• «удали последний сеанс» → удаление с подтверждением\n\n"
        "Для финансов и задач используй ☀️ @nexus_kailark_bot"
    )


@router.message()
async def route_message(message: Message, user_notion_id: str = "") -> None:
    try:
        if message.photo:
            from arcana.handlers.sessions import handle_tarot_photo
            await handle_tarot_photo(message)
            return

        from core.layout import maybe_convert
        text = maybe_convert((message.text or "").strip())
        if not text:
            await message.answer("Отправь текст или фото расклада.")
            return

        # reply-контекст
        if message.reply_to_message and message.reply_to_message.text:
            prev = maybe_convert(message.reply_to_message.text.strip())
            text = f"[контекст: {prev[:100]}]\n{text}"

        uid = message.from_user.id

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
        from arcana.handlers.sessions import handle_add_session, handle_tarot_interpret
        from arcana.handlers.rituals import handle_add_ritual
        from arcana.handlers.delete import handle_delete

        dispatch = {
            "new_client":   lambda: handle_add_client(message, text, user_notion_id),
            "session":      lambda: handle_add_session(message, text, user_notion_id),
            "ritual":       lambda: handle_add_ritual(message, text, user_notion_id),
            "client_info":  lambda: handle_client_info(message, text, user_notion_id),
            "debt":         lambda: handle_debts(message, user_notion_id),
            "tarot_interp": lambda: handle_tarot_interpret(message, text),
            "delete":       lambda: handle_delete(message, text),
        }

        handler = dispatch.get(intent)
        if handler:
            await handler()
        elif intent == "nexus":
            await message.answer("☀️ Это для бота Nexus — перешли туда: @nexus_kailark_bot")
        elif intent in ("unknown", "") or not intent:
            # Первый раз не поняла — переспрашиваем
            _clarify[uid] = text
            await message.answer("🤔 Не поняла — уточни, что сделать?")
        else:
            logged = await log_error(text, "parse_error", bot_label="🌒 Arcana", error_code="–")
            notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
            await message.answer(f"❌ Не так ответил Claude · пусть Кай правит промпт · {notion_status}")

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
