"""nexus_bot.py — Telegram-бот NEXUS. Claude — единственный роутер."""
from __future__ import annotations

import logging
import traceback as tb
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

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
dp.include_router(tasks_router)
dp.include_router(finance_router)
dp.include_router(memory_router)

MOSCOW_TZ = timezone(timedelta(hours=3))
_clarify: dict = {}
_pending_finance: dict = {}  # user_id → (kind, amount, category, source, title)
_pending_arcana: dict = {}  # user_id → text (оригинальный для arcana_clarify)


@dp.message(Command("start"))
async def cmd_start(msg: Message, user_notion_id: str = "") -> None:
    await msg.answer(
        "☀️ <b>Nexus запущен!</b>\n\n"
        "<b>Что это?</b>\n"
        "Твой личный AI-ассистент для оптимизации рутины и хаоса. "
        "Просто пиши как есть — я разберусь.\n\n"

        "<b>Что я умею:</b>\n"
        "💰 Финансы (расходы, доходы, статистика)\n"
        "✅ Задачи (с дедлайнами и напоминаниями)\n"
        "💡 Заметки (с тегами и категориями)\n"
        "🧠 Память (запомню факты о твоей жизни)\n"
        "🔮 Редирект в 🌒 Arcana (для ритуалов и практик)\n\n"

        "Напиши <code>/help</code> для полного гайда 📋\n\n"

        "<b>Создатель:</b> Кай Ларк\n"
        "❓ Ошибки/вопросы? <a href=\"https://t.me/witchcommit\">@witchcommit</a>"
    )


@dp.message(Command("help"))
async def cmd_help(msg: Message, user_notion_id: str = "") -> None:
    await msg.answer(
        "<b>ГАЙД ☀️ NEXUS</b>\n"
        "<i>Понимаю естественный язык — команды учить не нужно, просто пиши.</i>\n\n"

        "✅ <b>ЗАДАЧИ</b>\n"
        "Напиши текст — создам задачу автоматически.\n"
        "Дедлайн: «до пятницы», «сдать до 10 апреля».\n"
        "Напоминание: «напомни в 10», «напомни через 2 часа».\n"
        "Повторяющиеся: «каждую неделю», «ежедневно».\n"
        "В уведомлениях: ✅ выполнено · ❌ отмена · ⏳ отложить.\n"
        "  <code>/tasks</code> — все активные задачи\n\n"

        "💰 <b>ФИНАНСЫ</b>\n"
        "Расход: «450р такси», «кофе 180», «монстр 120».\n"
        "Доход: «получил 50к», «пришла зарплата», «аренда 35000».\n"
        "Лимит: «лимит на кафе 5000р» — предупрежу при 80% и 100%.\n"
        "Исправить: «измени категорию на продукты», «поменяй карту на нал».\n"
        "  <code>/finance</code> — расходы за сегодня\n"
        "  <code>/finance_stats</code> — сводка месяца с балансом и ревью\n\n"

        "💡 <b>ЗАМЕТКИ</b>\n"
        "Создать: «заметка: ...», «идея: ...», «запомни: ...», «рецепт: ...».\n"
        "Теги подбирает сам из существующих; новые — с подтверждением.\n"
        "  <code>/notes</code> — последние заметки\n"
        "  <code>/notes_digest</code> — дайджест старых\n\n"

        "🧠 <b>ПАМЯТЬ</b>\n"
        "Запоминаю факты: люди, животные, здоровье, предпочтения, паттерны.\n"
        "Сохранить: «запомни что маша не ест мясо», «у меня аллергия на пыль».\n"
        "Найти: «что знаешь о маше», «напомни про батона».\n"
        "Деактивация и удаление — прямо из результатов поиска.\n\n"

        "🌍 <b>ЧАСОВОЙ ПОЯС</b>\n"
        "«я в москве», «utc+5» — или явно:\n"
        "  <code>/tz UTC+3</code> — установить часовой пояс\n\n"

        "📊 <b>СТАТИСТИКА</b>\n"
        "«сколько потратил на кафе» — итог по категории + лимит.\n"
        "«расходы на еду за 3 месяца» — разбивка по месяцам + среднее.\n"
        "«сравни месяцы» — текущий vs предыдущий по категориям.\n"
        "«все доходы», «сколько заработал на практике» — и по доходам тоже.\n"
        "  <code>/finance_stats</code> — полная сводка месяца\n\n"

        "👨‍💻 <b>Создатель:</b> <a href=\"https://github.com/dontkaiad\">Кай Ларк</a>\n"
        "❓ Ошибки/вопросы? <a href=\"https://t.me/witchcommit\">@witchcommit</a>",
        parse_mode="HTML",
    )


