"""nexus_bot.py — Telegram-бот NEXUS. Claude — единственный роутер."""
from __future__ import annotations

import logging
import traceback as tb
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import config
from core.middleware import WhitelistMiddleware
from core.notion_client import log_error
from core.classifier import classify, process_item

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("nexus")

from core.logging_notion import install as _install_notion_logging
_install_notion_logging(bot_label="☀️ Nexus")

bot = Bot(
    token=config.nexus.tg_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
dp.message.middleware(WhitelistMiddleware())
dp.callback_query.middleware(WhitelistMiddleware())

from nexus.handlers.tasks import router as tasks_router
from nexus.handlers.finance import router as finance_router
from nexus.handlers.memory import router as memory_router
from nexus.handlers.lists import router as lists_router
dp.include_router(tasks_router)
dp.include_router(finance_router)
dp.include_router(memory_router)
dp.include_router(lists_router)

MOSCOW_TZ = timezone(timedelta(hours=3))
_clarify: dict = {}
_pending_finance: dict = {}  # user_id → (kind, amount, category, source, title)
_last_finance_ts: dict = {}  # user_id → timestamp последней записанной финансовой записи

import re as _re_nexus
_TYPE_CORRECTION_RE = _re_nexus.compile(
    r"^\s*(нет[,\s]+)?(это|был[аи]?|на самом деле)?\s*(это\s+)?(доход|расход)\s*$",
    _re_nexus.IGNORECASE,
)
_pending_arcana: dict = {}  # user_id → text (оригинальный для arcana_clarify)
_pending_unknown: dict = {}  # user_id → (text, user_notion_id, ts)


@dp.message(Command("start"))
async def cmd_start(msg: Message, user_notion_id: str = "") -> None:
    if not user_notion_id:
        await msg.answer("⛔ У тебя нет доступа. Обратись к владельцу.")
        return
    await msg.answer(
        "☀️ <b>Привет! Я NEXUS — твой персональный ИИ-ассистент.</b>\n\n"
        "Понимаю естественный язык — команды учить не нужно, просто пиши.\n\n"
        "📋 <b>Задачи</b> — создавать, напоминать, повторять, стрики\n"
        "💸 <b>Финансы</b> — расходы, доходы, лимиты\n"
        "💰 <b>Бюджет</b> — планирование, долги, цели\n"
        "🗒️ <b>Списки</b> — покупки, чеклисты, инвентарь\n"
        "✍️ <b>Заметки</b> — сохранять и искать по тегам\n"
        "🧠 <b>Память</b> — запоминать предпочтения и привычки\n"
        "🦋 <b>СДВГ</b> — персональный профиль, нудж, поддержка\n"
        "📸 <b>Фото</b> — скрины из банка → автопарсинг\n"
        "🎤 <b>Голосовые</b> — надиктуй, я пойму\n\n"
        "Подробнее — /help\n\n"
        '👩‍💻 Создатель: <a href="https://github.com/dontkaiad">Кай Ларк</a>\n'
        '❓ Ошибки/вопросы? <a href="https://t.me/hey_lark">@hey_lark</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("help"))
async def cmd_help(msg: Message, user_notion_id: str = "") -> None:
    await msg.answer(
        "ГАЙД ☀️ <b>NEXUS</b>\n"
        "Понимаю текст, голосовые 🎤 и фото 📸 — команды учить не нужно.\n\n"

        "📋 <b>ЗАДАЧИ</b>\n"
        "/tasks — задачи на сегодня + все остальные\n"
        "/today — экспресс: сегодня + бюджет + совет\n"
        "/stats — статистика + стрики 🔥\n"
        "Текстом: «купить корм коту», «напомни завтра в 10»\n"
        "Повторы: «напоминай пить воду каждый день в 9:00»\n\n"

        "💸 <b>ФИНАНСЫ</b>\n"
        "/finance — расходы за сегодня + сводка за месяц\n"
        "Текстом: «450р такси», «доход 50000»\n"
        "📸 Фото: скрин из банка → автопарсинг\n\n"

        "💰 <b>БЮДЖЕТ</b>\n"
        "/budget — бюджет + кнопка «Изменить данные»\n"
        "Текстом: «лимит привычки 15к», «закрыла долг Вике»\n\n"

        "🗒️ <b>СПИСКИ</b>\n"
        "/list — покупки + чеклисты + инвентарь\n"
        "Текстом: «купить молоко, яйца» → добавить\n"
        "«дома есть: парацетамол 2 пачки» → инвентарь\n"
        "«есть ибупрофен?» → поиск\n"
        "«разбей задачу X на подзадачи» → чеклист\n\n"

        "✍️ <b>ЗАМЕТКИ</b>\n"
        "/notes — все заметки с тегами\n"
        "Текстом: «запиши: идея для проекта»\n\n"

        "🧠 <b>ПАМЯТЬ</b> И 🦋 <b>СДВГ</b>\n"
        "/memory — что я помню о тебе\n"
        "/adhd — СДВГ-профиль\n"
        "Текстом: «запомни: монстры = привычки»\n\n"

        "🔮 <b>ЭЗОТЕРИКА И ПРАКТИКА</b>\n"
        'Всё про таро, ритуалы, клиентов, расходники → <a href="https://t.me/arcana_kailark_bot">🌒 Аркана</a>\n\n'

        "⚙️ <b>ПРОЧЕЕ</b>\n"
        "/start — приветствие\n"
        "/help — этот гайд\n"
        "/fixstreak — восстановить стрик из истории задач\n\n"

        '👩‍💻 Создатель: <a href="https://github.com/dontkaiad">Кай Ларк</a>\n'
        '❓ Ошибки/вопросы? <a href="https://t.me/hey_lark">@hey_lark</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )



@dp.message(Command("tasks"))
async def cmd_tasks(msg: Message, user_notion_id: str = "") -> None:
    """Задачи: СЕГОДНЯ + стрик + все остальные + СДВГ-совет."""
    from core.notion_client import query_pages, _with_user_filter
    from core.config import config
    from datetime import date as _date
    import random

    uid = msg.from_user.id if msg.from_user else 0

    # Все активные задачи
    base_filter = {"and": [
        {"property": "Статус", "status": {"does_not_equal": "Done"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        {"property": "Статус", "status": {"does_not_equal": "Complete"}},
    ]}
    filters = _with_user_filter(base_filter, user_notion_id)
    all_tasks = await query_pages(
        config.nexus.db_tasks, filters=filters,
        sorts=[{"property": "Приоритет", "direction": "descending"}],
        page_size=100,
    )
    if not all_tasks:
        await msg.answer("📭 Задач нет.")
        return

    today_str = _date.today().isoformat()
    _pri_icons = {"Срочно": "🔴", "Важно": "🟡", "Можно потом": "⚪"}
    _rep_labels = {"Ежедневно": "ежедневно", "Еженедельно": "еженедельно", "Ежемесячно": "ежемесячно"}

    overdue_items = []
    today_items = []
    daily_items = []
    other_items = []

    for t in all_tasks:
        props = t["properties"]
        title_parts = props.get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        priority_raw = (props.get("Приоритет", {}).get("select") or {}).get("name", "Важно")
        priority = priority_raw
        for _pk in _pri_icons:
            if _pk in priority_raw:
                priority = _pk
                break
        category = (props.get("Категория", {}).get("select") or {}).get("name", "")
        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
        reminder_raw = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
        repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "")
        is_repeat = repeat and repeat != "Нет"
        cat_icon = category[0] if category else "📌"

        deadline_date = deadline_raw[:10] if deadline_raw else ""
        reminder_date = reminder_raw[:10] if reminder_raw else ""

        # Время
        time_str = ""
        if "T" in reminder_raw:
            time_str = reminder_raw.split("T")[1][:5]
        elif "T" in deadline_raw:
            time_str = deadline_raw.split("T")[1][:5]

        # Дедлайн дисплей
        if is_repeat:
            dl = f"🔄 {_rep_labels.get(repeat, repeat.lower())}"
        elif deadline_date:
            dl = f"до {deadline_date[8:10]}.{deadline_date[5:7]}"
        else:
            dl = ""
        if time_str and not is_repeat:
            dl += f" {time_str}" if dl else time_str

        pri_icon = _pri_icons.get(priority, "⚪")
        item = {"pri_icon": pri_icon, "cat_icon": cat_icon, "title": title, "dl": dl,
                "priority": priority, "is_repeat": is_repeat}

        # Разделяем: просроченные / сегодня / ежедневные / остальные
        if is_repeat and repeat == "Ежедневно":
            daily_items.append(item)
        elif deadline_date and deadline_date < today_str and not is_repeat:
            overdue_items.append(item)
        elif (deadline_date == today_str or reminder_date == today_str) and not is_repeat:
            today_items.append(item)
        else:
            other_items.append(item)

    _all_top = overdue_items + today_items + daily_items
    lines: list[str] = ["📋 <b>Задачи · ☀️ Nexus</b>\n"]

    # ПРОСРОЧЕНО
    if overdue_items:
        lines.append("<b>🔥 ПРОСРОЧЕНО</b>")
        for it in overdue_items:
            line = f"  <i>{it['pri_icon']} {it['title']} · {it['cat_icon']}"
            if it["dl"]:
                line += f" · {it['dl']} ⚠️"
            line += "</i>"
            lines.append(line)
        lines.append("")

    # СЕГОДНЯ
    lines.append("<b>📅 СЕГОДНЯ</b>")
    if today_items or daily_items:
        for it in today_items:
            line = f"  <i>{it['pri_icon']} {it['title']} · {it['cat_icon']}"
            if it["dl"]:
                line += f" · {it['dl']}"
            line += "</i>"
            lines.append(line)
        for it in daily_items:
            line = f"  <i>{it['pri_icon']} {it['title']} · {it['cat_icon']}"
            if it["dl"]:
                line += f" · {it['dl']}"
            line += "</i>"
            lines.append(line)
    else:
        _FREE_TIPS = [
            "🌟 На сегодня чисто — иди отдыхай!",
            "✨ Свободный день — можно просто быть.",
            "🎉 Ноль задач на сегодня — ты заслужила.",
            "🌈 Сегодня без дедлайнов — редкая радость.",
            "🦋 Ничего срочного — мозг скажет спасибо за паузу.",
        ]
        lines.append(f"  {random.choice(_FREE_TIPS)}")
    lines.append("")

    # Стрик
    streak_line = "🔥 Стрик: 0 — начни сегодня!"
    try:
        from nexus.handlers.streaks import get_streak
        streak_data = get_streak(uid)
        s = streak_data.get("streak", 0) if streak_data else 0
        if s > 0:
            fire = "🔥" * min(s, 5)
            streak_line = f"{fire} {s} дней подряд"
    except Exception as e:
        logger.warning("tasks streak error: %s", e)
    lines.append(f"{streak_line}\n")

    # ВСЕ ОСТАЛЬНЫЕ
    if other_items:
        if _all_top:
            lines.append(f"<b>📋 ВСЕ ЗАДАЧИ</b> (ещё {len(other_items)})")
        else:
            lines.append(f"<b>📋 ВСЕ ЗАДАЧИ ({len(other_items)})</b>")
        for it in other_items[:10]:
            line = f"  <i>{it['pri_icon']} {it['title']} · {it['cat_icon']}"
            if it["dl"]:
                line += f" · {it['dl']}"
            line += "</i>"
            lines.append(line)
        if len(other_items) > 10:
            lines.append(f"  <i>...и ещё {len(other_items) - 10}</i>")

    # СДВГ-совет
    _TIPS = [
        "💡 Начни с одной задачи — не пытайся охватить всё сразу.",
        "🦋 Если задач много — выбери 3 главных, остальные подождут.",
        "⚡ Правило 2 минут: если можно сделать за 2 минуты — делай сейчас.",
        "🎯 Застрял? Разбей задачу на шаги поменьше.",
        "🌀 Переключение между задачами тратит энергию — заверши одну, потом следующую.",
        "✨ Не жди мотивации — начни делать, мотивация подтянется.",
        "🔥 Идеально не бывает. Сделано лучше чем идеально.",
    ]
    lines.append(f"\n{random.choice(_TIPS)}")

    text = "\n".join(lines)
    if len(text) <= 4000:
        await msg.answer(text)
    else:
        parts = text.split("\n")
        chunk = ""
        for line in parts:
            if len(chunk) + len(line) + 1 > 4000:
                await msg.answer(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            await msg.answer(chunk)


@dp.message(Command("today"))
async def cmd_today(msg: Message, user_notion_id: str = "") -> None:
    """Экспресс: сегодня + стрик + бюджет + совет — всё в одном."""
    from nexus.handlers.tasks import handle_tasks_today
    await handle_tasks_today(msg, user_notion_id=user_notion_id)


@dp.message(Command("notes"))
async def cmd_notes(msg: Message, user_notion_id: str = "") -> None:
    """Показать все заметки с пагинацией."""
    from nexus.handlers.notes import handle_note_search
    await handle_note_search(msg, {"query": ""}, user_notion_id=user_notion_id)


@dp.message(Command("memory"))
async def cmd_memory(msg: Message, user_notion_id: str = "") -> None:
    """/memory [категория] — все активные записи памяти, сгруппированные по категориям."""
    from core.layout import maybe_convert
    text = maybe_convert(msg.text or "")
    parts = text.strip().split(maxsplit=1)
    category_filter = parts[1] if len(parts) > 1 else ""
    # Скрываем СДВГ и Лимит если не запрошены явно
    cat_low = category_filter.lower().strip()
    is_adhd_request = cat_low in ("сдвг", "adhd", "🧠 сдвг", "🦋 сдвг")
    is_budget_request = cat_low in ("лимит", "бюджет", "💰 лимит", "финансы")
    from nexus.handlers.memory import handle_memory_list
    await handle_memory_list(msg, category_filter=category_filter,
                             user_notion_id=user_notion_id,
                             exclude_adhd=not is_adhd_request,
                             exclude_budget=not is_budget_request)


@dp.message(Command("adhd"))
async def cmd_adhd(msg: Message, user_notion_id: str = "") -> None:
    from nexus.handlers.memory import handle_adhd_command
    await handle_adhd_command(msg, user_notion_id=user_notion_id)


@dp.message(Command("budget"))
async def cmd_budget(msg: Message, user_notion_id: str = "") -> None:
    """v2: всегда Sonnet-анализ с текущими данными."""
    from nexus.handlers.finance import start_budget_analysis
    await start_budget_analysis(msg, user_notion_id)



@dp.message(Command("finance"))
async def cmd_finance(msg: Message, user_notion_id: str = "") -> None:
    """Финансы: свободных/день + лимиты на грани + по категориям."""
    import random, calendar as _cal
    from core.notion_client import finance_month
    from core.classifier import today_moscow
    from nexus.handlers.finance import _calc_free_remaining, _get_limits, _cat_link

    _FINANCE_ADHD_TIPS = [
        "💡 Записал — значит контролируешь. Мозг с СДВГ не считает в фоне.",
        "🧠 Не ругай себя за траты — анализируй и корректируй.",
        "⚡ Лайфхак: записывай расход сразу, потом забудешь.",
        "🎯 Маленькие траты незаметны по одной, но складываются в тысячи.",
        "🌀 Импульсивная покупка? Подожди 24 часа — часто отпускает.",
        "✨ Каждая записанная трата — шаг к финансовой осознанности.",
    ]

    _RU_MONTHS_CMD = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                      5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                      9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}

    today = today_moscow()
    month = today[:7]
    now = datetime.now(MOSCOW_TZ)
    month_label = f"{_RU_MONTHS_CMD.get(now.month, '')} {now.year}"

    records = await finance_month(month, user_notion_id=user_notion_id)

    # Считаем расходы по категориям + сегодня
    total_expense = 0.0
    by_cat: dict[str, float] = {}
    today_lines: list[str] = []
    today_total = 0.0
    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        if "Расход" not in type_name:
            continue
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        total_expense += amount
        by_cat[cat] = by_cat.get(cat, 0) + amount
        date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
        if date == today:
            desc_parts = props.get("Описание", {}).get("title", [])
            desc = desc_parts[0]["plain_text"] if desc_parts else "—"
            today_lines.append(f"  💸 {desc} · {cat} · {amount:,.0f}₽")
            today_total += amount

    # Свободных/день
    free_result = await _calc_free_remaining(user_notion_id)
    if free_result:
        free_left, days_rem = free_result
        daily_budget = free_left / max(days_rem, 1)
    else:
        free_left, days_rem, daily_budget = 0, 0, 0

    # Лимиты
    import os as _os
    mem_db = _os.environ.get("NOTION_DB_MEMORY")
    limits: dict[str, float] = {}
    if mem_db:
        limits = await _get_limits(mem_db)

    lines: list[str] = [f"💰 <b>{month_label}</b>\n"]

    # Главная строка
    if free_result:
        lines.append(f"💳 Свободных: <b>{free_left:,.0f}₽</b> · {daily_budget:,.0f}₽/день")
    # Потрачено из планового бюджета (income)
    from nexus.handlers.finance import _load_budget_data
    budget = await _load_budget_data(user_notion_id)
    plan_income = sum(d["amount"] for d in budget.get("доходы", []))
    if plan_income > 0:
        lines.append(f"📊 Потрачено: {total_expense:,.0f}₽ из {plan_income:,.0f}₽")
    else:
        lines.append(f"📊 Потрачено: {total_expense:,.0f}₽")

    # Сегодня
    if today_lines:
        lines.append(f"\n<b>💸 Сегодня ({today_total:,.0f}₽):</b>")
        lines.extend(today_lines)

    # На грани (>50% лимита)
    warns: list[str] = []
    for lim_key, lim_val in limits.items():
        if lim_val <= 0:
            continue
        spent = 0.0
        for cat_k, cat_s in by_cat.items():
            cl = _cat_link(cat_k)
            if lim_key in cl or cl in lim_key:
                spent += cat_s
        pct = int(spent / lim_val * 100)
        if pct >= 50:
            remaining = lim_val - spent
            color = "🔴" if pct >= 90 else "🟡"
            warns.append(f"  {lim_key.capitalize()}: {pct}% {color} (~{remaining:,.0f}₽ осталось)")
    if warns:
        lines.append(f"\n<b>🚨 На грани:</b>")
        lines.extend(warns)

    # По категориям (отсортированные по сумме)
    if by_cat:
        lines.append(f"\n<b>📋 По категориям:</b>")
        for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            cl = _cat_link(cat)
            lim = None
            for lk, lv in limits.items():
                if lk in cl or cl in lk:
                    lim = lv
                    break
            if lim:
                lines.append(f"  {cat}: {amt:,.0f} / {lim:,.0f}₽")
            else:
                lines.append(f"  {cat}: {amt:,.0f}₽")

    # СДВГ-совет
    lines.append(f"\n{random.choice(_FINANCE_ADHD_TIPS)}")

    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("finance_stats"))
async def cmd_finance_stats(msg: Message, user_notion_id: str = "") -> None:
    """Алиас → /finance."""
    await cmd_finance(msg, user_notion_id=user_notion_id)


@dp.message(Command("stats"))
async def cmd_stats(msg: Message, user_notion_id: str = "") -> None:
    """Статистика задач и стрики."""
    from nexus.handlers.tasks import handle_task_stats
    await handle_task_stats(msg, user_notion_id=user_notion_id)



@dp.message(Command("fixstreak"))
async def cmd_fixstreak(msg: Message, user_notion_id: str = "") -> None:
    """Пересчитать стрик из истории Notion."""
    from core.notion_client import query_pages, _with_user_filter
    from core.config import config
    from nexus.handlers.streaks import rebuild_streak_from_dates
    from nexus.handlers.tasks import _get_user_tz

    uid = msg.from_user.id
    tz_offset = await _get_user_tz(uid)

    filters = _with_user_filter(None, user_notion_id)
    all_tasks = await query_pages(
        config.nexus.db_tasks, filters=filters,
        sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
        page_size=500,
    )

    done_dates = []
    for t in all_tasks:
        props = t["properties"]
        status = (props.get("Статус", {}).get("status") or {}).get("name", "")
        if status not in ("Done", "Complete"):
            continue
        completion_raw = (props.get("Время завершения", {}).get("date") or {}).get("start", "")
        if not completion_raw:
            completion_raw = t.get("last_edited_time", "")
        if completion_raw:
            done_dates.append(completion_raw[:10])

    result = rebuild_streak_from_dates(uid, done_dates, tz_offset)
    await msg.answer(
        f"✅ Стрик пересчитан из {len(done_dates)} выполненных задач\n"
        f"🔥 Текущий стрик: {result['streak']} дней\n"
        f"🏆 Лучший: {result['best']} дней"
    )


@dp.message(Command("tz"))
async def set_tz(msg: Message, user_notion_id: str = "") -> None:
    """Установить часовой пояс. /tz UTC+5 или /tz Екатеринбург"""
    from nexus.handlers.tasks import _update_user_tz
    await _update_user_tz(msg, msg.text.replace("/tz", "").strip())


@dp.message(Command("list"))
async def cmd_list(msg: Message, user_notion_id: str = "") -> None:
    from nexus.handlers.lists import handle_list_command
    await handle_list_command(msg, user_notion_id=user_notion_id)


@dp.message(F.text)
async def handle_text(msg: Message, user_notion_id: str = "") -> None:
    from core.layout import maybe_convert
    from nexus.handlers.tasks import _pending_has, _pending_get, handle_task_clarification, handle_reschedule_reminder, _update_user_tz

    # Budget v2: payday reminder (once per period start)
    try:
        from nexus.handlers.finance import maybe_payday_reminder
        await maybe_payday_reminder(msg, user_notion_id)
    except Exception:
        pass

    from nexus.handlers.utils import react

    # Budget setup — перехватывает текст пока идёт настройка
    from nexus.handlers.finance import handle_budget_setup_text
    if await handle_budget_setup_text(msg, user_notion_id):
        await react(msg, "⚡")
        return

    # Lists pending — чеклист пункты, срок годности
    from nexus.handlers.lists import handle_list_pending
    if await handle_list_pending(msg, user_notion_id):
        await react(msg, "🫡")
        return

    # Receipt clarify pending — уточнение категорий фото-чека текстом
    if await _handle_receipt_clarify(msg, user_notion_id):
        await react(msg, "👌")
        return

    # Quick triggers (до классификатора)
    _tl = (msg.text or "").strip().lower()

    # Бюджет — v2: всегда Sonnet
    import re as _quick_re
    if _quick_re.search(r"покажи бюджет|сколько (могу тратить|свободных)|бюджет на месяц", _tl):
        from nexus.handlers.finance import start_budget_analysis
        await start_budget_analysis(msg, user_notion_id)
        await react(msg, "🏆")
        return

    # День отдыха (стрик)
    if _quick_re.search(r"день\s+отдыха|передышка|отдыхаю\s+сегодня", _tl):
        try:
            from nexus.handlers.streaks import request_rest_day
            from nexus.handlers.tasks import _get_user_tz
            tz = await _get_user_tz(msg.from_user.id) or 3
            result = await request_rest_day(msg.from_user.id, tz)
            await msg.answer(result)
        except Exception as e:
            await msg.answer("⚠️ Ошибка: {}".format(e))
        await react(msg, "⚡")
        return

    if _pending_has(msg.from_user.id):
        pending = _pending_get(msg.from_user.id)
        if pending and pending.get("action") == "reschedule":
            await handle_reschedule_reminder(msg)
            await react(msg, "⚡")
            return
        # Если это edit-команда — обновляем pending задачу напрямую
        import re as _re
        _text_low = (msg.text or "").strip()
        _edit_match = _re.search(
            r"\b(?:поменяй|измени|обнови|смени|замени|исправь)\s+(?:категорию|категория)\s+(?:на\s+)?(.+)",
            _text_low, _re.IGNORECASE,
        )
        if _edit_match:
            from nexus.handlers.tasks import _pending_set
            from core.classifier import _TASK_CATS
            new_cat = _edit_match.group(1).strip()
            # Ищем в _TASK_CATS
            real_cat = new_cat
            for tc in _TASK_CATS:
                if new_cat.lower() in tc.lower():
                    real_cat = tc
                    break
            pending["category"] = real_cat
            _pending_set(msg.from_user.id, pending)
            await msg.answer(f"✏️ Категория обновлена: {real_cat}\n\n<i>Уточни дедлайн или нажми «Сохранить»</i>")
            await react(msg, "⚡")
            return
        _edit_pri = _re.search(
            r"\b(?:поменяй|измени|обнови|смени|замени|исправь)\s+(?:приоритет)\s+(?:на\s+)?(.+)",
            _text_low, _re.IGNORECASE,
        )
        if _edit_pri:
            from nexus.handlers.tasks import _pending_set
            new_pri = _edit_pri.group(1).strip()
            _pri_map = {"срочно": "Срочно", "важно": "Важно", "можно потом": "Можно потом", "потом": "Можно потом"}
            real_pri = _pri_map.get(new_pri.lower(), new_pri)
            pending["priority"] = real_pri
            _pending_set(msg.from_user.id, pending)
            await msg.answer(f"✏️ Приоритет обновлён: {real_pri}\n\n<i>Уточни дедлайн или нажми «Сохранить»</i>")
            await react(msg, "⚡")
            return
        await handle_task_clarification(msg)
        await react(msg, "⚡")
        return

    # ── Уточнение после создания задачи (5-мин окно) ─────────────────────────
    _raw_text = (msg.text or "").strip()
    from nexus.handlers.tasks import _last_task_get, _CLARIFY_RE, handle_last_task_clarify
    if _last_task_get(msg.from_user.id) and _CLARIFY_RE.search(_raw_text):
        _handled = await handle_last_task_clarify(msg, _raw_text, msg.from_user.id, user_notion_id)
        if _handled:
            await react(msg, "⚡")
            return

    # Перехват "это доход" / "это расход" — исправляем тип последней записи в Notion
    _type_m = _TYPE_CORRECTION_RE.match((msg.text or "").strip())
    if _type_m:
        type_word = _type_m.group(4).lower()
        new_type = "💰 Доход" if type_word == "доход" else "💸 Расход"
        # Ищем последнюю запись противоположного типа (чтобы исправить на нужный)
        from core.notion_client import finance_update
        ok = await finance_update(
            target_type="expense" if type_word == "доход" else "income",
            field="type_",
            new_value=new_type,
        )
        if not ok:
            # Попробовать и тот же тип (вдруг уже правильный, но нужна другая правка)
            ok = await finance_update(target_type=type_word, field="type_", new_value=new_type)
        if ok:
            await msg.answer(f"✏️ Тип исправлен → <b>{new_type}</b>", parse_mode="HTML")
        else:
            await msg.answer("⚠️ Нет записи для обновления.")
        return

    # Если последняя запись была финансовой (< 2 мин) и юзер говорит "измени категорию" —
    # обновить финансовую запись, а не задачу
    import time as _time_now
    _fin_edit_re = _re_nexus.compile(
        r"\b(поменяй|измени|обнови|смени|замени|исправь)\s+(категорию|категория)\s+(?:на\s+)?(.+)",
        _re_nexus.IGNORECASE,
    )
    _fin_edit_m = _fin_edit_re.search((msg.text or "").strip())
    if _fin_edit_m:
        last_fin = _last_finance_ts.get(msg.from_user.id, 0)
        if _time_now.time() - last_fin < 120:  # 2 минуты
            new_cat = _fin_edit_m.group(3).strip()
            from core.notion_client import finance_update
            ok = await finance_update(target_type="expense", field="category", new_value=new_cat)
            if not ok:
                ok = await finance_update(target_type="income", field="category", new_value=new_cat)
            if ok:
                await msg.answer(f"✏️ Категория → <b>{new_cat}</b>", parse_mode="HTML")
            else:
                await msg.answer("⚠️ Нет записи для обновления.")
            return

    text = maybe_convert(msg.text.strip())
    await process_text(msg, text, user_notion_id)


async def process_text(msg: Message, text: str, user_notion_id: str = "") -> None:
    """Ядро обработки текста: spell correction → classify → process_item → ответ.

    Вызывается из handle_text, handle_voice, handle_photo (caption).
    """
    from core.layout import maybe_convert
    from nexus.handlers.tasks import _get_user_tz
    from nexus.handlers.utils import react

    original_text = text  # ВАЖНО: сохранить до spell correction

    # ── Исправляем опечатки через Claude Haiku ───────────────────────────
    # ВАЖНО: проверяем что ответ — исправленный текст, а не разговорный ответ Claude.
    # Если ответ намного длиннее оригинала или начинается как разговорная фраза → используем оригинал.
    _CONVERSATIONAL_STARTS = (
        "я не", "извините", "к сожалению", "я имею", "я могу", "я не могу",
        "не имею", "у меня нет", "мне не", "как ии", "как ai",
    )
    from core.claude_client import ask_claude
    try:
        corrected = await ask_claude(
            text,
            system="Исправь опечатки и описки. Если нет ошибок — верни текст как есть. Только текст, без объяснений.",
            max_tokens=100,
            model="claude-haiku-4-5-20251001"
        )
        if corrected:
            c = corrected.strip()
            c_low = c.lower()
            # Отклоняем если: ответ в 2+ раза длиннее оригинала ИЛИ начинается разговорно
            too_long = len(c) > len(text) * 2 + 30
            conversational = any(c_low.startswith(s) for s in _CONVERSATIONAL_STARTS)
            if too_long or conversational:
                logger.warning("spell correction rejected (too_long=%s conversational=%s): %r", too_long, conversational, c[:80])
            else:
                text = c
    except Exception as e:
        logger.error("spell correction error: %s", e)

    if msg.reply_to_message and msg.reply_to_message.text:
        prev = maybe_convert(msg.reply_to_message.text.strip())
        text = f"[контекст: {prev[:100]}]\n{text}"

    from nexus.handlers.utils import react
    await react(msg, "👀")
    await msg.bot.send_chat_action(msg.chat.id, "typing")
    uid = msg.from_user.id

    from nexus.handlers.tasks import _get_user_tz
    tz_offset = await _get_user_tz(uid)

    if uid in _clarify:
        original = _clarify.pop(uid)
        combined = f"{original}\nУточнение: {text}"
        try:
            items = await classify(combined, tz_offset=tz_offset)
            if items and items[0].get("type") not in ("unknown", "parse_error", None):
                lines = []
                for data in items:
                    line = await process_item(data, combined, msg, _clarify, user_notion_id=user_notion_id)
                    if line:
                        lines.append(line)
                if lines:
                    if len(lines) == 1:
                        await msg.answer(lines[0])
                    else:
                        body = "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))
                        await msg.answer(f"Записано {len(lines)} операций:\n\n{body}")
                # react уже вызван в process_item
                return
        except Exception:
            pass
        logged = await log_error(combined, "unknown_type", "", error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        await msg.answer(f"🌒 Так и не понял · {notion_status}")
        await react(msg, "🤔")
        return

    # ── URL + note keywords → быстрый путь в заметки ─────────────────────
    import re as _re_url
    _URL_PAT = _re_url.compile(r'https?://\S+')
    _NOTE_KW = _re_url.compile(r'\b(в заметк[иу]|запиши|сохрани|заметка)\b', _re_url.IGNORECASE)
    _found_urls = _URL_PAT.findall(original_text)
    _url_shortcut = False
    if _found_urls and _NOTE_KW.search(original_text):
        # Текст содержит URL + явное указание "в заметки" → сразу note
        url_str = " ".join(_found_urls)
        clean = _URL_PAT.sub("", original_text).strip()
        # Убираем ключевое слово
        clean = _re_url.sub(r'\b(в заметк[иу]|запиши|сохрани|заметка)\b', '', clean, flags=_re_url.IGNORECASE).strip()
        note_title = f"{clean} — {url_str}" if clean else url_str
        _url_items = [{"type": "note", "text": note_title, "tags": "", "url": url_str}]
        _url_shortcut = True
        logger.info("handle_text: URL+note shortcut → title=%s url=%s", note_title[:50], url_str[:80])

    try:
        if _url_shortcut:
            items = _url_items
        else:
            items = await classify(original_text, tz_offset=tz_offset)
        logger.info("handle_text: classify returned %d items: %s", len(items), [i.get("type") for i in items])

        lines = []
        has_clarify = False
        finance_data = None
        arcana_clarify_text = None
        unknown_clarify_text = None

        for data in items:
            logger.info("handle_text: processing item type=%s", data.get("type"))
            line = await process_item(data, original_text, msg, _clarify, user_notion_id=user_notion_id)
            logger.info("handle_text: process_item returned: %s", line[:50] if line else "None/empty")

            if line and line.startswith("finance_clarify:"):
                # Распарсить: finance_clarify:kind:amount:category:source:title
                parts = line.split(":", 5)
                if len(parts) == 6:
                    _, kind, amount_str, category, source, title = parts
                    finance_data = {
                        "kind": kind,
                        "amount": float(amount_str),
                        "category": category,
                        "source": source,
                        "title": title,
                    }
                    _pending_finance[msg.from_user.id] = (finance_data, text, user_notion_id)
                    has_clarify = True
            elif line and line.startswith("arcana_clarify:"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    arcana_clarify_text = parts[1]
                    _pending_arcana[msg.from_user.id] = arcana_clarify_text
            elif line and line.startswith("unknown_clarify:"):
                unknown_clarify_text = line.split(":", 1)[1]
                import time as _time
                _pending_unknown[msg.from_user.id] = (unknown_clarify_text, user_notion_id, _time.time())
            elif line:
                lines.append(line)
                # Запомнить время последней финансовой записи для контекста редактирования
                if data.get("type") in ("expense", "income") and "₽" in line:
                    import time as _time2
                    _last_finance_ts[msg.from_user.id] = _time2.time()

        # Show UI if unknown — offer action buttons
        if unknown_clarify_text:
            short = unknown_clarify_text[:60]
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🛒 В покупки", callback_data=f"unk_buy_{msg.from_user.id}"),
                    InlineKeyboardButton(text="📋 Задача", callback_data=f"unk_task_{msg.from_user.id}"),
                ],
                [
                    InlineKeyboardButton(text="📝 Заметка", callback_data=f"unk_note_{msg.from_user.id}"),
                    InlineKeyboardButton(text="🧠 Запомнить", callback_data=f"unk_mem_{msg.from_user.id}"),
                ]
            ])
            await msg.answer(
                f"🤔 Не понял «<b>{short}</b>»\nЧто сделать?",
                reply_markup=kb,
            )
            await react(msg, "🤔")
        # Show UI if arcana clarify needed
        elif arcana_clarify_text:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔮 Это для Арканы", callback_data=f"arcana_choice_yes_{msg.from_user.id}"),
                    InlineKeyboardButton(text="✓ Это обычная задача", callback_data=f"arcana_choice_no_{msg.from_user.id}"),
                ]
            ])
            text_msg = (
                f"❓ <b>{arcana_clarify_text}</b>\n\n"
                f"Это для ритуалов/практики (Аркана) или обычная задача?"
            )
            await msg.answer(text_msg, reply_markup=kb)
            await react(msg, "🤔")
        # Show UI if low confidence finance
        elif has_clarify and finance_data:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💸 Расход", callback_data=f"fin_type_expense_{msg.from_user.id}"),
                    InlineKeyboardButton(text="💰 Доход", callback_data=f"fin_type_income_{msg.from_user.id}"),
                ],
                [
                    InlineKeyboardButton(text="🔄 Бартер", callback_data=f"fin_type_barter_{msg.from_user.id}"),
                ]
            ])
            text_msg = (
                f"❓ {finance_data['amount']:,.0f}₽ — <b>{finance_data['title']}</b>\n\n"
                f"Это расход, доход или бартер?"
            )
            await msg.answer(text_msg, reply_markup=kb)
            await react(msg, "🤔")
        else:
            if len(lines) == 1:
                await msg.answer(lines[0])
            elif len(lines) > 1:
                body = "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))
                await msg.answer(f"Записано {len(lines)} операций:\n\n{body}")

        # Финальная реакция по типу из classify (не по результату process_item)
        item_type = items[0].get("type", "unknown") if items else "unknown"
        _final_react = item_type

    except Exception as e:
        _final_react = "error"
        trace = tb.format_exc()
        logger.error("handle_text error: %s", trace)
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
        logged = await log_error(text, "processing_error", "", trace, error_code=code)
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        short_err = err_str[:200] if err_str else "—"
        await msg.answer(
            f"❌ {suffix}\n"
            f"<code>{short_err}</code>\n"
            f"{notion_status}"
        )
        await react(msg, "🤡")
        return

    # Безусловная финальная реакция по типу classify
    # Только валидные Telegram реакции!
    _REACTION_MAP = {
        "task": "⚡", "expense": "👌", "income": "🏆",
        "note": "✍️", "memory_save": "💅", "memory_search": "👀",
        "memory_delete": "💅", "memory_deactivate": "💅",
        "edit_note": "✍️", "note_search": "👀", "note_delete": "✍️",
        "task_done": "🔥", "task_cancel": "😈",
        "list_buy": "🫡", "list_done": "🏆", "list_done_bulk": "🏆",
        "list_check": "🫡", "list_subtask": "🫡",
        "list_inventory_add": "🌚", "list_inventory_search": "👀",
        "list_inventory_update": "🌚",
        "edit_record": "⚡", "stats": "🤓",
        "budget": "🏆", "debt_command": "🏆",
        "goal_command": "🏆", "limit_override": "🏆",
        "timezone_update": "⚡",
        "unknown": "🤔", "parse_error": "🤡",
        "arcana_redirect": "🌚",
        "adhd": "❤️‍🔥",
    }
    await react(msg, _REACTION_MAP.get(_final_react, "⚡"))


