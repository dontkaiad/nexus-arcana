"""arcana/handlers/works.py — Работы (= Задачи Nexus для практики)."""
from __future__ import annotations

import json
import logging
import traceback as tb
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram.types import Message
from core.claude_client import ask_claude
from core.notion_client import (
    work_add, works_list, work_done, client_find, log_error,
    _extract_text, _extract_number,
)
from core.shared_handlers import get_user_tz

logger = logging.getLogger("arcana.works")

PARSE_WORK_SYSTEM = (
    "Извлеки данные работы/задачи практики. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"title": "что сделать", "deadline": "YYYY-MM-DD или YYYY-MM-DDTHH:MM или null", '
    '"priority": "срочно/важно/можно потом", '
    '"category": "расклад/ритуал/соцсети/расходники/обучение/прочее или null", '
    '"client_name": "имя клиента или null", '
    '"type": "личная/клиентская"}\n\n'
    "Правила парсинга deadline (как в Nexus):\n"
    "- 'завтра' → дата=завтра без времени (YYYY-MM-DD)\n"
    "- 'послезавтра' → +2 дня\n"
    "- 'через N дней' → now + N дней\n"
    "- 'в пятницу/субботу/...' → ближайший этот день недели\n"
    "- 'завтра в 18' / 'в субботу в 19:30' → YYYY-MM-DDTHH:MM\n"
    "- 'к 10 мая' → 2026-05-10 (год — текущий или следующий по контексту)\n"
    "- если дедлайн НЕ упомянут — null. Не выдумывай.\n\n"
    "title — короткий, по-делу: «Финансовый ритуал для Маши», «Расклад на работу Игоря».\n\n"
    "Примеры (для Haiku — следуй формату строго):\n"
    'Вход: «сделать маше финансовый ритуал завтра в 18»\n'
    'Выход: {"title":"Финансовый ритуал для Маши","deadline":"<завтра>T18:00",'
    '"priority":"важно","category":"ритуал","client_name":"Маша","type":"клиентская"}\n\n'
    'Вход: «расклад на работу игоря к пятнице»\n'
    'Выход: {"title":"Расклад на работу Игоря","deadline":"<ближайшая пятница>",'
    '"priority":"важно","category":"расклад","client_name":"Игорь","type":"клиентская"}\n\n'
    'Вход: «закупить свечи»\n'
    'Выход: {"title":"Закупить свечи","deadline":null,'
    '"priority":"можно потом","category":"расходники","client_name":null,"type":"личная"}'
)

WORK_CATEGORY_MAP = {
    "расклад": "🃏 Расклад",
    "ритуал": "✨ Ритуал",
    "соцсети": "📱 Соцсети",
    "расходники": "🛒 Расходники",
    "обучение": "📚 Обучение",
    "прочее": "🗂️ Прочее",
}

WORK_PRIORITY_MAP = {
    "срочно": "Срочно",
    "важно": "Важно",
    "можно потом": "Можно потом",
}

_PRIORITY_EMOJI = {
    "Срочно": "🔴",
    "Важно": "🟡",
    "Можно потом": "⚪",
}


async def handle_add_work(message: Message, text: str, user_notion_id: str = "") -> None:
    """Точка входа создания Работы — preview-flow (паритет с Nexus tasks).

    Запись в Notion НЕ создаётся здесь; сначала Кай подтверждает превью.
    """
    from arcana.handlers.work_preview import handle_add_work_preview
    await handle_add_work_preview(message, text, user_notion_id)


async def handle_work_done(message: Message, text: str, user_notion_id: str = "") -> None:
    try:
        hint = (await ask_claude(
            text,
            system="Извлеки ключевые слова названия выполненной работы/задачи. Ответь ТОЛЬКО ключевыми словами, без объяснений.",
            max_tokens=50,
        )).strip()

        items = await works_list(user_notion_id=user_notion_id)
        if not items:
            await message.answer("📋 Нет открытых работ.")
            return

        # fuzzy match по названию
        hint_lower = hint.lower()
        best = None
        best_score = 0
        for item in items:
            name = _extract_text(item["properties"].get("Работа", {})).lower()
            # считаем сколько слов из hint есть в названии
            words = [w for w in hint_lower.split() if len(w) > 2]
            score = sum(1 for w in words if w in name)
            if score > best_score:
                best_score = score
                best = item

        if not best or best_score == 0:
            await message.answer(f"❌ Не нашла работу по «{hint}».")
            return

        page_id = best["id"]
        title = _extract_text(best["properties"].get("Работа", {}))
        ok = await work_done(page_id)
        if ok:
            await message.answer(f"🔥 Работа выполнена!\n📌 {title}")
        else:
            await message.answer("⚠️ Ошибка обновления в Notion.")

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_work_done error: %s", trace)
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


async def handle_works_list(message: Message, user_notion_id: str = "") -> None:
    try:
        items = await works_list(user_notion_id=user_notion_id)
        if not items:
            await message.answer("📋 Работ нет.")
            return

        groups: dict = {"Срочно": [], "Важно": [], "Можно потом": []}
        for item in items:
            props = item["properties"]
            title = _extract_text(props.get("Работа", {}))
            priority_val = (props.get("Приоритет") or {}).get("select", {})
            priority = (priority_val.get("name") or "Можно потом") if priority_val else "Можно потом"
            if priority not in groups:
                priority = "Можно потом"

            deadline_val = (props.get("Дедлайн") or {}).get("date", {})
            deadline_str = ""
            if deadline_val:
                start = (deadline_val.get("start") or "")[:16]
                if start:
                    deadline_str = f" · 📅 {start[8:10]}.{start[5:7]}"
                    if len(start) > 10:
                        deadline_str += f" {start[11:16]}"

            cat_val = (props.get("Категория") or {}).get("select", {})
            cat_str = f" · {cat_val.get('name')}" if cat_val and cat_val.get("name") else ""

            rel = (props.get("👥 Клиенты") or {}).get("relation", [])
            client_str = ""
            if rel:
                client_str = " · 👤 …"

            groups[priority].append(f"  • {title}{deadline_str}{cat_str}{client_str}")

        lines = ["📋 <b>Работы:</b>\n"]
        for priority, emoji in _PRIORITY_EMOJI.items():
            if groups[priority]:
                lines.append(f"{emoji} <b>{priority}:</b>")
                lines.extend(groups[priority])
                lines.append("")

        await message.answer("\n".join(lines).strip())

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_works_list error: %s", trace)
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
