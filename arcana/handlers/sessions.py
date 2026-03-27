"""arcana/handlers/sessions.py"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone, timedelta

from aiogram.types import Message
from core.claude_client import ask_claude, ask_claude_vision
from core.notion_client import session_add, client_find, log_error

logger = logging.getLogger("arcana.sessions")
MOSCOW_TZ = timezone(timedelta(hours=3))

SPREAD_MAP = {
    "триплет": "🔺 Триплет",
    "3 карты": "🔺 Триплет",
    "три карты": "🔺 Триплет",
    "сфера": "🌐 Сфера жизни",
    "сфера жизни": "🌐 Сфера жизни",
    "кельтский": "✝️ Кельтский крест",
    "кельтский крест": "✝️ Кельтский крест",
    "celtic cross": "✝️ Кельтский крест",
    "воздействия": "⚡ Магические воздействия",
    "магические воздействия": "⚡ Магические воздействия",
    "диагностика перед ритуалом": "🔍 Диагностика перед ритуалом",
    "диагностика": "🔍 Диагностика перед ритуалом",
    "способности": "✨ Диагностика способностей",
    "диагностика способностей": "✨ Диагностика способностей",
    "родовой": "🌳 Родовой узел",
    "родовой узел": "🌳 Родовой узел",
}


def _match_spread(text: str) -> str:
    """Fuzzy-матч текста от Claude → emoji-prefixed значение для Notion multi-select."""
    if not text:
        return ""
    low = text.strip().lower()
    # exact match
    if low in SPREAD_MAP:
        return SPREAD_MAP[low]
    # partial match — ключ содержится в тексте или текст содержится в ключе
    for key, value in SPREAD_MAP.items():
        if key in low or low in key:
            return value
    # fallback — вернуть оригинал (лучше чем ошибка)
    return text.strip()


PARSE_SESSION_SYSTEM = (
    "Извлеки данные о сеансе таро. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "spread_type": "Кельтский крест|3 карты|Расклад на месяц|Другой", '
    '"question": "тема", "cards": "карты через запятую или null", "amount": число, "paid": число}'
)

TAROT_SYSTEM = (
    "Ты — опытный таролог. Дай глубокую трактовку расклада. "
    "Стиль: мистический, образный, практичный. 3–5 абзацев. "
    "Структура: общий смысл → каждая карта в позиции → вывод и рекомендация."
)

VISION_SYSTEM = (
    "Ты анализируешь фото расклада карт таро. "
    "Определи все карты и тип расклада. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"spread_type": "тип или Другой", "cards": [{"position": "позиция", "card": "название"}]}'
)


def _now_iso() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()


async def handle_add_session(message: Message, text: str, user_notion_id: str = "") -> None:
    raw = await ask_claude(text, system=PARSE_SESSION_SYSTEM, max_tokens=300)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(text, "parse_error", raw)
        await message.answer("⚠️ Не смог разобрать данные сеанса.")
        return

    client_name = data.get("client_name")
    is_personal = not client_name
    client_id = None

    if not is_personal:
        client = await client_find(client_name, user_notion_id=user_notion_id)
        if client:
            client_id = client["id"]

    cards_text = data.get("cards") or ""
    interpretation = ""
    if cards_text:
        interpretation = await ask_claude(
            f"Расклад: {data.get('spread_type', '')}\nВопрос: {data.get('question', '')}\nКарты: {cards_text}",
            system=TAROT_SYSTEM,
            max_tokens=2000,
        )

    spread = _match_spread(data.get("spread_type") or "")

    await session_add(
        date=_now_iso(),
        spread_type=spread,
        question=data.get("question", ""),
        cards=cards_text,
        interpretation=interpretation,
        amount=float(data.get("amount") or 0),
        paid=float(data.get("paid") or 0),
        session_type="Личный" if is_personal else "Клиентский",
        client_id=client_id,
        user_notion_id=user_notion_id,
    )

    debt = max(0, float(data.get("amount") or 0) - float(data.get("paid") or 0))
    reply = (
        f"✅ Сеанс записан\n"
        f"{'🔮 Личный' if is_personal else '👤 ' + client_name}\n"
        f"🃏 {data.get('spread_type', '')}\n"
        f"{'💰 ' + str(int(data.get('amount'))) + '₽' if data.get('amount') else ''}"
        f"{'  ⚠️ долг ' + str(int(debt)) + '₽' if debt > 0 else ''}"
    )

    if interpretation:
        await message.answer(reply)
        for chunk_start in range(0, min(len(interpretation), 7000), 4000):
            await message.answer(f"🔮 <b>Трактовка:</b>\n\n{interpretation[chunk_start:chunk_start+4000]}"
                                 if chunk_start == 0 else interpretation[chunk_start:chunk_start+4000])
    else:
        await message.answer(reply)


async def handle_tarot_photo(message: Message) -> None:
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    bio = await message.bot.download_file(file.file_path)
    image_b64 = base64.standard_b64encode(bio.read()).decode()

    await message.answer("🔍 Распознаю карты...")

    raw = await ask_claude_vision("Определи карты в раскладе.", image_b64, system=VISION_SYSTEM)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        vision_data = json.loads(raw)
    except Exception:
        await message.answer("⚠️ Не смог распознать карты. Опиши текстом.")
        return

    cards = vision_data.get("cards", [])
    spread_type = vision_data.get("spread_type", "Другой")

    if not cards:
        await message.answer("⚠️ Карты не определены. Опиши текстом.")
        return

    cards_text = ", ".join(f"{c['position']}: {c['card']}" for c in cards)
    question = message.caption or "общий расклад"

    interpretation = await ask_claude(
        f"Расклад: {spread_type}\nВопрос: {question}\nКарты: {cards_text}",
        system=TAROT_SYSTEM,
        max_tokens=2000,
    )

    cards_display = "\n".join(f"  {c['position']} — <b>{c['card']}</b>" for c in cards[:10])
    await message.answer(
        f"🃏 <b>{spread_type}</b>\n\n"
        f"<b>Карты:</b>\n{cards_display}\n\n"
        f"🔮 <b>Трактовка:</b>\n\n{interpretation[:4000]}"
    )
    if len(interpretation) > 4000:
        await message.answer(interpretation[4000:8000])


async def handle_tarot_interpret(message: Message, text: str) -> None:
    interpretation = await ask_claude(
        f"Карты/расклад: {text}",
        system=TAROT_SYSTEM,
        max_tokens=2000,
    )
    await message.answer(f"🔮 <b>Трактовка:</b>\n\n{interpretation[:4000]}")
