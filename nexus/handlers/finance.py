"""nexus/handlers/finance.py"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.claude_client import ask_claude, ask_claude_vision
from core.notion_client import finance_month, log_error, page_create, update_page, create_report_page, _title, _number, _select, _date, _text

logger = logging.getLogger("nexus.finance")
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

_LIMIT_AMOUNT_RE = re.compile(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]')
# Парсит "лимит: 🍱 Кафе/Доставка — 9000₽/мес" → group(1)=категория, group(2)=сумма
_LIMIT_FACT_RE = re.compile(
    r'лимит[:\s]+([^—\-\d]+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE | re.UNICODE,
)
_INCOME_MARKERS_RE = re.compile(
    r'\b(получила|получил|заработала|заработал|зарплата|доход|перевели|перевёл|перевел'
    r'|вернули|вернул|пришло|пришла|поступил[аио]?|аванс)\b',
    re.IGNORECASE,
)
_BARTER_MARKERS_RE = re.compile(r'\b(бартер|обмен|в\s+обмен)\b', re.IGNORECASE)

_RU_MONTHS = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}


def _cat_link(cat: str) -> str:
    """'🚬 Привычки' → 'привычки', '🍱 Кафе/Доставка' → 'кафе'"""
    name = re.sub(r'^[^\w\u0400-\u04FF]+', '', cat, flags=re.UNICODE).strip()
    return name.split('/')[0].strip().lower()


async def _get_limits(mem_db: str) -> Dict[str, float]:
    """Загрузить все лимиты из памяти. Возвращает {cat_link: amount}.

    Стратегия: сначала фильтр по Категория="💰 Лимит", если упал —
    забираем все записи и ищем те, у которых текст начинается с "лимит:".
    Ключ берётся из поля Связь или парсится из факт-текста через _LIMIT_FACT_RE.
    """
    from core.notion_client import db_query
    from core.config import config
    db = mem_db or config.nexus.db_memory
    if not db:
        logger.warning("_get_limits: no memory db configured")
        return {}
    limits: Dict[str, float] = {}
    pages: list = []
    try:
        pages = await db_query(db, filter_obj={
            "property": "Категория", "select": {"equals": "💰 Лимит"}
        }, page_size=100)
        logger.info("_get_limits: category filter → %d pages", len(pages))
    except Exception as e:
        logger.warning("_get_limits: category filter failed (%s), trying text search", e)

    # Если фильтр вернул 0 или упал — берём все страницы и фильтруем сами
    if not pages:
        try:
            all_pages = await db_query(db, page_size=200)
            pages = [
                p for p in all_pages
                if (p["properties"].get("Текст", {}).get("title") or [{}])[0]
                   .get("plain_text", "").lower().startswith("лимит")
            ]
            logger.info("_get_limits: text fallback → %d limit pages from %d total", len(pages), len(all_pages))
        except Exception as e2:
            logger.error("_get_limits: fallback failed: %s", e2, exc_info=True)
            return {}

    for p in pages:
        props = p["properties"]
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""

        # Стратегия 1: поле Связь
        связь_parts = props.get("Связь", {}).get("rich_text", [])
        связь = связь_parts[0]["plain_text"].strip().lower() if связь_parts else ""

        # Стратегия 2: парсим категорию из текста "лимит: 🍱 Кафе/Доставка — 9000₽/мес"
        fact_match = _LIMIT_FACT_RE.search(fact)
        if fact_match and not связь:
            связь = _cat_link(fact_match.group(1).strip())

        # Сумма из текста
        amount_match = _LIMIT_AMOUNT_RE.search(fact)
        logger.info("_get_limits: fact=%r связь=%r amount=%r",
                    fact, связь, amount_match.group(0) if amount_match else None)

        if связь and amount_match:
            limits[связь] = float(amount_match.group(1).replace(' ', '').replace(',', '.'))
        else:
            logger.warning("_get_limits: skip — связь=%r fact=%r", связь, fact)

    logger.info("_get_limits: result=%s", limits)
    return limits


async def _check_budget_limit(category: str, message: Message, user_notion_id: str = "") -> None:
    """После записи расхода — проверить бюджетный лимит по категории."""
    logger.info("_check_budget_limit called: category=%s", category)
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        logger.info("_check_budget_limit: NOTION_DB_MEMORY not set, skip")
        return
    link = _cat_link(category)
    limits = await _get_limits(mem_db)
    logger.info("_check_budget_limit: limits=%s link=%r", limits, link)
    limit_amount: Optional[float] = None
    for key, val in limits.items():
        if key in link or link in key:
            limit_amount = val
            break
    if not limit_amount:
        logger.info("_check_budget_limit: no limit for category=%r, skip", category)
        return

    from core.config import config
    from core.notion_client import db_query
    now = datetime.now(MOSCOW_TZ)
    month_start = now.strftime("%Y-%m-01")
    today_str = now.strftime("%Y-%m-%d")
    try:
        records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип",       "select": {"equals": "💸 Расход"}},
            {"property": "Категория", "select": {"equals": category}},
            {"property": "Дата",      "date":   {"on_or_after": month_start}},
            {"property": "Дата",      "date":   {"on_or_before": today_str}},
        ]}, page_size=200)
        month_total = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in records)
        logger.info("_check_budget_limit: month_total=%.0f limit=%.0f category=%s",
                    month_total, limit_amount, category)
    except Exception as e:
        logger.error("_check_budget_limit db_query: %s", e, exc_info=True)
        return

    pct = month_total / limit_amount * 100 if limit_amount else 0
    if pct >= 100:
        over = month_total - limit_amount
        await message.answer(
            f"🚨 Превышен лимит на {category}: <b>{month_total:,.0f}₽</b> из {limit_amount:,.0f}₽ (+{over:,.0f}₽)"
        )
    elif pct >= 80:
        await message.answer(
            f"⚠️ Уже потрачено <b>{month_total:,.0f}₽</b> из {limit_amount:,.0f}₽ на {category} ({pct:.0f}%)"
        )

    # Прогноз до конца месяца (только если не уже превышен и >= 5 дней данных)
    import calendar as _cal2
    now2 = datetime.now(MOSCOW_TZ)
    day2 = now2.day
    if pct < 100 and day2 >= 5 and day2 < 25 and limit_amount:
        days_in_month = _cal2.monthrange(now2.year, now2.month)[1]
        forecast = month_total / day2 * days_in_month
        if forecast > limit_amount:
            await message.answer(
                f"📈 Прогноз до конца месяца: ~{forecast:,.0f}₽ (лимит {limit_amount:,.0f}₽) — темп высоковат"
            )


async def get_finance_stats(month: str, user_notion_id: str = "", compare_prev: bool = False) -> str:
    """Сводка за месяц с лимитами. month = 'YYYY-MM'."""
    from core.praise import get_praise
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    try:
        records = await finance_month(month, user_notion_id=user_notion_id)
    except Exception as e:
        logger.error("get_finance_stats: %s", e)
        return "⚠️ Ошибка получения данных"

    total_expense = 0.0
    total_income = 0.0
    by_cat: Dict[str, float] = {}

    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        if "Доход" in type_name:
            total_income += amount
        elif "Расход" in type_name:
            total_expense += amount
            if cat:
                by_cat[cat] = by_cat.get(cat, 0) + amount

    limits: Dict[str, float] = {}
    if mem_db:
        limits = await _get_limits(mem_db)
    logger.info("get_finance_stats: mem_db=%r limits=%s by_cat=%s", mem_db, limits, by_cat)

    y, m = int(month[:4]), int(month[5:7])
    month_label = f"{_RU_MONTHS.get(m, month)} {y}"

    lines = [f"📊 <b>Финансы за {month_label}:</b>",
             f"Расходы: <b>{total_expense:,.0f}₽</b>",
             f"Доходы: <b>{total_income:,.0f}₽</b>",
             "", "<b>По категориям:</b>"]

    # cat → (spent, limit_val) для ревью
    cat_review: list[tuple[str, float, float]] = []

    for cat, amount in sorted(by_cat.items(), key=lambda x: -x[1]):
        link = _cat_link(cat)
        limit_val: Optional[float] = None
        for key, val in limits.items():
            logger.info("get_finance_stats: matching cat=%r link=%r key=%r → %s",
                        cat, link, key, key in link or link in key)
            if key in link or link in key:
                limit_val = val
                break
        if limit_val:
            pct = amount / limit_val * 100
            if pct > 100:
                status = f"🔴 (+{amount - limit_val:,.0f}₽)"
            elif pct >= 80:
                status = f"🟡 ({pct:.0f}%)"
            else:
                status = f"🟢 ({pct:.0f}%)"
            lines.append(f"{cat}: {amount:,.0f}₽ / лимит {limit_val:,.0f}₽ {status}")
            cat_review.append((cat, amount, limit_val))
        else:
            lines.append(f"{cat}: {amount:,.0f}₽")

    balance = total_income - total_expense
    sign = "+" if balance >= 0 else ""
    lines.append(f"\n💰 <b>Баланс: {sign}{balance:,.0f}₽</b>")

    # Ревью по лимитам — только если есть хотя бы один лимит
    if cat_review:
        review_lines: List[str] = []
        for cat, spent, lim in cat_review:
            pct = spent / lim * 100
            if pct > 100:
                over = spent - lim
                review_lines.append(
                    f"😬 <b>{cat}</b>: лимит превышен на {over:,.0f}₽. "
                    f"В следующем месяце попробуй уложиться в {lim:,.0f}₽"
                )
            elif pct >= 80:
                review_lines.append(
                    f"⚠️ <b>{cat}</b>: почти весь лимит — {spent:,.0f}₽ из {lim:,.0f}₽"
                )
            elif pct < 50:
                praise = get_praise("finance_under_limit")
                review_lines.append(
                    f"🎉 <b>{cat}</b>: отличный результат! Потратил {spent:,.0f}₽ из {lim:,.0f}₽\n"
                    + praise
                )
            # 50-80% — без комментария
        if review_lines:
            lines.append("\n<b>Ревью по лимитам:</b>")
            lines.extend(review_lines)

    # Прогноз до конца месяца
    import calendar as _cal
    now_fc = datetime.now(MOSCOW_TZ)
    day = now_fc.day
    if day >= 5 and day < 25 and cat_review:
        days_in_month = _cal.monthrange(now_fc.year, now_fc.month)[1]
        forecast_lines: List[str] = []
        for cat, spent, lim in cat_review:
            cat_forecast = spent / day * days_in_month
            if cat_forecast > lim * 1.1:
                forecast_lines.append(
                    f"📈 Прогноз <b>{cat}</b> до конца месяца: ~{cat_forecast:,.0f}₽ "
                    f"(лимит {lim:,.0f}₽) — темп высоковат"
                )
        if forecast_lines:
            lines.append("")
            lines.extend(forecast_lines)

    # Сравнение с предыдущим месяцем
    if compare_prev:
        try:
            prev_month = _month_offset(1)
            prev_records = await finance_month(prev_month, user_notion_id=user_notion_id)
            prev_by_cat: Dict[str, float] = {}
            for r in prev_records:
                props = r["properties"]
                amount = props.get("Сумма", {}).get("number") or 0
                type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
                cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
                if "Расход" in type_name and cat:
                    prev_by_cat[cat] = prev_by_cat.get(cat, 0) + amount

            prev_m_num = int(prev_month[5:7])
            prev_label = _RU_MONTHS.get(prev_m_num, prev_month)

            compare_lines: List[str] = []
            all_cats = set(list(by_cat.keys()) + list(prev_by_cat.keys()))
            for cat in sorted(all_cats, key=lambda c: -by_cat.get(c, 0.0)):
                cur = by_cat.get(cat, 0.0)
                prev = prev_by_cat.get(cat, 0.0)
                delta = cur - prev
                if delta > 0:
                    arrow = f"↑ +{delta:,.0f}₽"
                elif delta < 0:
                    arrow = f"↓ {delta:,.0f}₽"
                else:
                    arrow = "→ без изменений"
                compare_lines.append(f"{cat}: {cur:,.0f}₽ ({arrow} vs {prev_label})")

            if compare_lines:
                lines.append(f"\n<b>Сравнение с {prev_label}:</b>")
                lines.extend(compare_lines)
        except Exception as e:
            logger.debug("compare_prev: %s", e)

    return "\n".join(lines)

CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "🕯️ Расходники", "📚 Хобби/Учеба",
    "💰 Зарплата", "🔮 Практика", "💳 Прочее",
]

SOURCES = ["💳 Карта", "💵 Наличные", "🔄 Бартер"]

STATS_SYSTEM = f"""Определи, запрашивает ли пользователь статистику по конкретной категории или конкретному имени/объекту.
Ответь ТОЛЬКО JSON без markdown:
{{
  "category": "одна из: {', '.join(CATEGORIES)} или null если запрос общей сводки",
  "type_": "expense если спрашивает о расходах, income если о доходах, null если оба",
  "description_search": "ключевое слово/имя для фильтра описания или null. Извлекай имена людей, магазины, организации после слов 'на/у/за/для/от'. Пример: 'у вадима' → 'вадим', 'на клинику' → 'клиника', 'за маму' → 'мама'",
  "months": 1,
  "compare": false
}}

