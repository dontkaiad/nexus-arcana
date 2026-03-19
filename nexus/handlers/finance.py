"""nexus/handlers/finance.py"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.claude_client import ask_claude, ask_claude_vision
from core.notion_client import finance_month, log_error, page_create, update_page, create_report_page, _title, _number, _select, _date, _text

logger = logging.getLogger("nexus.finance")
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

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
  "description_search": "ключевое слово/имя для фильтра описания или null. Извлекай имена людей, магазины, организации после слов 'на/у/за/для/от'. Пример: 'у вадима' → 'вадим', 'на клинику' → 'клиника', 'за маму' → 'мама'"
}}

Правила:
- Если в запросе есть имя/магазин/организация/объект рядом со словами 'на/у/за/для/от/по' → description_search = это слово
- Для доходов: 'получила X', 'пришло X', 'заработала X', 'доход по X', 'аренда', 'аренды' → description_search = X, type_=income
- Категория и description_search могут быть вместе: 'расходы на транспорт для вадима' → category=Транспорт, description_search=вадим
- Если категория явно указана (коты, транспорт, продукты...) → category; если имя/человек/объект → description_search

Примеры:
"сколько потратила на коты" → {{"category": "🐾 Коты", "type_": "expense", "description_search": null}}
"расходы на транспорт" → {{"category": "🚕 Транспорт", "type_": "expense", "description_search": null}}
"кола" → {{"category": "🚬 Привычки", "type_": "expense", "description_search": null}}
"заработала на практике" → {{"category": "🔮 Практика", "type_": "income", "description_search": null}}
"сколько перевела вадиму" → {{"category": null, "type_": "expense", "description_search": "вадим"}}
"расходы на клинику" → {{"category": "🏥 Здоровье", "type_": "expense", "description_search": "клиника"}}
"у мамы" → {{"category": null, "type_": null, "description_search": "мама"}}
"сколько получила аренды" → {{"category": null, "type_": "income", "description_search": "аренда"}}
"сколько пришло от вадима" → {{"category": null, "type_": "income", "description_search": "вадим"}}
"доход по аренде" → {{"category": null, "type_": "income", "description_search": "аренда"}}
"все доходы" → {{"category": null, "type_": "income", "description_search": null}}
"сводка за месяц" → {{"category": null, "type_": null, "description_search": null}}
"статистика" → {{"category": null, "type_": null, "description_search": null}}"""

PARSE_SYSTEM = f"""Извлеки финансовую запись. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:
{{
  "amount": число,
  "type_": "💰 Доход" или "💸 Расход",
  "category": "одна из: {', '.join(CATEGORIES)}",
  "source": "одна из: {', '.join(SOURCES)}",
  "description": "краткое описание на русском: кто/что/куда (исправь опечатки)",
  "is_update": false,
  "update_field": null,
  "update_value": null,
  "confidence": "high" если всё понятно, "low" если категория или описание неясны,
  "question": "уточняющий вопрос если confidence=low, иначе null"
}}

Если это запрос на ИЗМЕНЕНИЕ последней записи (слова: измени, поменяй, исправь, обнови):
  "is_update": true,
  "update_field": "source" | "category" | "amount" | "description",
  "update_value": "новое значение в точном формате из списка",
  "amount": 0

Правила source: нал/наличные/кэш→"💵 Наличные", бартер→"🔄 Бартер", иначе→"💳 Карта"

Примеры:
"350 вадим" → description="Вадим", category="💳 Прочее", confidence="low", question="Вадим — это кто?"
"450р такси" → description="Такси", category="🚕 Транспорт", source="💳 Карта", confidence="high"
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
                        user_notion_id: str = "") -> str:
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
    return await page_create(db_id, props)


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

    # Низкая уверенность — уточняем
    if data.get("confidence") == "low" and data.get("question"):
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
    page_id = await _save_finance(data, config.nexus.db_finance, bot_label, user_notion_id)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await message.answer(_format_record(data))


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
        page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid)
        if page_id:
            _last_page_id[uid] = page_id
            await message.answer(_format_record(pending))
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
    page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await message.answer(_format_record(pending))


@router.callback_query(F.data == "fin_save_asis")
async def fin_save_asis(call: CallbackQuery) -> None:
    from core.config import config
    uid = call.from_user.id
    pending = _pending_finance.pop(uid, None)
    if not pending:
        await call.answer("Нет данных.")
        return
    page_id = await _save_finance(pending, config.nexus.db_finance)
    if page_id:
        _last_page_id[uid] = page_id
    await call.message.edit_text(_format_record(pending))
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
        sign = "−" if action != "income" else "+"
        icon = "💸" if action != "income" else "💰"
        text = f"{icon} <b>{sign}{amount:,.0f}₽</b> · <b>{description}</b>\n🏷 {real_category} <i>{real_source}</i>"
        await call.message.edit_text(text, parse_mode="HTML")
        _pending_finance.pop(uid, None)
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


async def handle_finance_summary(query: str = "", user_notion_id: str = "") -> str:
    """Возвращает строку со статистикой. Вызывающий сам отправляет её пользователю."""
    # Попробовать распарсить категорию и имя из запроса
    category_filter = None
    type_filter = None
    description_search = None
    if query:
        raw = await ask_claude(query, system=STATS_SYSTEM, max_tokens=150)
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

    # Первые 4-5 символов для Notion title contains (fuzzy: "вадима" → "вади" → найдёт "вадиму")
    notion_desc_kw = (description_search or "")[:5].strip() if description_search else ""

    # Notion делает фильтрацию по описанию и типу на стороне API
    records = await finance_month(
        _month(),
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

        report_title = f"{header} — {now.strftime('%B %Y')}"
        lines = [
            f"{icon} {header} — {now.strftime('%B %Y')}",
            f"{label}: {total:,.0f}₽  ({len(matched)} зап.)",
        ]
        if matched:
            last5 = sorted(matched, key=lambda x: x[0], reverse=True)[:5]
            lines.append("")
            for date_str, desc, amount in last5:
                try:
                    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    day = f"{d.day} {('янв фев мар апр май июн июл авг сен окт ноя дек'.split())[d.month - 1]}"
                except Exception:
                    day = date_str[:10]
                lines.append(f"• {day} — {desc or '—'} — {amount:,.0f}₽")

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