# ── Voice messages ──────────────────────────────────────────────────────────

@dp.message(F.voice | F.audio)
async def handle_voice(msg: Message, user_notion_id: str = "") -> None:
    """Голосовое → Whisper → текст → pipeline."""
    from nexus.handlers.utils import react
    from core.voice import transcribe

    if msg.voice:
        file = await msg.bot.get_file(msg.voice.file_id)
    else:
        file = await msg.bot.get_file(msg.audio.file_id)

    file_io = await msg.bot.download_file(file.file_path)
    content = file_io.read()

    await react(msg, "👂")

    text = await transcribe(content)
    if text is None:
        await msg.answer("🎤 Голосовые не настроены (OPENAI_API_KEY).")
        return
    if not text:
        await msg.answer("🎤 Не удалось распознать голосовое.")
        return

    await msg.answer(f"🎤 <i>«{text}»</i>", parse_mode="HTML")

    # Lists pending — могут ждать ответ на чек/чеклист
    from nexus.handlers.lists import handle_list_pending
    if await handle_list_pending(msg, user_notion_id):
        return

    await process_text(msg, text, user_notion_id)


# ── Photo messages (receipts) ──────────────────────────────────────────────

_RECEIPT_CATS_MAP = {
    "коты": "🐾 Коты", "кот": "🐾 Коты", "корм": "🐾 Коты",
    "продукты": "🍜 Продукты", "еда": "🍜 Продукты",
    "кафе": "🍱 Кафе/Доставка", "доставка": "🍱 Кафе/Доставка",
    "транспорт": "🚕 Транспорт", "такси": "🚕 Транспорт",
    "сигареты": "🚬 Привычки", "привычки": "🚬 Привычки", "табак": "🚬 Привычки",
    "бьюти": "💅 Бьюти", "салон": "💅 Бьюти", "ногти": "💅 Бьюти",
    "здоровье": "🏥 Здоровье", "аптека": "🏥 Здоровье", "лекарства": "🏥 Здоровье",
    "подписки": "💻 Подписки", "подписка": "💻 Подписки",
    "жилье": "🏠 Жилье", "быт": "🏠 Жилье", "жкх": "🏠 Жилье",
    "одежда": "👗 Гардероб", "гардероб": "👗 Гардероб", "обувь": "👗 Гардероб",
    "прочее": "💳 Прочее",
}