@dp.message(Command("tasks"))
async def cmd_tasks(msg: Message, user_notion_id: str = "") -> None:
    """Показать активные задачи из Notion с пагинацией если > PAGE_SIZE."""
    from core.notion_client import tasks_active
    from core.pagination import PAGE_SIZE, register_pages, get_page_text, get_page_keyboard
    tasks = await tasks_active(user_notion_id=user_notion_id)
    if not tasks:
        await msg.answer("📭 Активных задач нет.")
        return
    _icons = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "⚪"}
    task_items = []
    for t in tasks:
        props = t["properties"]
        title_parts = props.get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "Низкий")
        deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")[:10]
        repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "")
        repeat_mark = " 🔄" if repeat and repeat != "Нет" else ""
        task_items.append({
            "icon": _icons.get(priority, "⚪"),
            "title": title + repeat_mark,
            "deadline": deadline,
        })

    def _task_fmt(it: dict) -> str:
        line = f"{it['icon']} {it['title']}"
        if it.get("deadline"):
            line += f" · {it['deadline']}"
        return line

    if len(task_items) > PAGE_SIZE:
        uid = msg.from_user.id
        register_pages(uid, task_items, "📋 Активные задачи", _task_fmt)
        await msg.answer(get_page_text(uid), reply_markup=get_page_keyboard(uid))
    else:
        await msg.answer("📋 <b>Активные задачи:</b>\n\n" + "\n".join(_task_fmt(t) for t in task_items))


