"""core/classifier.py — классификация и обработка сообщений Nexus (без circular import)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.claude_client import ask_claude
from core.notion_client import finance_add, log_error
from core.config import ARCANA_KEYWORDS

logger = logging.getLogger("nexus.classifier")
MOSCOW_TZ = timezone(timedelta(hours=3))

# Маппинг известных тегов на эмодзи
TAGS_EMOJI = {
    "практика": "🔮",
    "таро": "🔮",
    "ритуал": "🕯️",
    "расходники": "🕯️",
    "идея": "💡",
    "рецепт": "🍳",
    "здоровье": "❤️",
    "финансы": "💰",
    "мысль": "🧠",
}

_classify_last_raw: str = ""


def today_moscow() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _today() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()[:10]


def build_system(tz_offset: int = 3) -> str:
    now_local = datetime.now(timezone(timedelta(hours=tz_offset)))
    today = now_local.strftime("%Y-%m-%d")
    night_rule = (
        f"- НОЧНАЯ ЛОГИКА: сейчас {now_local.strftime('%H:%M')} (ночь до 05:00) — "
        f"'завтра' = СЕГОДНЯ ({today}), 'послезавтра' = завтра ({(now_local + timedelta(days=1)).strftime('%Y-%m-%d')})"
    ) if now_local.hour < 5 else ""
    cats = ", ".join(["🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
                      "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
                      "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба",
                      "💰 Зарплата", "💳 Прочее"])
    srcs = ", ".join(["💳 Карта", "💵 Наличные", "🔄 Бартер"])

    return "\n".join([
        "Ты помощник Nexus. Классифицируй сообщение пользователя. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:",
        "",
        "expense — трата денег:",
        '{"type":"expense","amount":450.0,"title":"назначение платежа","category":"<из ' + cats + '>","source":"<из ' + srcs + '>","confidence":"high если явно расход, иначе low"}',
        "",
        "income — поступление денег:",
        '{"type":"income","amount":35000.0,"title":"откуда поступление","category":"<из ' + cats + '>","source":"<из ' + srcs + '>","confidence":"high если явно доход, иначе low"}',
        "",
        "update — исправить последнюю запись финансов:",
        '{"type":"update","target":"<expense|income>","field":"<source|category|amount>","new_value":"новое значение"}',
        "Примеры: исправь на налик/карту → field=source; на категорию → field=category; сумму → field=amount",
        "",
        "edit_record — изменить поле существующей записи (задачи или финансовой):",
        '{"type":"edit_record","record_type":"task|finance","record_hint":"поисковые слова","field":"category|priority|title|deadline","new_value":"новое значение"}',
        "Примеры: 'поменяй категорию задачи купить корм на продукты' → edit_record",
        "         'переименуй задачу купить корм в купить корм котам' → edit_record",
        "         'смени приоритет купить молоко на высокий' → edit_record",
        "ВАЖНО: edit_record только если явно упомянуто изменение поля существующей записи!",
        "",
        "task_done — пользователь сообщает что уже выполнил какое-то дело:",
        '{"type":"task_done","task_hint":"ключевые слова названия задачи"}',
        "Примеры: 'сделала покупку корма' → task_done hint='покупка корм'",
        "         'позвонила в клинику' → task_done hint='позвонить клиника'",
        "         'написала отчёт' → task_done hint='написать отчёт'",
        "         'купила корм готово' → task_done hint='купить корм'",
        "         'отметь врача выполненным' → task_done hint='врач'",
        "ВАЖНО: task_done только если речь идёт о чём-то уже сделанном (прошедшее время глагола или слово 'готово'/'выполнено')",
        "",
        "task — задача на Нексус (БЕЗ слов из Арканы!):",
        '{"type":"task","title":"что сделать","category":"<из кат>","priority":"Высокий|Средний|Низкий","deadline":"YYYY-MM-DD или YYYY-MM-DDTHH:MM или null","confidence":"high если есть дата, low если даты нет"}',
        "",
        "arcana_redirect — отправить в Аркану (содержит слова из ARCANA_KEYWORDS):",
        '{"type":"arcana_redirect","text":"оригинальный текст"}',
        "",
        "arcana_clarify — уточнить у пользователя: это для Арканы или обычная задача?",
        '{"type":"arcana_clarify","text":"оригинальный текст","confidence":"low"}',
        "Примеры: 'купить свечи' (может быть и задача и для ритуала) → arcana_clarify с confidence=low",
        "note — заметка:",
        '{"type":"note","text":"содержание","tags":"<список тегов через запятую>"}',
        "",
        "stats — статистика / сводка / вопрос о расходах по категории:",
        '{"type":"stats","query":"<запрос>"}',
        "Примеры stats: 'сколько потратила на коты', 'скок ушло на транспорт', 'сколько потратила на котов в этом месяце',",
        "  'расходы за месяц', 'сводка', 'статистика', 'сколько потратила', 'сколько ушло на продукты'",
        "ВАЖНО: любой вопрос со словами 'сколько/скок потратила/потратил/ушло/израсходовала' → ВСЕГДА stats, НЕ task!",
        "",
        "help:",
        '{"type":"help"}',
        "",
        "ПРАВИЛА:",
        "- ИСПРАВЛЯЙ ОПЕЧАТКИ во всех полях, включая title: 'вадмму'→'вадиму', 'молко'→'молоко'",
        "- source: нал/наличные/кэш/налик → '💵 Наличные'; бартер → '🔄 Бартер'; иначе '💳 Карта'",
        "- type task/expense/income/note: если в тексте есть явное слово ('заметка', 'расход', 'доход', 'задача') - это приоритет! Даже если есть ARCANA_KEYWORDS → определить точный тип БЕЗ redirection",
        "- type task/expense/income: если есть ключевое слово из ARCANA_KEYWORDS (ритуал, практика, расходники, клиент, сеанс...) И нет явного типа → НЕ task/expense/income, а arcana_redirect!",
        "- arcana_redirect: явно для Арканы (слова типа 'ритуал', 'практика', 'сеанс', 'гримуар', 'таро') → сразу редирект без вопросов",
        "- arcana_clarify: подозрительные слова (свечи, травы, масла, пентаграмма), которые могут быть и обычной задачей и для ритуала → спросить пользователя",
        "- type task: только для Нексуса. Обычные задачи/дела: глаголы действия (купить, позвонить, написать, отправить, запросить, посетить, встретиться, забрать, принести, исправить, отремонтировать и т.д.)",
        "- task_done: глагол прошедшего времени (сделала/выполнила/купила/написала/позвонила/закончила/отправила) БЕЗ суммы → task_done, НЕ task!",
        "- task_done vs expense: 'купила корм' БЕЗ суммы → task_done; 'купила корм 500₽' → expense",
        "- ПРИОРИТЕТ stats: фразы 'сколько потратила/потратил', 'скок потратила', 'сколько ушло', 'сколько израсходовала' → ВСЕГДА stats, даже если есть категория или слово 'на'! НЕ task, НЕ expense.",
        "- ВАЖНО: Короткие глаголы ВСЕГДА задача (type=task), даже без деталей! Примеры: 'позвонить'→task, 'написать'→task, 'купить'→task (confidence=low если нет деталей)",
        "- Если просто глагол БЕЗ объекта (типа 'написать') → task с title=исходный глагол, confidence=low",
        "- type expense/income: если 'доход'/'пришла'/'зарплата'/'поступление' → income, confidence=high; 'расход'/'потрачено' → expense, confidence=high",
        "- category для income: ТОЛЬКО если явно 'зарплата' → '💰 Зарплата'; иначе '💳 Прочее' (даже если просто 'доход')",
        "- confidence: high если есть явное слово (доход/расход/бартер); low если только сумма+имя (спросить потом)",
        "- title: ВСЕГДА объединяй всё остальное в одну строку. Пример: '450 такси карта вадмму' → title='такси вадиму' (исправленная опечатка)",
        "- priority: срочно/важно/сегодня → 'Высокий'; потом → 'Низкий'; иначе 'Средний'",
        "- deadline: день недели → вычисли ISO дату от " + today + "; сегодня=" + today,
        "- deadline с временем: парсить 'завтра в 15:00' → YYYY-MM-DDTHH:MM; 'в 14:30 без даты' → сегодня+время",
    ] + ([night_rule] if night_rule else []) + [
        "- к/тыс в суммах: 35к = 35000",
        "- tags: из [практика, таро, ритуал, идея, рецепт, здоровье, финансы, мысль]",
        '- неизвестная строка -> {"type":"unknown"}',
        "",
        "ARCANA_KEYWORDS (→ arcana_redirect): " + ", ".join(sorted(ARCANA_KEYWORDS)),
        "",
        "ПРИМЕР:",
        "  Ввод (4 строки):",
        "    450 такси карта вадмму",
        "    доход 50к нал",
        "    пришла зарплата 80к",
        "    12к котам",
        "  Ответ JSON:",
        '  [{"type":"expense","amount":450,"title":"такси вадиму","category":"🚕 Транспорт","source":"💳 Карта","confidence":"high"},',
        '   {"type":"income","amount":50000,"title":"доход","category":"💳 Прочее","source":"💵 Наличные","confidence":"high"},',
        '   {"type":"income","amount":80000,"title":"зарплата","category":"💰 Зарплата","source":"💳 Карта","confidence":"high"},',
        '   {"type":"expense","amount":12000,"title":"котам","category":"🐾 Коты","source":"💳 Карта","confidence":"low"}]',
        "",
        "ПРИМЕР STATS:",
        "  Ввод: 'скок потратила на котов в этом месяце'",
        '  Ответ: {"type":"stats","query":"скок потратила на котов в этом месяце"}',
        "",
        "ПРИМЕР ARCANA_REDIRECT:",
        "  Ввод: 'провести ритуал защиты'",
        '  Ответ: {"type":"arcana_redirect","text":"провести ритуал защиты"}',
        "",
        "ПРИМЕР ARCANA_CLARIFY:",
        "  Ввод: 'купить свечи'",
        '  Ответ: {"type":"arcana_clarify","text":"купить свечи","confidence":"low"}',
        "  (Пользователь выберет: это для Арканы или просто задача?)",
    ])


_EDIT_RE = re.compile(
    r"\b(поменяй|измени|обнови|переименуй|переимен|смени|измените|переименовать)\b"
    r".{0,40}\b(категорию|приоритет|название|дедлайн|имя|статус)\b",
    re.IGNORECASE,
)

_RENAME_RE = re.compile(
    r"\bпереименуй\b.{0,60}\bв\b",
    re.IGNORECASE,
)

_DONE_RE = re.compile(
    r"\b(сделал[аи]?\b|выполнил[аи]?\b|закончил[аи]?\b|завершил[аи]?\b|"
    r"позвонил[аи]\b|написал[аи]\b|отправил[аи]\b|забрал[аи]\b|"
    r"готово\b|готова\b|выполнено\b|сделано\b|"
    r"отметь\s+\w+\s+выполненным|отметь\s+выполненным|уже\s+сделал[аи]?\b)",
    re.IGNORECASE,
)

_TZ_RE = re.compile(
    r"(я\s+в\s+\w+|переезжаю\s+в\s+\w+|мой\s+часовой\s+пояс|utc[+-]\d|в\s+спб\b|в\s+москве\b|"
    r"в\s+екб\b|в\s+екатеринбурге\b|в\s+новосибирске\b|в\s+владивостоке\b|"
    r"в\s+иркутске\b|в\s+красноярске\b|в\s+хабаровске\b|в\s+омске\b|в\s+челябинске\b|"
    r"часовой\s+пояс|timezone)",
    re.IGNORECASE,
)

_STATS_RE = re.compile(
    r"(скол?ько|скок|сколько)\s+(потратил[аи]?|ушло|израсходовал[аи]?|трачу|потрачено)"
    r"|расходы\s+за\s+(месяц|неделю|период|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр|январ|феврал)"
    r"|(финансовая?\s+)?сводка"
    r"|статистика\s+(за|расходов|доходов)",
    re.IGNORECASE,
)


_EDIT_PARSE_SYSTEM = (
    "Извлеки параметры редактирования записи. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"type":"edit_record","record_type":"task","record_hint":"ключевые слова для поиска","field":"category|priority|title|deadline","new_value":"новое значение"}\n'
    "\nПравила:\n"
    "- record_type: 'task' если о задаче, 'finance' если о финансовой записи\n"
    "- field: 'category' для категории; 'priority' для приоритета; 'title' или 'name' для переименования; 'deadline' для дедлайна\n"
    "- record_hint: фраза для поиска записи (название задачи/финансовой операции)\n"
    "- new_value: новое значение\n"
    "\nПримеры:\n"
    "'поменяй категорию задачи купить корм на Продукты' → record_type=task, record_hint='купить корм', field=category, new_value='Продукты'\n"
    "'переименуй задачу купить корм в купить корм котам' → record_type=task, record_hint='купить корм', field=title, new_value='купить корм котам'\n"
    "'смени приоритет купить молоко на высокий' → record_type=task, record_hint='купить молоко', field=priority, new_value='Высокий'\n"
)


async def _parse_edit_record(text: str) -> dict:
    """Распарсить запрос на редактирование записи."""
    raw = await ask_claude(text, system=_EDIT_PARSE_SYSTEM, max_tokens=200, model="claude-haiku-4-5-20251001")
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        data["type"] = "edit_record"
        return data
    except Exception:
        return {"type": "edit_record", "record_hint": text, "field": "unknown", "new_value": ""}


async def classify(text: str, tz_offset: int = 3) -> list[dict]:
    """Классифицировать текст через Claude."""
    logger.info("classify: input text=%r tz_offset=%d", text[:100], tz_offset)

    # Быстрый pre-фильтр: изменение записи ("поменяй категорию X на Y", "переименуй X в Y")
    if _EDIT_RE.search(text) or _RENAME_RE.search(text):
        logger.info("classify: edit_record pattern matched")
        parsed = await _parse_edit_record(text)
        return [parsed]

    # Быстрый pre-фильтр: задача выполнена ("сделала X", "X готово")
    if _DONE_RE.search(text):
        logger.info("classify: task_done pattern matched")
        return [{"type": "task_done", "task_hint": text}]

    # Быстрый pre-фильтр: timezone
    if _TZ_RE.search(text):
        logger.info("classify: timezone pattern matched")
        return [{"type": "timezone_update", "text": text}]

    # Быстрый pre-фильтр: stats-запросы не отдаём Claude — он их путает с task
    if _STATS_RE.search(text):
        logger.info("classify: stats pattern matched, bypassing Claude")
        return [{"type": "stats", "query": text}]

    raw = await ask_claude(text, system=build_system(tz_offset), max_tokens=1024)
    global _classify_last_raw
    _classify_last_raw = raw
    
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            items = [items]
        logger.info("classify: parsed %d items: %s", len(items), [i.get("type") for i in items])
        return items
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("classify: bad JSON for %r → %r", text, raw)
        await log_error(text, "parse_error", raw, str(e), error_code="–")
        return [{"type": "parse_error"}]


async def process_item(data: Dict[str, Any], original_text: str, msg, clarify: dict, user_notion_id: str = "") -> str:
    """Обработка классифицированного элемента."""
    kind = data.get("type", "unknown")
    logger.info("process_item: type=%r data=%s", kind, data)

    # EDIT RECORD
    if kind == "edit_record":
        from nexus.handlers.tasks import handle_edit_record
        await handle_edit_record(
            msg,
            record_hint=data.get("record_hint", original_text),
            field=data.get("field", ""),
            new_value=data.get("new_value", ""),
            record_type=data.get("record_type", "task"),
            user_notion_id=user_notion_id,
        )
        return ""

    # TASK DONE
    if kind == "task_done":
        from nexus.handlers.tasks import handle_task_done
        await handle_task_done(msg, data.get("task_hint", original_text), user_notion_id=user_notion_id)
        return ""

    # TIMEZONE UPDATE
    if kind == "timezone_update":
        from nexus.handlers.tasks import _update_user_tz
        await _update_user_tz(msg, data.get("text", original_text))
        return ""

    if kind == "unknown":
        return "❓ Не смог разобрать. Попробуй переформулировать."
    
    if kind == "parse_error":
        logged = await log_error(original_text, "parse_error", _classify_last_raw, error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        raw_preview = _classify_last_raw[:200] if _classify_last_raw else "—"
        return f"❌ Не понял: <code>{raw_preview}</code>\n{notion_status}"

    # АРКАНА РЕДИРЕКТ
    if kind == "arcana_redirect":
        return ("🔮 <b>Это работа для Арканы!</b>\n\n"
                "Перейди в <a href=\"https://t.me/arcana_kailark_bot\">🌒 Arcana</a> и отправь туда:\n"
                f"<code>{original_text[:100]}</code>\n\n"
                "Там я помогу с ритуалами, практикой и сеансами.")
    
    # АРКАНА УТОЧНЕНИЕ - спросить пользователя
    if kind == "arcana_clarify":
        confidence = data.get("confidence", "low")
        if confidence == "low":
            return f"arcana_clarify:{original_text}"
        # Если confidence=high - редирект без вопроса
        return ("🔮 <b>Это работа для Арканы!</b>\n\n"
                "Перейди в <a href=\"https://t.me/arcana_kailark_bot\">🌒 Arcana</a> и отправь туда:\n"
                f"<code>{original_text[:100]}</code>\n\n"
                "Там я помогу с ритуалами, практикой и сеансами.")

    # ФИНАНСЫ
    if kind in ("expense", "income"):
        confidence = data.get("confidence", "high")
        type_label = "💸 Расход" if kind == "expense" else "💰 Доход"
        
        # Safe cast amount
        raw_amount = data.get("amount")
        amount = float(raw_amount) if raw_amount not in (None, "", 0) else 0
        
        # Определить переменные ДО проверки confidence
        category = data.get("category", "💳 Прочее")
        source = data.get("source", "💳 Карта")
        title = data.get("title", original_text[:50])
        
        logger.info("process_item: finance %s - amount=%.0f category=%r source=%r confidence=%r", 
                   kind, amount, category, source, confidence)
        
        # Low confidence → UI вместо сохранения (даже если amount=0)
        if confidence == "low":
            logger.info("process_item: low confidence finance, showing UI for clarification")
            return f"finance_clarify:{kind}:{amount}:{category}:{source}:{title}"
        
        # High confidence + amount=0 → пропустить
        if amount == 0:
            logger.info("process_item: high confidence but amount=0, skipping")
            return ""
        
        # High confidence + amount > 0 → сохранить в Notion
        logger.info("process_item: saving to Notion - %s %s %s", type_label, amount, category)
        result = await finance_add(
            date=today_moscow(),
            amount=amount,
            category=category,
            type_=type_label,
            source=source,
            description=title,
            user_notion_id=user_notion_id,
        )
        if result:
            sign = "−" if kind == "expense" else "+"
            icon = "💸" if kind == "expense" else "💰"
            return f"{icon} <b>{sign}{amount:,.0f}₽</b> · <b>{title}</b>\n🏷 {category} <i>{source}</i>"
        
        logged = await log_error(original_text, "processing_error", _classify_last_raw,
                                 "finance_add вернул None", error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        return f"❌ Ошибка записи финансов · {notion_status}"

    # UPDATE - исправить последнюю финансовую запись
    if kind == "update":
        from core.notion_client import finance_update
        target = data.get("target", "expense")  # expense или income
        field = data.get("field", "source")  # source, category, amount
        new_value = data.get("new_value", "")
        
        result = await finance_update(target_type=target, field=field, new_value=new_value)
        if result:
            field_name = {"source": "Источник", "category": "Категория", "amount": "Сумма"}.get(field, field)
            return f"✏️ Обновлено: {field_name} → {new_value}"
        
        return "❌ Ошибка при обновлении записи"
    if kind == "task":
        from nexus.handlers.tasks import handle_task_parsed, _REL_TIME_RE, _parse_relative_time, _get_user_tz
        logger.info("classifier: task detected - title=%r category=%r deadline=%r priority=%r",
                   data.get("title"), data.get("category"), data.get("deadline"), data.get("priority"))

        # Post-processing: исправить относительное время которое Claude мог понять неверно
        # "через 2 мин" → Claude пишет "00:02", правильно: datetime.now() + timedelta(minutes=2)
        rel_match = _REL_TIME_RE.search(original_text)
        if rel_match:
            uid = msg.from_user.id
            tz_offset = await _get_user_tz(uid)
            relative_time = _parse_relative_time(original_text, tz_offset)
            unit = rel_match.group(2).lower()
            if unit.startswith("мин") or unit.startswith("ч"):
                logger.info("classifier: overriding deadline→reminder_time with relative=%s", relative_time)
                data["reminder_time"] = relative_time
                data["deadline"] = None
            else:
                logger.info("classifier: overriding deadline with relative=%s", relative_time)
                data["deadline"] = relative_time

        logger.info("classifier: calling handle_task_parsed with full data=%s", data)
        data["user_notion_id"] = user_notion_id
        await handle_task_parsed(msg, data)
        return ""

    # ЗАМЕТКИ
    if kind == "note":
        from nexus.handlers.notes import handle_note
        from core.config import config
        
        logger.info("process_item: note - text=%r tags=%r", data.get("text", "")[:50], data.get("tags", ""))
        
        # Получить теги из classifier
        raw_tags = data.get("tags", "")
        
        # Добавить эмодзи к известным тегам
        if raw_tags:
            tag_list = [t.strip().lstrip("#").lower() for t in raw_tags.split(",")]
            enriched_tags = []
            for tag in tag_list:
                if tag in TAGS_EMOJI:
                    enriched_tags.append(f"{TAGS_EMOJI[tag]} {tag.capitalize()}")
                else:
                    enriched_tags.append(tag)
            raw_tags = ", ".join(enriched_tags)
        
        await handle_note(msg, data.get("text", original_text), config.nexus.db_notes, raw_tags,
                          user_notion_id=user_notion_id)
        return ""

    # СТАТИСТИКА
    if kind == "stats":
        tg_id = msg.from_user.id if msg and msg.from_user else "unknown"
        logger.info(
            "process_item: stats request - tg_id=%s user_notion_id=%r query=%r",
            tg_id, user_notion_id, data.get("query", ""),
        )
        from nexus.handlers.finance import handle_finance_summary
        return await handle_finance_summary(query=data.get("query", ""), user_notion_id=user_notion_id)

    # ПОМОЩЬ
    if kind == "help":
        logger.info("process_item: help request")
        return ("📋 <b>Nexus понимает свободный текст</b>\n\n"
                "Просто пиши как есть — я разберусь:\n\n"
                "💸 <b>Финансы:</b> <code>450р такси</code>, <code>пришла аренда 35000</code>\n"
                "✓ <b>Задачи:</b> <code>купить корм коту</code>, <code>записаться к врачу</code>\n"
                "📝 <b>Заметки:</b> <code>идея про подкаст</code>, <code>рецепт #здоровье</code>\n"
                "🔮 <b>Для Арканы:</b> <code>купить свечи</code>, <code>ритуал новолуния</code>\n\n"
                "Или команды: <code>/help /stats /start</code>")

    return f"❌ Не так ответил Claude · пусть Кай правит промпт"