Правила:
- Если в запросе есть имя/магазин/организация/объект рядом со словами 'на/у/за/для/от/по' → description_search = это слово
- Для доходов: 'получила X', 'пришло X', 'заработала X', 'доход по X', 'аренда', 'аренды' → description_search = X, type_=income
- Категория и description_search могут быть вместе: 'расходы на транспорт для вадима' → category=Транспорт, description_search=вадим
- Если категория явно указана (коты, транспорт, продукты...) → category; если имя/человек/объект → description_search
- months: сколько месяцев захватить. "за 3 месяца" → 3, "за полгода" → 6, "за год" → 12. По умолчанию 1
- compare=true если просят сравнить месяцы: "сравни", "сравнение", "как изменились расходы"

Примеры:
"сколько потратила на коты" → {{"category": "🐾 Коты", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"расходы на транспорт" → {{"category": "🚕 Транспорт", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"кола" → {{"category": "🚬 Привычки", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"заработала на практике" → {{"category": "🔮 Практика", "type_": "income", "description_search": null, "months": 1, "compare": false}}
"сколько перевела вадиму" → {{"category": null, "type_": "expense", "description_search": "вадим", "months": 1, "compare": false}}
"расходы на клинику" → {{"category": "🏥 Здоровье", "type_": "expense", "description_search": "клиника", "months": 1, "compare": false}}
"у мамы" → {{"category": null, "type_": null, "description_search": "мама", "months": 1, "compare": false}}
"сколько получила аренды" → {{"category": null, "type_": "income", "description_search": "аренда", "months": 1, "compare": false}}
"сколько пришло от вадима" → {{"category": null, "type_": "income", "description_search": "вадим", "months": 1, "compare": false}}
"доход по аренде" → {{"category": null, "type_": "income", "description_search": "аренда", "months": 1, "compare": false}}
"все доходы" → {{"category": null, "type_": "income", "description_search": null, "months": 1, "compare": false}}
"сводка за месяц" → {{"category": null, "type_": null, "description_search": null, "months": 1, "compare": false}}
"статистика" → {{"category": null, "type_": null, "description_search": null, "months": 1, "compare": false}}
"сколько потратила на еду за 3 месяца" → {{"category": "🍜 Продукты", "type_": "expense", "description_search": null, "months": 3, "compare": false}}
"расходы на кафе за 2 месяца" → {{"category": "🍱 Кафе/Доставка", "type_": "expense", "description_search": null, "months": 2, "compare": false}}
"статистика за полгода" → {{"category": null, "type_": null, "description_search": null, "months": 6, "compare": false}}
"сравни месяцы" → {{"category": null, "type_": null, "description_search": null, "months": 3, "compare": true}}
"сравни расходы на кафе" → {{"category": "🍱 Кафе/Доставка", "type_": "expense", "description_search": null, "months": 2, "compare": true}}"""

PARSE_SYSTEM = f"""Извлеки финансовую запись. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:
{{
  "amount": число (всегда положительное),
  "type_": "💰 Доход" или "💸 Расход",
  "category": "одна из: {', '.join(CATEGORIES)}",
  "source": "одна из: {', '.join(SOURCES)}",
  "description": "краткое описание на русском: кто/что/куда (исправь опечатки)",
  "is_update": false,
  "update_field": null,
  "update_value": null,
  "confidence": "high" или "low",
  "question": "уточняющий вопрос если confidence=low, иначе null"
}}

Если это запрос на ИЗМЕНЕНИЕ последней записи (слова: измени, поменяй, исправь, обнови):
  "is_update": true,
  "update_field": "source" | "category" | "amount" | "description",
  "update_value": "новое значение в точном формате из списка",
  "amount": 0

Правила source: нал/наличные/кэш→"💵 Наличные", бартер→"🔄 Бартер", иначе→"💳 Карта"

Правила confidence:
- confidence=HIGH (не спрашивай): если type_ понятен ИЛИ сумма+категория понятны
- confidence=LOW (спроси): ТОЛЬКО если ВООБЩЕ непонятно — это доход или расход? (например "500р от вадима")
- НЕ ставь low только потому что не знаешь описание — description="?" и high достаточно

Правила type_:
- "💸 Расход" если: имя товара/услуги (такси, продукты, кофе, энергетик), NEGATIVE сумма,
  слова купил/купила/потратила/заплатила/оплатила/потрачено/расход
- "💰 Доход" если: зарплата/аванс/перевод/пришло/получила/поступление/доход/аренда
- По умолчанию если неясно → "💸 Расход"

Категории (выбирай ближайшую, НЕ используй "💳 Прочее" если хоть что-то подходит):
- 🐾 Коты: корм, лоток, ветеринар, игрушки для кота/кошки
- 🏠 Жилье: аренда, ЖКХ, ремонт, мебель, квартира, дом
- 🚬 Привычки: сигареты, табак, алкоголь, энергетик, Monster, Red Bull, Burn, пиво, вино
- 🍜 Продукты: еда, продукты, супермаркет, магазин, снеки, чипсы, орехи, сок, вода, молоко, хлеб
- 🍱 Кафе/Доставка: кафе, ресторан, кофейня, доставка, Самокат, Яндекс.Еда, бургер, пицца, роллы, суши
- 🚕 Транспорт: такси, Яндекс.Такси, метро, автобус, бензин, каршеринг
- 💅 Бьюти: маникюр, педикюр, стрижка, косметика, уходовая, салон красоты, депиляция
- 👗 Гардероб: одежда, обувь, аксессуары, Wildberries, OZON (вещи)
- 💻 Подписки: Netflix, Spotify, iCloud, VPN, ChatGPT, подписка, сервис
- 🏥 Здоровье: аптека, лекарства, врач, анализы, клиника, больница
- 🕯️ Расходники: свечи, масла, травы, ароматизатор, уборка, хозтовары
- 📚 Хобби/Учеба: книги, курсы, кино, театр, концерт, игры, спорт
- 💰 Зарплата: зарплата, аванс (только доход)
- 🔮 Практика: таро, ритуал, сеанс, гадание (доход от практики)
- 💳 Прочее: ТОЛЬКО если ни одна категория выше не подходит вообще

Примеры:
"350 вадим" → description="Вадим", type_="💸 Расход", category="💳 Прочее", confidence="low", question="350р Вадиму — за что?"
"450р такси" → description="Такси", type_="💸 Расход", category="🚕 Транспорт", confidence="high"
"монстр 120" → description="Monster Energy", type_="💸 Расход", category="🚬 Привычки", confidence="high"
"кофе 180р" → description="Кофе", type_="💸 Расход", category="🍱 Кафе/Доставка", confidence="high"
"снеки 300" → description="Снеки", type_="💸 Расход", category="🍜 Продукты", confidence="high"
"пришла зарплата 80к" → description="Зарплата", type_="💰 Доход", category="💰 Зарплата", confidence="high"
"500р от вадима" → description="От Вадима", type_="💸 Расход", category="💳 Прочее", confidence="low", question="500р от Вадима — это доход или расход?"
"измени карту на бартер" → is_update=true, update_field="source", update_value="🔄 Бартер", amount=0
"поменяй категорию на продукты" → is_update=true, update_field="category", update_value="🍜 Продукты", amount=0"""

# Pending-записи до уточнения: {{user_id: data}}
_pending_finance: dict = {}
# Последняя записанная страница: {{user_id: page_id}}
_last_page_id: dict = {}


def _today() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m")


def _format_record(data: dict) -> str:
    icon = "💰" if "Доход" in data.get("type_", "") else "💸"
    return (
        f"✅ Записано\n"
        f"{icon} <b>{data['amount']}₽</b>\n"
        f"📝 {data.get('description', '—')}\n"
        f"🏷 {data.get('category', '—')}\n"
        f"💳 {data.get('source', '—')}"
    )


async def _save_finance(data: dict, db_id: str, bot_label: str = "☀️ Nexus",
                        user_notion_id: str = "", uid: int = 0) -> str:
    """Создаёт запись в Notion. Возвращает page_id или None."""
    from core.notion_client import _relation
    props = {
        "Описание": _title(data.get("description") or ""),
        "Дата":     _date(_today()),
        "Сумма":    _number(float(data["amount"])),
        "Категория": _select(data.get("category", "💳 Прочее")),
        "Тип":      _select(data.get("type_", "💸 Расход")),
        "Источник": _select(data.get("source", "💳 Карта")),
        "Бот":      _select(bot_label),
    }
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    page_id = await page_create(db_id, props)
    if page_id and uid:
        from nexus.handlers.tasks import last_record_set
        last_record_set(uid, "finance", page_id)
    return page_id


async def _update_last_finance(uid: int, field: str, value: str) -> bool:
    """Обновляет поле последней записанной страницы."""
    page_id = _last_page_id.get(uid)
    if not page_id:
        return False

    field_map = {
        "source":      ("Источник", _select(value)),
        "category":    ("Категория", _select(value)),
        "description": ("Описание", _title(value)),
        "amount":      ("Сумма", _number(float(value))),
    }
    if field not in field_map:
        return False

    notion_key, notion_val = field_map[field]
    try:
        await update_page(page_id, {notion_key: notion_val})
        return True
    except Exception as e:
        logger.error("_update_last_finance error: %s", e)
        return False


async def handle_finance_text(message: Message, text: str, bot_label: str = "☀️ Nexus",
                              user_notion_id: str = "") -> None:
    from core.config import config

    raw = await ask_claude(text, system=PARSE_SYSTEM, max_tokens=400)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(text, "parse_error", raw)
        await message.answer("⚠️ Не смог разобрать. Попробуй: «450р такси»")
        return

    uid = message.from_user.id

    # ── Post-processing: форсируем Расход если явные признаки ───────────────
    _EXPENSE_VERBS = re.compile(
        r"\b(купил[аи]?|потратил[аи]?|заплатил[аи]?|оплатил[аи]?|"
        r"потрачено|расход|заплачено|сняла|сняли?)\b",
        re.IGNORECASE,
    )
    raw_amount = data.get("amount", 0)
    # Отрицательная сумма → всегда расход
    if isinstance(raw_amount, (int, float)) and raw_amount < 0:
        data["amount"] = abs(raw_amount)
        data["type_"] = "💸 Расход"
        data["confidence"] = "high"
    # Явный глагол расхода → форсируем тип и убираем вопрос о типе
    elif _EXPENSE_VERBS.search(text):
        data["type_"] = "💸 Расход"
        data["confidence"] = "high"
    # ── Конец post-processing ────────────────────────────────────────────────

    # Запрос на изменение последней записи
    if data.get("is_update"):
        field = data.get("update_field", "")
        value = data.get("update_value", "")
        ok = await _update_last_finance(uid, field, value)
        if ok:
            labels = {"source": "Источник", "category": "Категория",
                      "description": "Описание", "amount": "Сумма"}
            await message.answer(f"✏️ Обновлено: {labels.get(field, field)} → <b>{value}</b>")
        else:
            await message.answer("⚠️ Нет последней записи для обновления.")
        return

    if not data.get("amount"):
        await message.answer("⚠️ Не нашёл сумму.")
        return

    # Низкая уверенность — уточняем только если есть маркеры дохода/бартера
    if data.get("confidence") == "low" and data.get("question"):
        has_income = bool(_INCOME_MARKERS_RE.search(text))
        has_barter = bool(_BARTER_MARKERS_RE.search(text))
        if not has_income and not has_barter:
            # Нет маркеров дохода/бартера → автоматически расход
            logger.info("finance: low confidence but no income/barter markers → auto-expense")
            data["type_"] = "💸 Расход"
            data["confidence"] = "high"
        else:
            _pending_finance[uid] = (data, user_notion_id)
            amount = data.get("amount", 0)
            description = data.get("description", "?")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💸 Расход", callback_data="fin_expense"),
                    InlineKeyboardButton(text="💰 Доход", callback_data="fin_income"),
                    InlineKeyboardButton(text="🔄 Бартер", callback_data="fin_barter"),
                ]
            ])
            await message.answer(
                f"❓ <b>{amount:,.0f}₽ — {description}</b>\n\n"
                f"Это доход, расход или бартер?",
                reply_markup=kb,
            )
            return

    # Высокая уверенность — пишем сразу
    page_id = await _save_finance(data, config.nexus.db_finance, bot_label, user_notion_id, uid=uid)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await message.answer(_format_record(data))
    if "Расход" in data.get("type_", ""):
        logger.info("finance saved: category=%s — calling budget check", data.get("category", ""))
        try:
            await _check_budget_limit(data.get("category", ""), message, user_notion_id)
        except Exception as e:
            logger.error("budget check error: %s", e, exc_info=True)


@router.message(F.text)
async def handle_finance_clarification(message: Message, user_notion_id: str = "") -> None:
    """Текстовые ответы на уточнение: вместо кнопок или уточнение данных."""
    from core.config import config

    uid = message.from_user.id
    pending_entry = _pending_finance.get(uid)
    if not pending_entry:
        return
    # Support both formats: data dict (old) or (data, user_notion_id) tuple (new)
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending = pending_entry
        stored_uid = user_notion_id

    text_lower = (message.text or "").strip().lower()

    if text_lower in ("отмена", "нет", "cancel", "❌"):
        _pending_finance.pop(uid, None)
        await message.answer("❌ Отменено.")
        return

    if text_lower in ("записать", "да", "ок", "ok", "✅", "записать как есть"):
        _pending_finance.pop(uid, None)
        page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
        if page_id:
            _last_page_id[uid] = page_id
            await message.answer(_format_record(pending))
            if "Расход" in pending.get("type_", ""):
                try:
                    await _check_budget_limit(pending.get("category", ""), message, stored_uid)
                except Exception as e:
                    logger.debug("budget check skip: %s", e)
        else:
            await message.answer("⚠️ Ошибка записи в Notion.")
        return

    # Уточнение через Claude
    UPDATE_SYSTEM = (
        f"У тебя финансовая запись и уточнение от пользователя. "
        f"Обнови нужные поля. Ответь ТОЛЬКО JSON без markdown:\n"
        f'{{"description":"...","category":"одна из: {", ".join(CATEGORIES)}",'
        f'"type_":"💰 Доход или 💸 Расход","source":"одна из: {", ".join(SOURCES)}"}}\n'
        f"Текущая запись: {json.dumps(pending, ensure_ascii=False)}"
    )
    raw = await ask_claude(message.text.strip(), system=UPDATE_SYSTEM, max_tokens=200)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        pending.update(json.loads(raw))
    except Exception:
        pass

    _pending_finance.pop(uid, None)
    page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await message.answer(_format_record(pending))
    if "Расход" in pending.get("type_", ""):
        try:
            await _check_budget_limit(pending.get("category", ""), message, stored_uid)
        except Exception as e:
            logger.debug("budget check skip: %s", e)


@router.callback_query(F.data == "fin_save_asis")
async def fin_save_asis(call: CallbackQuery) -> None:
    from core.config import config
    uid = call.from_user.id
    pending_entry = _pending_finance.pop(uid, None)
    if not pending_entry:
        await call.answer("Нет данных.")
        return
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending, stored_uid = pending_entry, ""
    page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
    if page_id:
        _last_page_id[uid] = page_id
    await call.message.edit_text(_format_record(pending))
    if "Расход" in pending.get("type_", ""):
        logger.info("finance saved (asis): category=%s — calling budget check", pending.get("category", ""))
        try:
            await _check_budget_limit(pending.get("category", ""), call.message, stored_uid)
        except Exception as e:
            logger.error("budget check error: %s", e, exc_info=True)
    await call.answer()


@router.callback_query(F.data == "fin_cancel")
async def fin_cancel(call: CallbackQuery) -> None:
    _pending_finance.pop(call.from_user.id, None)
    await call.message.edit_text("❌ Отмена.")
    await call.answer()


@router.callback_query(F.data.startswith("fin_expense") | F.data.startswith("fin_income") | F.data.startswith("fin_barter"))
async def handle_finance_clarify(call: CallbackQuery, user_notion_id: str = "") -> None:
    """Обработчик уточнения доход/расход/бартер для неясных операций."""
    from core.config import config
    from core.notion_client import match_select, _relation

    action = call.data.split("_")[1]  # expense, income или barter
    uid = call.from_user.id

    pending_entry = _pending_finance.get(uid)
    if not pending_entry:
        await call.answer("⚠️ Сессия истекла. Отправь операцию ещё раз.")
        await call.message.edit_text("⚠️ Сессия истекла.")
        return

    await call.answer()

    # Support both formats
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending = pending_entry
        stored_uid = user_notion_id

    amount = float(pending.get("amount", 0))
    category = pending.get("category", "💳 Прочее")
    source = pending.get("source", "💳 Карта")
    description = pending.get("description", "")

    db_id = config.nexus.db_finance

    if action == "barter":
        type_label = "💸 Расход"
        source = "🔄 Бартер"
    elif action == "income":
        type_label = "💰 Доход"
    else:
        type_label = "💸 Расход"

    real_category = await match_select(db_id, "Категория", category)
    real_source = await match_select(db_id, "Источник", source)
    real_type = await match_select(db_id, "Тип", type_label)

    props = {
        "Описание": _title(description),
        "Дата": _date(datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")),
        "Сумма": _number(amount),
        "Категория": _select(real_category),
        "Тип": _select(real_type),
        "Источник": _select(real_source),
        "Бот": _select("☀️ Nexus"),
    }
    eff_uid = stored_uid or user_notion_id
    if eff_uid:
        props["🪪 Пользователи"] = _relation(eff_uid)

    result = await page_create(db_id, props)

    if result:
        from nexus.handlers.tasks import last_record_set
        last_record_set(uid, "finance", result)
        sign = "−" if action != "income" else "+"
        icon = "💸" if action != "income" else "💰"
        text = f"{icon} <b>{sign}{amount:,.0f}₽</b> · <b>{description}</b>\n🏷 {real_category} <i>{real_source}</i>"
        await call.message.edit_text(text, parse_mode="HTML")
        _pending_finance.pop(uid, None)
        if action != "income":
            await _check_budget_limit(real_category, call.message)
    else:
        await call.message.edit_text("⚠️ Ошибка записи. Попробуй позже.")


async def handle_bank_screenshot(message: Message, bot_label: str = "☀️ Nexus") -> None:
    from core.config import config
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    bio = await message.bot.download_file(file.file_path)
    image_b64 = base64.standard_b64encode(bio.read()).decode()

    await message.answer("🔍 Читаю скрин...")

    cats = ", ".join(CATEGORIES)
    srcs = ", ".join(SOURCES)
    system = (
        "Ты анализируешь скрин банковского приложения. "
        "Извлеки ВСЕ транзакции. Ответь ТОЛЬКО JSON без markdown:\n"
        f'{{"transactions": [{{"amount": число, "type_": "💰 Доход|💸 Расход", '
        f'"category": "одна из: {cats}", "source": "одна из: {srcs}", "description": "описание"}}]}}'
    )
    raw = await ask_claude_vision("Извлеки транзакции.", image_b64, system=system)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(message.caption or "bank_screenshot", "parse_error", raw)
        await message.answer("⚠️ Не смог распознать транзакции.")
        return

    uid = message.from_user.id
    saved = []
    for t in data.get("transactions", []):
        amount = float(t.get("amount") or 0)
        if not amount:
            continue
        page_id = await _save_finance(t, config.nexus.db_finance, bot_label)
        if page_id:
            _last_page_id[uid] = page_id
        icon = "💰" if "Доход" in t.get("type_", "") else "💸"
        saved.append(f"{icon} {amount:,.0f}₽ — {t.get('description', '')}")

    if not saved:
        await message.answer("Транзакций не найдено.")
        return
    await message.answer(f"✅ Записано {len(saved)}:\n" + "\n".join(saved[:15]))


_MONTH_MAP = {
    "январ": 1,  "янв": 1,
    "феврал": 2, "фев": 2,
    "март": 3,   "мар": 3,
    "апрел": 4,  "апр": 4,
    "май": 5,    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8, "авг": 8,
    "сентябр": 9,  "сен": 9,
    "октябр": 10,  "окт": 10,
    "ноябр": 11,   "ноя": 11,
    "декабр": 12,  "дек": 12,
}
_MONTH_RE = re.compile(
    r"\b(январ[яеь]?|янв|феврал[яеь]?|фев|март[ае]?|мар|апрел[яеь]?|апр"
    r"|май|мая|июн[яеь]?|июл[яеь]?|август[ае]?|авг"
    r"|сентябр[яеь]?|сен|октябр[яеь]?|окт|ноябр[яеь]?|ноя|декабр[яеь]?|дек)\b",
    re.IGNORECASE,
)


def _parse_month_from_query(text: str) -> str:
    """Вернуть 'YYYY-MM' из текста или текущий месяц."""
    now = datetime.now(MOSCOW_TZ)
    m = _MONTH_RE.search(text.lower())
    if not m:
        return _month()
    word = m.group(1).lower()
    # Найти номер месяца по максимальному совпадению префикса
    month_num = None
    for prefix, num in _MONTH_MAP.items():
        if word.startswith(prefix) or prefix.startswith(word[:3]):
            month_num = num
            break
    if month_num is None:
        return _month()
    # Год: текущий, но если месяц ещё не наступил — тот же год (не будущий)
    year = now.year
    if month_num > now.month:
        year -= 1  # "в декабре" в январе → прошлый декабрь
    return f"{year}-{month_num:02d}"


def _month_offset(offset: int) -> str:
    """Вернуть 'YYYY-MM' для текущего месяца минус offset месяцев."""
    now = datetime.now(MOSCOW_TZ)
    month = now.month - offset
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return f"{year}-{month:02d}"


async def _handle_multimonth_stats(
    months_count: int,
    category_filter: Optional[str],
    type_filter: Optional[str],
    description_search: Optional[str],
    compare_mode: bool,
    user_notion_id: str,
    uid: int,
) -> str:
    """Статистика за несколько месяцев с разбивкой по каждому."""
    _MONTHS_SHORT = "янв фев мар апр май июн июл авг сен окт ноя дек".split()
    notion_desc_kw = (description_search or "")[:5].strip() if description_search else ""

    month_strings = [_month_offset(i) for i in range(months_count - 1, -1, -1)]
    month_totals: List[tuple] = []  # (month_str, total)

    for ms in month_strings:
        records = await finance_month(
            ms,
            user_notion_id=user_notion_id,
            description_filter=notion_desc_kw,
            type_filter=type_filter or "",
        )
        total = 0.0
        for r in records:
            props = r["properties"]
            amount = props.get("Сумма", {}).get("number") or 0
            cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
            type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
            if category_filter and cat_name != category_filter:
                continue
            if type_filter == "expense" and "Расход" not in type_name:
                continue
            if type_filter == "income" and "Доход" not in type_name:
                continue
            total += amount
        month_totals.append((ms, total))

    icon = "💸" if type_filter == "expense" else ("💰" if type_filter == "income" else "📊")
    label_parts = []
    if category_filter:
        label_parts.append(category_filter)
    if description_search:
        label_parts.append(f"«{description_search}»")
    label = " · ".join(label_parts) if label_parts else "Общая статистика"

    lines = [f"{icon} {label} — {months_count} мес."]

    grand_total = 0.0
    for ms, total in month_totals:
        try:
            y, m = int(ms[:4]), int(ms[5:7])
            ml = f"{_MONTHS_SHORT[m - 1]} {y}"
        except Exception:
            ml = ms
        lines.append(f"{ml}: {total:,.0f}₽")
        grand_total += total

    avg = grand_total / months_count if months_count else 0
    lines.append(f"\nИтого: {grand_total:,.0f}₽ · среднее: {avg:,.0f}₽/мес")

    # Лимит если есть категория расходов
    if category_filter and type_filter != "income":
        try:
            mem_db = os.environ.get("NOTION_DB_MEMORY")
            if mem_db:
                limits = await _get_limits(mem_db)
                link = _cat_link(category_filter)
                limit_val: Optional[float] = None
                for key, val in limits.items():
                    if key in link or link in key:
                        limit_val = val
                        break
                if limit_val:
                    avg_pct = avg / limit_val * 100
                    if avg_pct > 100:
                        indicator = "🔴"
                    elif avg_pct >= 80:
                        indicator = "🟡"
                    else:
                        indicator = "🟢"
                    lines.append(
                        f"📊 Лимит: {limit_val:,.0f}₽/мес → в среднем {avg_pct:.0f}% {indicator}"
                    )
        except Exception as e:
            logger.debug("multimonth limit: %s", e)

    report_title = f"{label} — {months_count} мес."
    return await _stats_publish(report_title, lines)


async def handle_finance_summary(query: str = "", user_notion_id: str = "", uid: int = 0) -> str:
    """Возвращает строку со статистикой. Вызывающий сам отправляет её пользователю."""
    logger.info("handle_finance_summary: user_notion_id=%r query=%r", user_notion_id, query)
    # Попробовать распарсить категорию и имя из запроса
    category_filter = None
    type_filter = None
    description_search = None
    parsed: dict = {}
    if query:
        raw = await ask_claude(query, system=STATS_SYSTEM, max_tokens=200)
        try:
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw)
            category_filter = parsed.get("category") or None
            type_filter = parsed.get("type_") or None
            description_search = parsed.get("description_search") or None
            if description_search:
                description_search = description_search.lower().strip()
        except Exception:
            pass

    months_count = max(1, int(parsed.get("months") or 1))
    compare_mode = bool(parsed.get("compare"))

    # Мультимесячный режим
    if months_count > 1:
        return await _handle_multimonth_stats(
            months_count=months_count,
            category_filter=category_filter,
            type_filter=type_filter,
            description_search=description_search,
            compare_mode=compare_mode,
            user_notion_id=user_notion_id,
            uid=uid,
        )

    # Месяц: из текста запроса или текущий
    month_str = _parse_month_from_query(query) if query else _month()

    # Первые 4-5 символов для Notion title contains (fuzzy: "вадима" → "вади" → найдёт "вадиму")
    notion_desc_kw = (description_search or "")[:5].strip() if description_search else ""

    # Notion делает фильтрацию по описанию и типу на стороне API
    records = await finance_month(
        month_str,
        user_notion_id=user_notion_id,
        description_filter=notion_desc_kw,
        type_filter=type_filter or "",
    )
    now = datetime.now(MOSCOW_TZ)

    def _get_desc(props):
        title_items = (props.get("Описание", {}).get("title") or [])
        if title_items:
            return title_items[0].get("text", {}).get("content", "")
        return ""

    # Запрос по категории, описанию ИЛИ конкретному типу дохода/расхода
    if category_filter or description_search or type_filter:
        total = 0.0
        matched = []
        for r in records:
            props = r["properties"]
            amount = props.get("Сумма", {}).get("number") or 0
            cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
            type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
            desc = _get_desc(props)

            # Фильтр по категории (Python-side, Notion не умеет exact match по select)
            if category_filter and cat_name != category_filter:
                continue
            # Фильтр по типу (Notion уже отфильтровал, но дублируем для надёжности)
            if type_filter == "expense" and "Расход" not in type_name:
                continue
            if type_filter == "income" and "Доход" not in type_name:
                continue

            total += amount
            date_str = (props.get("Дата", {}).get("date") or {}).get("start", "")
            matched.append((date_str, desc, amount))

        icon = "💸" if type_filter == "expense" else ("💰" if type_filter == "income" else "📊")
        label = "Расходы" if type_filter == "expense" else ("Доходы" if type_filter == "income" else "Итого")

        # Заголовок
        header_parts = []
        if category_filter:
            header_parts.append(category_filter)
        if description_search:
            header_parts.append(f"«{description_search}»")
        header = " · ".join(header_parts) if header_parts else "Фильтр"

        # Человекочитаемый заголовок месяца
        try:
            month_dt = datetime.strptime(month_str, "%Y-%m")
            month_label = f"{('январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь'.split())[month_dt.month - 1]} {month_dt.year}"
        except Exception:
            month_label = now.strftime("%B %Y")

        report_title = f"{header} — {month_label}"

        if matched:
            all_sorted = sorted(matched, key=lambda x: x[0], reverse=True)

            # Пагинация если записей > PAGE_SIZE и есть uid
            from core.pagination import PAGE_SIZE as _PS, register_pages
            if uid and len(all_sorted) > _PS:
                _MONTHS = "янв фев мар апр май июн июл авг сен окт ноя дек".split()

                def _finance_fmt(it: dict) -> str:
                    try:
                        d = datetime.strptime(it["date"][:10], "%Y-%m-%d")
                        day = f"{d.day} {_MONTHS[d.month - 1]}"
                    except Exception:
                        day = it["date"][:10]
                    return f"• {day} — {it['desc'] or '—'} — {it['amount']:,.0f}₽"

                finance_items = [
                    {"date": ds, "desc": desc, "amount": amt}
                    for ds, desc, amt in all_sorted
                ]
                register_pages(uid, finance_items, f"{icon} {report_title} · {total:,.0f}₽", _finance_fmt)
                return "__paginated__"

            lines = [
                f"{icon} {header} — {month_label}",
                f"{label}: {total:,.0f}₽  ({len(matched)} зап.)",
                "",
            ]
            for date_str, desc, amount in all_sorted:
                try:
                    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    day = f"{d.day} {('янв фев мар апр май июн июл авг сен окт ноя дек'.split())[d.month - 1]}"
                except Exception:
                    day = date_str[:10]
                lines.append(f"• {day} — {desc or '—'} — {amount:,.0f}₽")
        else:
            lines = [
                f"{icon} {header} — {month_label}",
                f"{label}: {total:,.0f}₽  (0 зап.)",
            ]

        # Ревью по лимиту — только для запросов по категории (расходы)
        if category_filter and type_filter != "income":
            try:
                from core.praise import get_praise
                mem_db = os.environ.get("NOTION_DB_MEMORY")
                if mem_db:
                    limits = await _get_limits(mem_db)
                    link = _cat_link(category_filter)
                    limit_val: Optional[float] = None
                    for key, val in limits.items():
                        if key in link or link in key:
                            limit_val = val
                            break
                    if limit_val:
                        pct = total / limit_val * 100
                        remaining = limit_val - total
                        if pct > 100:
                            over = total - limit_val
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n😬 Превышен на {over:,.0f}₽ — постараемся уложиться в следующем месяце"
                            )
                        elif pct >= 80:
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n⚠️ Осталось {remaining:,.0f}₽ — почти весь бюджет"
                            )
                        else:
                            praise = get_praise("finance_under_limit")
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n✅ Осталось {remaining:,.0f}₽\n{praise}"
                            )
            except Exception as e:
                logger.debug("stats limit review: %s", e)

        return await _stats_publish(report_title, lines)

    # Общая сводка
    income_nexus_salary = 0.0
    income_arcana_salary = 0.0
    income_other = 0.0
    expense_total = 0.0
    cat_totals: dict = {}

    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
        bot_name = (props.get("Бот", {}).get("select") or {}).get("name", "")

        if "Доход" in type_name:
            if cat_name == "💰 Зарплата":
                if "Nexus" in bot_name:
                    income_nexus_salary += amount
                elif "Arcana" in bot_name:
                    income_arcana_salary += amount
                else:
                    income_other += amount
            else:
                income_other += amount
        elif "Расход" in type_name:
            expense_total += amount
            cat_totals[cat_name] = cat_totals.get(cat_name, 0.0) + amount

    income_total = income_nexus_salary + income_arcana_salary + income_other
    balance = income_total - expense_total

    report_title = f"Финансы — {now.strftime('%B %Y')}"

    salary_detail = ""
    if income_nexus_salary or income_arcana_salary:
        salary_detail = (
            f"  ☀️ Nexus: {income_nexus_salary:,.0f}₽"
            f" | 🌒 Arcana: {income_arcana_salary:,.0f}₽"
        )

    lines = [
        report_title,
        "",
        f"💰 Доходы: {income_total:,.0f}₽",
    ]
    if salary_detail:
        lines.append(salary_detail)
    lines += [
        f"💸 Расходы: {expense_total:,.0f}₽",
        f"{'🟢' if balance >= 0 else '🔴'} Баланс: {'+' if balance >= 0 else ''}{balance:,.0f}₽",
    ]
    # Топ категорий расходов
    if cat_totals:
        lines.append("")
        lines.append("Топ категорий:")
        for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5]:
            lines.append(f"  {cat}: {amt:,.0f}₽")

    return await _stats_publish(report_title, lines)


async def _stats_publish(title: str, lines: List[str]) -> str:
    """Создать Notion-страницу отчёта (если настроена) или вернуть текст."""
    from core.config import config
    page_reports = config.nexus.page_reports
    if page_reports:
        url = await create_report_page(title, lines, page_reports)
        if url:
            return f"📊 <b>Отчёт готов:</b> <a href=\"{url}\">{title}</a>"

    # Fallback: форматированный текст с HTML
    out = []
    for line in lines:
        if not line:
            out.append("")
        elif line == lines[0]:  # title
            out.append(f"📊 <b>{line}</b>")
        elif line.startswith("💰") or line.startswith("💸") or line.startswith("🟢") or line.startswith("🔴"):
            out.append(f"<b>{line}</b>")
        else:
            out.append(line)
    return "\n".join(out)