_RECEIPT_CLARIFY_SYSTEM = """Пользователь уточняет категории для позиций из чека.

Позиции в чеке (id → название → сумма):
{all_items}

Пользователь написал: "{user_text}"

Допустимые категории:
🐾 Коты, 🍜 Продукты, 🍱 Кафе/Доставка, 🚕 Транспорт, 🚬 Привычки,
💅 Бьюти, 🏥 Здоровье, 💻 Подписки, 🏠 Жилье, 👗 Гардероб, 💳 Прочее

Задача: определи какие позиции пользователь уточняет и какую категорию назначает.
Пользователь пишет сокращённо: "кб" = Красное&Белое, "озон" = OZON, "пиплбот" = ПиплБот.
Сопоставь по смыслу/сокращению. Пользователь может переопределить ЛЮБУЮ позицию.

ВАЖНО: в поле "id" верни ТОЧНЫЙ id из списка выше (число).

Верни JSON: {{"overrides": [{{"id": 0, "category": "🚬 Привычки"}}]}}
Только JSON, без пояснений. Если "нет"/"пропустить" → {{"overrides": []}}
"""


def _receipt_summary_lines(items: list[dict]) -> list[str]:
    """Сформировать текст распознанного чека с разделением доход/расход."""
    lines = ["📸 <b>Чек распознан:</b>"]
    for item in items:
        typ = item.get("type", "expense")
        sign = "+" if typ == "income" else ""
        marker = "❓" if item.get("need_clarify") else item.get("category", "💳")
        lines.append(f"  {marker} {item['name']} — {sign}{item['amount']}₽")
    expenses = sum(it["amount"] for it in items if it.get("type") != "income")
    income = sum(it["amount"] for it in items if it.get("type") == "income")
    totals = []
    if expenses:
        totals.append(f"Расходы: {expenses}₽")
    if income:
        totals.append(f"Доходы: +{income}₽")
    if totals:
        lines.append("\n" + " · ".join(totals))
    return lines


