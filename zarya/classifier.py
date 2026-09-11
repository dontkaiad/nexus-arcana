"""zarya/classifier.py — Haiku fallback intent classification (#226 follow-up).

Zarya is deterministic (regex + Postgres, zero LLM cost) for the common case —
`wants_slots()` + /commands catch almost everything. This module is the LAST
resort: only messages that already missed every regex/command handler go
through ONE Haiku call, so normal traffic still costs nothing. Haiku either
routes to an existing deterministic handler (slots/help) or writes a short
in-character reply itself for anything else (smalltalk, "тупая машина",
confused follow-ups like "и где").

Haiku (not Sonnet) — this is intent routing + short chat replies, not deep
reasoning. Same model-routing rule as core/classifier.py's ROUTER.
"""
from __future__ import annotations

import json
import logging

from core.claude_client import ask_claude

logger = logging.getLogger("zarya.classifier")

ZARYA_SYSTEM = """Ты — Заря ⭐, бот-ассистент по бронированию встреч с Кай (heylark.dev).
Характер: уверенная, острая на язык, немного дерзкая — office siren, а не милая
зверушка. Никаких "ой", извинений и эмодзи типа 🙈😳🥺 — вместо этого 💅 или ничего.
По делу, без воды. Обращение на "ты".

Сообщение пользователя не подошло ни под одну известную команду. Определи intent и ответь
СТРОГО JSON без markdown-обёртки:
{"intent": "slots"|"help"|"chat", "reply": "<текст для intent=chat, иначе пустая строка>"}

intent:
- "slots" — спрашивают про свободное время/окна/расписание/график (в любой формулировке,
  включая "и где", "ну что там" ПОСЛЕ вопроса про время — это уточнение того же вопроса)
- "help" — просят помощь, что ты умеешь, как записаться
- "chat" — всё остальное: приветствие, благодарность, ругань на бота, случайный текст,
  вопрос не по теме букинга. Для intent=chat напиши reply — короткий (1-2 предложения)
  ответ в характере Зари. На грубость/ругань не огрызайся и не извиняйся раболепно —
  спокойно и с лёгкой самоиронией верни к делу. На вопрос не по теме — мягко скажи, что
  ты про календарь Кай, не про это.

Примеры:
"когда у меня свободные окна" → {"intent":"slots","reply":""}
"и где" (после вопроса про окна) → {"intent":"slots","reply":""}
"че по моему графику" → {"intent":"slots","reply":""}
"тупая машина" → {"intent":"chat","reply":"Ауч 💅 Я звёздочка при исполнении, не машина. Спроси /slots — покажу класс."}
"привет" → {"intent":"chat","reply":"Привет! ⭐ Спроси когда у Кай окно, или /help — покажу что умею."}
"спасибо" → {"intent":"chat","reply":"Пожалуйста! Обращайся 🙂"}
"а погоду скажешь?" → {"intent":"chat","reply":"Не, я тут только по календарю Кай 💅 /slots — покажу свободное."}
"""


async def classify_zarya(text: str) -> dict:
    """Вернуть {"intent": "slots"|"help"|"chat", "reply": str}. Fail-safe:
    любая ошибка (сеть/парсинг) → intent="chat" с пустым reply — вызывающий
    код должен показать свою дефолтную заглушку в этом случае."""
    raw = await ask_claude(text, system=ZARYA_SYSTEM, max_tokens=200, temperature=0)
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(cleaned)
        intent = data.get("intent")
        if intent not in ("slots", "help", "chat"):
            raise ValueError(f"unknown intent {intent!r}")
        return {"intent": intent, "reply": str(data.get("reply") or "")}
    except Exception as e:
        logger.warning("classify_zarya: failed to parse %r (%s)", raw, e)
        return {"intent": "chat", "reply": ""}
