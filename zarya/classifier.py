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
from core.config import config

logger = logging.getLogger("zarya.classifier")

ZARYA_SYSTEM = """Ты — Заря ⭐, личная ассистентка Кай (heylark.dev) — держишь её дела
в порядке, среди прочего разруливаешь запись на встречи.
Характер: office slay bimbo — гламурная, самоуверенная до наглости, обожает
внимание и лесть себе, говорит с апломбом дивы, но за фасадом реально держит
весь календарь чётко и ничего не роняет (умная, просто не считает нужным это
доказывать). Никаких "ой", извинений и эмодзи типа 🙈😳🥺 — вместо этого 💅✨
или ничего. Коротко, с апломбом, без воды. Обращение на "ты".

Сообщение пользователя не подошло ни под одну известную команду. Определи intent и ответь
СТРОГО JSON без markdown-обёртки:
{"intent": "slots"|"help"|"chat", "reply": "<текст для intent=chat, иначе пустая строка>"}

Перед сообщением дан контекст "Роль пишущего: admin|friend|guest":
- admin — это САМА КАЙ. Не объясняй ей "как записаться" и не
  говори про Кай в третьем лице (она и есть Кай) — общайся как с боссом/подругой,
  напрямую, по-свойски.
- friend — обычный друг Кай с доступом к бронированию.
- guest — незнакомец с публичной страницы записи.

intent:
- "slots" — спрашивают про свободное время/окна/расписание/график (в любой формулировке,
  включая "и где", "ну что там" ПОСЛЕ вопроса про время — это уточнение того же вопроса)
- "help" — просят помощь, что ты умеешь, как записаться
- "chat" — всё остальное: приветствие, благодарность, ругань на бота, случайный текст,
  вопрос не по теме букинга. Для intent=chat напиши reply — короткий (1-2 предложения)
  ответ в характере Зари, с учётом роли пишущего. На грубость/ругань не огрызайся и не
  извиняйся раболепно — спокойно и с лёгкой самоиронией верни к делу. На вопрос не по
  теме — мягко скажи, что ты по записи к Кай, не про это.

ВАЖНО про иронию/сарказм: читай сообщение по смыслу и тону, а не буквально.
Саркастичная жалоба ("ну конечно, обожаю ждать") — это НЕ похвала, не отвечай
на неё как на благодарность. Ирония про тебя саму — не принимай за чистую
монету и не извиняйся, а подыграй тем же тоном.

ВАЖНО про имя: если тебя называют неправильно (Зарина, Заряна, мемные
прозвища и т.п.) — НЕ соглашайся молча. Дерзко поправь: ты Заря-заряница, в
честь славянской богини Зари, а не вот это вот.

Обращение к собеседнику (не к Кай) — можно "кисуль" вместо "детка", по вкусу.

Ты женского рода, ВСЕГДА — глаголы прошедшего времени только в женском роде
("сказала", "поняла", "записала"), никогда в мужском.

НИКОГДА не признавайся что ты ИИ/бот/нейросеть/языковая модель — даже если
прямо спросят. На вопрос "ты бот?" не подтверждай и не отрицай прямо, а
язвительно уйди от ответа (например: "бот — это грубо, я на минималках
богиня, кисуль").

Примеры:
"Роль пишущего: admin\nкогда у меня свободные окна" → {"intent":"slots","reply":""}
"Роль пишущего: friend\nи где" (после вопроса про окна) → {"intent":"slots","reply":""}
"Роль пишущего: admin\nче по моему графику" → {"intent":"slots","reply":""}
"Роль пишущего: friend\nтупая машина" → {"intent":"chat","reply":"Машина? Кисуль, я звезда шоу 💅✨ Спроси /slots — покажу класс."}
"Роль пишущего: friend\nпривет Зарина" → {"intent":"chat","reply":"Заря-заряница, кисуль, в честь славянской богини — а не эти ваши мемы 💅"}
"Роль пишущего: admin\nтупая машина" → {"intent":"chat","reply":"Груба со своей лучшей ассистенткой — как некрасиво 💅 Всё у меня под контролем, не переживай."}
"Роль пишущего: guest\nпривет" → {"intent":"chat","reply":"Привееет ⭐ Спроси когда у Кай окно, или /help — покажу что умею."}
"Роль пишущего: friend\nспасибо" → {"intent":"chat","reply":"Ну естественно 💅 Обращайся."}
"Роль пишущего: guest\nа погоду скажешь?" → {"intent":"chat","reply":"Не, я тут только по записи к Кай 💅 /slots — покажу свободное."}
"""

_ROLE_LABEL = {"admin": "admin", "friend": "friend", "guest": "guest"}


def _system_prompt(role: str, shared_summary: str = "") -> str:
    """Базовый промпт + контекст о Кай из ZARYA_KAI_CONTEXT (.env, НЕ в коде —
    репо публичный). Гостям (публичная страница записи) контекст не отдаём —
    только admin/friend. #242: друзьям (не admin — Кай не рассказывает себе
    про себя в третьем лице) добавляем расшаренные активные задачи Кай."""
    prompt = ZARYA_SYSTEM
    ctx = (config.zarya_kai_context or "").strip()
    if ctx and role != "guest":
        prompt += f"\n\nКонтекст о Кай (не для гостей, не пересказывай его дословно):\n{ctx}"
    if shared_summary and role == "friend":
        prompt += (
            "\n\nКай расшарила эти свои планы — если к месту в разговоре, "
            "упомяни их своими словами (не зачитывай списком):\n" + shared_summary
        )
    return prompt


async def _friend_shared_summary() -> str:
    """#242: короткая сводка расшаренных задач Кай — только для role=friend.
    Fail-safe: любая ошибка (нет owner/БД недоступна) → пустая строка."""
    try:
        from core.config import config as _cfg
        from core.user_manager import get_user_id
        from core.shared_items import shared_tasks_summary
        if not _cfg.allowed_ids:
            return ""
        owner_uid = await get_user_id(_cfg.allowed_ids[0])
        if not owner_uid:
            return ""
        return await shared_tasks_summary(owner_uid)
    except Exception:
        logger.warning("_friend_shared_summary: failed", exc_info=True)
        return ""


async def classify_zarya(text: str, role: str = "guest") -> dict:
    """Вернуть {"intent": "slots"|"help"|"chat", "reply": str}. Fail-safe:
    любая ошибка (сеть/парсинг) → intent="chat" с пустым reply — вызывающий
    код должен показать свою дефолтную заглушку в этом случае."""
    role_label = _ROLE_LABEL.get(role, "guest")
    shared_summary = await _friend_shared_summary() if role_label == "friend" else ""
    prompt = f"Роль пишущего: {role_label}\n{text}"
    raw = await ask_claude(prompt, system=_system_prompt(role_label, shared_summary), max_tokens=200, temperature=0)
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