async def _handle_receipt_clarify(msg: Message, user_notion_id: str = "") -> bool:
    """Обработка текстового уточнения категорий фото-чека."""
    from core.list_manager import pending_get, pending_set, pending_del
    from core.vision import _VALID_EXPENSE_CATS

    uid = msg.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("action") != "receipt_clarify":
        return False

    text = (msg.text or "").strip()
    if not text:
        return False

    items = pending.get("items", [])
    is_bank = pending.get("is_bank", False)
    p_user_id = pending.get("user_notion_id", user_notion_id)

    skip_words = {"нет", "пропустить", "skip", "no", "—", "-"}
    if text.lower() in skip_words:
        for it in items:
            if it.get("need_clarify"):
                it["category"] = "💳 Прочее"
                it["need_clarify"] = False
    else:
        # Haiku парсит уточнение — по индексам позиций
        all_items_list = "\n".join(
            f"id={i} → {it['name']} → {it['amount']}₽ (текущая: {it.get('category', '?')}, need_clarify={it.get('need_clarify', False)})"
            for i, it in enumerate(items)
        )
        system = _RECEIPT_CLARIFY_SYSTEM.format(
            all_items=all_items_list,
            user_text=text,
        )
        applied = False
        try:
            from core.claude_client import ask_claude
            import json as _json
            raw = await ask_claude(
                prompt=f"Уточнение категорий: {text}",
                system=system,
                max_tokens=256,
            )
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = _json.loads(raw)
            overrides = parsed.get("overrides", [])

            for ov in overrides:
                idx = ov.get("id")
                ov_cat = ov.get("category", "")
                if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(items):
                    continue
                if ov_cat not in _VALID_EXPENSE_CATS:
                    continue
                items[idx]["category"] = ov_cat
                items[idx]["need_clarify"] = False
                applied = True
        except Exception as e:
            logger.warning("receipt clarify Haiku error: %s, trying local", e)

        # Local fallback если Haiku не применил ничего
        if not applied:
            text_lower = text.lower()
            # Собрать все слова-категории из текста
            found_cats = []
            for keyword, cat in _RECEIPT_CATS_MAP.items():
                if keyword in text_lower:
                    found_cats.append(cat)

            if found_cats:
                # Если одна категория и один ❓ — применить
                unclear = [i for i, it in enumerate(items) if it.get("need_clarify")]
                if len(unclear) == 1 and len(set(found_cats)) == 1:
                    items[unclear[0]]["category"] = found_cats[0]
                    items[unclear[0]]["need_clarify"] = False

        # Оставшиеся ❓ → Прочее
        for it in items:
            if it.get("need_clarify"):
                it["category"] = "💳 Прочее"
                it["need_clarify"] = False

    # Показать итог и спросить подтверждение
    pending_del(uid)
    pending_set(uid, {
        "action": "photo_receipt",
        "items": items,
        "is_bank": is_bank,
        "user_notion_id": p_user_id,
    })

    lines = _receipt_summary_lines(items)
    lines.append("Записать в финансы?")
    await msg.answer("\n".join(lines), parse_mode="HTML",
                     reply_markup=_receipt_confirm_kb(is_bank))
    return True