@dp.message(Command("notes"))
async def cmd_notes(msg: Message, user_notion_id: str = "") -> None:
    """Показать последние 5 заметок из Notion."""
    from core.notion_client import db_query
    pages = await db_query(
        config.nexus.db_notes,
        sorts=[{"property": "Дата", "direction": "descending"}],
        page_size=5,
    )
    if not pages:
        await msg.answer("📭 Заметок нет.")
        return
    lines = []
    for p in pages:
        props = p["properties"]
        title_parts = props.get("Заголовок", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        tags_items = props.get("Теги", {}).get("multi_select", [])
        tags_str = " ".join(f"#{t['name']}" for t in tags_items)
        date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
        line = f"💡 {title}"
        if tags_str:
            line += f" {tags_str}"
        if date:
            line += f" · {date}"
        lines.append(line)
    await msg.answer("📝 <b>Последние заметки:</b>\n\n" + "\n".join(lines))


@dp.message(Command("finance"))
async def cmd_finance(msg: Message, user_notion_id: str = "") -> None:
    """Показать расходы за сегодня + итого."""
    from core.notion_client import finance_month
    from core.classifier import today_moscow
    today = today_moscow()
    month = today[:7]
    records = await finance_month(month, user_notion_id=user_notion_id)
    lines = []
    total = 0.0
    for r in records:
        props = r["properties"]
        date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
        if date != today:
            continue
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        if "Расход" not in type_name:
            continue
        desc_parts = props.get("Описание", {}).get("title", [])
        desc = desc_parts[0]["plain_text"] if desc_parts else "—"
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        lines.append(f"  💸 {desc} · {cat} · {amount:,.0f}₽")
        total += amount
    if not lines:
        await msg.answer(f"💸 Расходов за {today} нет.")
        return
    text = f"💸 <b>Расходы за {today}:</b>\n" + "\n".join(lines) + f"\n\n💰 Итого: <b>{total:,.0f}₽</b>"
    await msg.answer(text)


@dp.message(Command("finance_stats"))
async def cmd_finance_stats(msg: Message, user_notion_id: str = "") -> None:
    """Финансовая сводка за текущий месяц с бюджетными лимитами."""
    from nexus.handlers.finance import get_finance_stats
    month = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m")
    text = await get_finance_stats(month, user_notion_id)
    await msg.answer(text)


@dp.message(Command("notes_digest"))
async def cmd_notes_digest(msg: Message, user_notion_id: str = "") -> None:
    """Ручной запуск дайджеста заметок."""
    from nexus.handlers.notes import send_notes_digest
    await send_notes_digest(bot, msg.from_user.id, user_notion_id)


@dp.message(Command("tz"))
async def set_tz(msg: Message, user_notion_id: str = "") -> None:
    """Установить часовой пояс. /tz UTC+5 или /tz Екатеринбург"""
    from nexus.handlers.tasks import _update_user_tz
    await _update_user_tz(msg, msg.text.replace("/tz", "").strip())


@dp.message(F.text)
async def handle_text(msg: Message, user_notion_id: str = "") -> None:
    from core.layout import maybe_convert
    from nexus.handlers.tasks import _pending_has, _pending_get, handle_task_clarification, handle_reschedule_reminder, _update_user_tz

    if _pending_has(msg.from_user.id):
        pending = _pending_get(msg.from_user.id)
        if pending and pending.get("action") == "reschedule":
            await handle_reschedule_reminder(msg)
            return
        await handle_task_clarification(msg)
        return

    text = maybe_convert(msg.text.strip())
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
                return
        except Exception:
            pass
        logged = await log_error(combined, "unknown_type", "", error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        await msg.answer(f"🌒 Так и не понял · {notion_status}")
        return

    try:
        items = await classify(original_text, tz_offset=tz_offset)
        logger.info("handle_text: classify returned %d items: %s", len(items), [i.get("type") for i in items])

        lines = []
        has_clarify = False
        finance_data = None
        arcana_clarify_text = None

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
            elif line:
                lines.append(line)

        # Show UI if arcana clarify needed
        if arcana_clarify_text:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
        # Show UI if low confidence finance
        elif has_clarify and finance_data:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
        else:
            if len(lines) == 1:
                await msg.answer(lines[0])
            elif len(lines) > 1:
                body = "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))
                await msg.answer(f"Записано {len(lines)} операций:\n\n{body}")

    except Exception as e:
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
        result = await task_add(title=text, category="💳 Прочее", priority="Средний",
                                user_notion_id=user_notion_id)
        if result:
            msg_text = f"✓ <b>{text}</b>\n🟡 Средний · 💳 Прочее"
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
        BotCommand(command="start", description="Запустить Nexus"),
        BotCommand(command="help", description="Гайд по использованию"),
        BotCommand(command="tasks", description="Активные задачи"),
        BotCommand(command="notes", description="Последние 5 заметок"),
        BotCommand(command="finance", description="Расходы за сегодня"),
        BotCommand(command="finance_stats", description="Сводка за месяц + лимиты"),
        BotCommand(command="notes_digest", description="Дайджест старых заметок"),
    ])

    init_scheduler(bot)
    from nexus.handlers.tasks import restore_reminders_on_startup
    from nexus.handlers.notes import send_notes_digest_all
    from apscheduler.triggers.cron import CronTrigger
    from nexus.handlers.tasks import _scheduler as nexus_scheduler
    # Еженедельный дайджест заметок: каждое воскресенье в 07:00 UTC (12:00 UTC+5)
    if nexus_scheduler:
        nexus_scheduler.add_job(
            send_notes_digest_all,
            args=[bot],
            trigger=CronTrigger(day_of_week="sun", hour=7, minute=0),
            id="notes_digest_weekly",
            replace_existing=True,
        )
    await restore_reminders_on_startup()
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