def _receipt_confirm_kb(is_bank: bool) -> InlineKeyboardMarkup:
    """Кнопки подтверждения. Банк → сразу Да/Нет, бумажный → Карта/Наличные/Нет."""
    if is_bank:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Записать", callback_data="receipt_card"),
            InlineKeyboardButton(text="Нет", callback_data="receipt_cancel"),
        ]])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Карта", callback_data="receipt_card"),
        InlineKeyboardButton(text="💵 Наличные", callback_data="receipt_cash"),
        InlineKeyboardButton(text="Нет", callback_data="receipt_cancel"),
    ]])


@dp.message(F.photo)
async def handle_photo(msg: Message, user_notion_id: str = "") -> None:
    """Фото → Vision → парсинг чека ИЛИ caption → текст."""
    from nexus.handlers.utils import react
    from core.vision import parse_receipt
    from core.list_manager import pending_set

    photo = msg.photo[-1]
    file = await msg.bot.get_file(photo.file_id)
    file_io = await msg.bot.download_file(file.file_path)
    content = file_io.read()

    await react(msg, "👀")

    result = await parse_receipt(content)

    if result and result.get("items"):
        items = result["items"]
        is_bank = result.get("source") == "bank_app"
        uid = msg.from_user.id

        # Показать чек
        lines = _receipt_summary_lines(items)

        # Есть ли айтемы, требующие уточнения?
        unclear = [it for it in items if it.get("need_clarify")]
        if unclear:
            # Текстовое уточнение
            unclear_desc = ", ".join(f"{it['name']} {it['amount']}₽" for it in unclear)
            lines.append(f"\nУточни непонятные (❓):\n<code>{unclear_desc}</code>")
            lines.append("\nНапиши категории (или «нет» чтобы записать как Прочее)")
            pending_set(uid, {
                "action": "receipt_clarify",
                "items": items,
                "is_bank": is_bank,
                "user_notion_id": user_notion_id,
            })
            await msg.answer("\n".join(lines), parse_mode="HTML")
        else:
            # Всё определено — сразу подтверждение
            lines.append("Записать в финансы?")
            pending_set(uid, {
                "action": "photo_receipt",
                "items": items,
                "is_bank": is_bank,
                "user_notion_id": user_notion_id,
            })
            await msg.answer("\n".join(lines), parse_mode="HTML",
                             reply_markup=_receipt_confirm_kb(is_bank))
    elif msg.caption:
        from core.layout import maybe_convert
        text = maybe_convert(msg.caption.strip())
        await process_text(msg, text, user_notion_id)
    else:
        await msg.answer("📸 Не смог распознать. Попробуй сфоткать ровнее или напиши текстом.")


@dp.callback_query(lambda c: c.data and c.data.startswith("receipt_"))
async def on_receipt(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Подтверждение записи фото-чека в финансы."""
    from core.list_manager import pending_get, pending_del
    from core.notion_client import finance_add
    from core.classifier import today_moscow

    uid = query.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("action") != "photo_receipt":
        await query.answer("⏰ Сессия истекла.")
        return

    action = query.data.replace("receipt_", "")
    if action == "cancel":
        pending_del(uid)
        try:
            await query.message.edit_reply_markup()
        except Exception:
            pass
        await query.answer("Отменено")
        return

    is_bank = pending.get("is_bank", False)
    source = "💳 Карта" if (action == "card" or is_bank) else "💵 Наличные"
    items = pending.get("items", [])
    p_user_id = pending.get("user_notion_id", user_notion_id)
    pending_del(uid)

    # Группировать по типу + категории
    by_cat: dict[str, dict] = {}
    for item in items:
        cat = item.get("category", "💳 Прочее")
        typ = item.get("type", "expense")
        key = f"{typ}:{cat}"
        if key not in by_cat:
            by_cat[key] = {"names": [], "total": 0, "type": typ, "cat": cat}
        by_cat[key]["names"].append(item["name"])
        by_cat[key]["total"] += item.get("amount", 0)

    lines = ["✅ <b>Записано:</b>"]
    for key, data in by_cat.items():
        desc = ", ".join(data["names"])
        fin_type = "💰 Доход" if data["type"] == "income" else "💸 Расход"
        await finance_add(
            date=today_moscow(),
            amount=float(data["total"]),
            category=data["cat"],
            type_=fin_type,
            source=source,
            description=desc,
            bot_label="☀️ Nexus",
            user_notion_id=p_user_id,
        )
        sign = "+" if data["type"] == "income" else ""
        lines.append(f"  {data['cat']}: {sign}{int(data['total'])}₽ ({desc})")

    # Раздельные итоги
    total_exp = sum(d["total"] for d in by_cat.values() if d["type"] == "expense")
    total_inc = sum(d["total"] for d in by_cat.values() if d["type"] == "income")
    totals = []
    if total_exp:
        totals.append(f"Расходы: {int(total_exp)}₽")
    if total_inc:
        totals.append(f"Доходы: +{int(total_inc)}₽")
    if totals:
        lines.append("\n" + " · ".join(totals) + f" · {source}")

    try:
        await query.message.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception:
        await query.message.answer("\n".join(lines), parse_mode="HTML")

    # Лимиты (только для расходов)
    for key, data in by_cat.items():
        if data["type"] == "expense":
            try:
                from nexus.handlers.finance import _check_budget_limit
                await _check_budget_limit(data["cat"], query.message, p_user_id, amount=data["total"])
            except Exception:
                pass

    await query.answer("👌 Записано!")


@dp.callback_query(lambda c: c.data and (c.data.startswith("opt_") or c.data.startswith("note_replace:")))
async def on_note_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
    from nexus.handlers.notes import handle_note_callback
    await handle_note_callback(query)


@dp.callback_query(lambda c: c.data and c.data.startswith("page:"))
async def on_page_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
    from core.pagination import handle_page_callback
    await handle_page_callback(query)


@dp.callback_query(lambda c: c.data and c.data.startswith("arcana_choice_"))
async def on_arcana_choice(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Handle: выбор между Аркана и Задача."""
    uid = query.from_user.id
    if uid not in _pending_arcana:
        await query.answer("⏱ Время истекло, попробуй снова")
        return

    text = _pending_arcana.pop(uid)
    parts = query.data.split("_")
    choice = parts[2]  # yes или no

    if choice == "yes":
        msg_text = (
            "🔮 <b>Это работа для Арканы!</b>\n\n"
            "Перейди в <a href=\"https://t.me/arcana_kailark_bot\">🌒 Arcana</a> и отправь туда:\n"
            f"<code>{text[:100]}</code>\n\n"
            "Там я помогу с ритуалами, практикой и сеансами."
        )
    else:
        from core.notion_client import task_add
        result = await task_add(title=text, category="💳 Прочее", priority="Важно",
                                user_notion_id=user_notion_id)
        if result:
            msg_text = f"✓ <b>{text}</b>\n🟡 Важно · 💳 Прочее"
        else:
            msg_text = "❌ Ошибка при создании задачи"

    await query.message.edit_text(msg_text)
    await query.answer("✅ Выбор принят")


@dp.callback_query(lambda c: c.data and c.data.startswith("fin_type_"))
async def on_finance_clarify(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Handle finance type clarification (expense/income/barter)."""
    from core.notion_client import finance_add
    from core.classifier import today_moscow

    uid = query.from_user.id
    if uid not in _pending_finance:
        await query.answer("⏱ Время истекло, попробуй снова")
        return

    parts = query.data.split("_")
    if len(parts) < 3:
        return

    fin_type = parts[2]  # expense, income, barter
    pending_entry = _pending_finance.pop(uid)
    # Support both old (2-tuple) and new (3-tuple with user_notion_id) formats
    if len(pending_entry) == 3:
        finance_data, original_text, stored_uid = pending_entry
    else:
        finance_data, original_text = pending_entry
        stored_uid = user_notion_id

    if fin_type == "expense":
        type_label = "💸 Расход"
        icon, sign = "💸", "−"
        source = finance_data["source"]
    elif fin_type == "income":
        type_label = "💰 Доход"
        icon, sign = "💰", "+"
        source = finance_data["source"]
    elif fin_type == "barter":
        type_label = "💸 Расход"
        icon, sign = "💸", "−"
        source = "🔄 Бартер"
    else:
        return

    result = await finance_add(
        date=today_moscow(),
        amount=finance_data["amount"],
        category=finance_data["category"],
        type_=type_label,
        source=source,
        description=finance_data["title"],
        user_notion_id=stored_uid or user_notion_id,
    )

    if result:
        text_msg = (
            f"{icon} <b>{sign}{finance_data['amount']:,.0f}₽</b> · "
            f"<b>{finance_data['title']}</b>\n"
            f"🏷 {finance_data['category']} <i>{source}</i>"
        )
    else:
        text_msg = "❌ Ошибка записи в Notion"

    await query.message.edit_text(text_msg)
    await query.answer("✅ Сохранено")


_UNKNOWN_TTL = 300  # 5 min


@dp.callback_query(lambda c: c.data and c.data.startswith("unk_"))
async def on_unknown_clarify(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Handle unknown text → user chose action type."""
    import time as _time

    uid = query.from_user.id
    pending = _pending_unknown.pop(uid, None)
    if not pending or _time.time() - pending[2] > _UNKNOWN_TTL:
        await query.answer("⏰ Время истекло, отправь текст ещё раз")
        return

    original_text, stored_uid, _ = pending
    notion_id = stored_uid or user_notion_id

    # Parse action: unk_buy_123, unk_task_123, unk_note_123, unk_mem_123
    action = query.data.split("_")[1]  # buy, task, note, mem

    if action == "buy":
        from nexus.handlers.lists import handle_list_buy
        fake_data = {"text": original_text}
        await handle_list_buy(query.message, fake_data, user_notion_id=notion_id)
        await query.answer("🛒 Добавляю в покупки")

    elif action == "task":
        from core.notion_client import task_add
        result = await task_add(title=original_text, category="💳 Прочее", priority="Важно",
                                user_notion_id=notion_id)
        if result:
            await query.message.edit_text(f"📋 <b>{original_text}</b>\n🟡 Важно · 💳 Прочее")
        else:
            await query.message.edit_text("❌ Ошибка при создании задачи")
        await query.answer("📋 Задача создана" if result else "❌ Ошибка")

    elif action == "note":
        from nexus.handlers.notes import handle_note
        await handle_note(query.message, original_text, config.nexus.db_notes,
                          user_notion_id=notion_id)
        await query.answer("📝 Создаю заметку")

    elif action == "mem":
        from nexus.handlers.memory import handle_memory_save
        fake_data = {"text": original_text}
        await handle_memory_save(query.message, fake_data, user_notion_id=notion_id)
        await query.answer("🧠 Сохраняю в память")


@dp.message()
async def handle_unauthorized(msg: Message) -> None:
    logger.warning(
        "Unauthorized: user_id=%s",
        msg.from_user.id if msg.from_user else "unknown",
    )


async def main() -> None:
    logger.info("Nexus bot starting...")
    from nexus.handlers.tasks import init_scheduler
    from aiogram.types import BotCommand

    await bot.set_my_commands([
        BotCommand(command="start", description="Приветствие"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="tasks", description="Задачи"),
        BotCommand(command="today", description="Задачи на сегодня"),
        BotCommand(command="stats", description="Статистика + стрики"),
        BotCommand(command="finance", description="Финансы (сегодня + месяц)"),
        BotCommand(command="budget", description="Бюджетный план"),
        BotCommand(command="list", description="Списки (покупки, чеклисты, инвентарь)"),
        BotCommand(command="notes", description="Заметки"),
        BotCommand(command="memory", description="Память"),
        BotCommand(command="adhd", description="СДВГ-профиль"),
    ])

    init_scheduler(bot)
    from nexus.handlers.tasks import restore_reminders_on_startup
    from nexus.handlers.notes import send_notes_digest_all
    from apscheduler.triggers.cron import CronTrigger
    from nexus.handlers.tasks import _scheduler as nexus_scheduler
    # ☀️ Утренний дайджест задач — ежедневно 07:00 UTC (10:00 МСК)
    from nexus.handlers.tasks import send_morning_digest
    if nexus_scheduler:
        nexus_scheduler.add_job(
            send_morning_digest,
            args=[bot],
            trigger=CronTrigger(hour=7, minute=0),
            id="daily_morning_digest",
            replace_existing=True,
        )
    # Дайджест заметок за неделю: каждое воскресенье в 09:00 UTC (12:00 МСК)
    if nexus_scheduler:
        nexus_scheduler.add_job(
            send_notes_digest_all,
            args=[bot],
            trigger=CronTrigger(day_of_week="sun", hour=9, minute=0),
            id="notes_reminder_weekly",
            replace_existing=True,
        )
        # 🗒️ Списки: клон повторяющихся покупок — ежедневно 00:00 UTC (03:00 СПб)
        from core.list_manager import clone_recurring, check_expiry
        nexus_scheduler.add_job(
            clone_recurring,
            trigger=CronTrigger(hour=0, minute=0),
            id="list_recurring",
            replace_existing=True,
        )
        # 🗒️ Списки: проверка сроков годности — ежедневно 07:05 UTC (10:05 СПб)
        nexus_scheduler.add_job(
            check_expiry,
            args=[bot, 3],
            trigger=CronTrigger(hour=7, minute=5),
            id="list_expiry",
            replace_existing=True,
        )
        # 💰 Бюджет: ревью в день зарплаты — ежедневно 07:30 UTC (10:30 МСК), внутри проверяет payday
        from nexus.handlers.finance import proactive_budget_review
        nexus_scheduler.add_job(
            proactive_budget_review,
            args=[bot],
            trigger=CronTrigger(hour=7, minute=30),
            id="budget_payday_review",
            replace_existing=True,
        )
    # Логируем все запланированные cron jobs
    if nexus_scheduler:
        cron_jobs = [(j.id, str(j.trigger), str(j.next_run_time)) for j in nexus_scheduler.get_jobs()]
        logger.info("Scheduled cron jobs (%d): %s", len(cron_jobs), cron_jobs)

    # restore_reminders планируем ПОСЛЕ старта polling,
    # иначе бот не может отправлять сообщения (missed reminders)
    import asyncio as _asyncio

    async def _on_startup(**kwargs) -> None:
        await restore_reminders_on_startup()

    dp.startup.register(_on_startup)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
