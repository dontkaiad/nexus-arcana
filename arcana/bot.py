import asyncio, logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from core.config import config
from core.middleware import WhitelistMiddleware
from core.claude_client import analyze_image
from arcana.handlers.base import router
from arcana.handlers.memory import router as memory_router
from arcana.handlers.lists import router as lists_router
from arcana.handlers.sessions import router as sessions_router
from arcana.handlers.grimoire import router as grimoire_router
from arcana.handlers.delete import router as delete_router
from arcana.handlers.clients import router as clients_router
from arcana.handlers.payment import router as payment_router
from arcana.handlers.intent_resolve import router as intent_resolve_router
from arcana.handlers.work_kb import router as work_kb_router

logger = logging.getLogger("arcana.bot")

_photo_pending: dict = {}  # uid → (message_id, user_notion_id, ts)
_PHOTO_TTL = 120  # 2 минуты

# Глобальная инстанция планировщика напоминаний для Arcana (см. core/reminder_scheduler.py).
# Инициализируется в main() после старта APScheduler.
from core.reminder_scheduler import ReminderScheduler
arcana_reminder_flow = ReminderScheduler(callback_prefix="work")


def create_dp_and_bot():
    """Создать dp + bot без запуска polling. Для тестов и main()."""
    bot = Bot(token=config.arcana.tg_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.callback_query.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.include_router(sessions_router)   # callbacks tarot_save/edit/cancel — ПЕРВЫМ
    dp.include_router(payment_router)    # pay_*/barter_* callbacks
    dp.include_router(intent_resolve_router)  # intent_planned/intent_done callbacks
    dp.include_router(work_kb_router)         # work_dl/work_rm callbacks
    dp.include_router(grimoire_router)   # callbacks grim_* — до base router
    dp.include_router(delete_router)     # callbacks del_confirm/del_cancel
    dp.include_router(clients_router)    # callbacks create_client
    dp.include_router(router)
    dp.include_router(memory_router)
    dp.include_router(lists_router)

    from aiogram.filters import Command as ArcanaCommand
    from arcana.handlers.lists import handle_list_command as arcana_list_cmd
    from arcana.handlers.works import handle_works_list as arcana_works_list
    from arcana.handlers.stats import handle_stats, get_unverified_count

    @dp.message(ArcanaCommand("list"))
    async def cmd_list(msg: Message, user_notion_id: str = "") -> None:
        await arcana_list_cmd(msg, user_notion_id=user_notion_id)

    @dp.message(ArcanaCommand("works"))
    async def cmd_works(msg: Message, user_notion_id: str = "") -> None:
        await arcana_works_list(msg, user_notion_id)

    @dp.message(ArcanaCommand("stats"))
    async def cmd_stats(msg: Message, user_notion_id: str = "") -> None:
        await handle_stats(msg, user_notion_id)

    @dp.message(ArcanaCommand("finance"))
    async def cmd_finance(msg: Message, user_notion_id: str = "") -> None:
        from arcana.handlers.finance import handle_arcana_finance
        await handle_arcana_finance(msg, user_notion_id)

    @dp.message(ArcanaCommand("grimoire"))
    async def cmd_grimoire(msg: Message, user_notion_id: str = "") -> None:
        from arcana.handlers.grimoire import handle_grimoire_menu
        await handle_grimoire_menu(msg, user_notion_id)

    @dp.message(F.voice | F.audio)
    async def handle_voice(msg: Message, user_notion_id: str = "") -> None:
        """Голосовое → Whisper → текст → base router."""
        from core.voice import transcribe

        if msg.voice:
            file = await bot.get_file(msg.voice.file_id)
        else:
            file = await bot.get_file(msg.audio.file_id)

        file_io = await bot.download_file(file.file_path)
        content = file_io.read()

        try:
            await msg.react([{"type": "emoji", "emoji": "👂"}])
        except Exception:
            pass

        text = await transcribe(content)
        if text is None:
            await msg.answer("🎤 Голосовые не настроены (OPENAI_API_KEY).")
            return
        if not text:
            await msg.answer("🎤 Не удалось распознать голосовое.")
            return

        await msg.answer(f"🎤 <i>«{text}»</i>", parse_mode="HTML")

        # Lists pending
        from arcana.handlers.lists import handle_list_pending
        if await handle_list_pending(msg, user_notion_id):
            return

        # Pending: режим сбора инфы о клиенте
        from arcana.pending_clients import get_pending_client
        pending_client = await get_pending_client(msg.from_user.id)
        if pending_client and pending_client.get("step") == "collecting":
            from arcana.handlers.clients import _handle_collecting
            await _handle_collecting(msg, text, pending_client, user_notion_id)
            return

        # Pending: правка трактовки таро
        from arcana.pending_tarot import get_pending
        pending = await get_pending(msg.from_user.id)
        if pending and pending.get("awaiting_edit"):
            from arcana.handlers.base import _handle_tarot_correction
            await _handle_tarot_correction(msg, text, pending, user_notion_id)
            return

        # Полный pipeline — передаём текст явно (msg заморожен)
        from arcana.handlers.base import route_message
        await route_message(msg, user_notion_id=user_notion_id, _text=text)

    @dp.message(F.photo)
    async def handle_photo(msg: Message, user_notion_id: str = "") -> None:
        """Фото: collecting → скрин контакта. С подписью → route_message. Без → спросить."""
        from arcana.pending_clients import get_pending_client
        pending_client = await get_pending_client(msg.from_user.id)
        if pending_client and pending_client.get("step") == "collecting":
            import base64
            f = await bot.get_file(msg.photo[-1].file_id)
            bio = await bot.download_file(f.file_path)
            image_b64 = base64.standard_b64encode(bio.read()).decode()
            from arcana.handlers.clients import handle_client_photo_input
            await handle_client_photo_input(msg, image_b64, pending_client)
            return

        if msg.caption:
            from arcana.handlers.base import route_message
            await route_message(msg, user_notion_id=user_notion_id, _text=msg.caption)
            return

        # Фото без подписи и без контекста — спросить что это
        uid = msg.from_user.id
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import time as _t
        # Сохраняем message_id фото для последующей обработки
        _photo_pending[uid] = (msg.message_id, user_notion_id, _t.time())
        from core.utils import cancel_button
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🃏 Расклад",          callback_data=f"photo_tarot:{uid}"),
            InlineKeyboardButton(text="👤 Контакт клиента",  callback_data=f"photo_client:{uid}"),
            cancel_button("❌ Отмена", f"photo_cancel:{uid}"),
        ]])
        await msg.reply("Что это за фото?", reply_markup=kb)

    @dp.message(F.contact)
    async def handle_contact(msg: Message, user_notion_id: str = "") -> None:
        """TG контакт (share contact) → дополнить карточку если есть collecting."""
        from arcana.pending_clients import get_pending_client, update_pending_client

        pending = await get_pending_client(msg.from_user.id)
        if not pending or pending.get("step") != "collecting":
            await msg.answer("🤔 Не жду контакт. Скажи «создай клиента Имя» сначала.")
            return

        uid = msg.from_user.id
        phone = msg.contact.phone_number or ""
        tg_user_id = msg.contact.user_id
        contact_name = f"{msg.contact.first_name or ''} {msg.contact.last_name or ''}".strip()

        value = phone
        if tg_user_id:
            value = f"{phone} (TG: {tg_user_id})" if phone else f"TG: {tg_user_id}"
        label = contact_name or ""

        await update_pending_client(uid, {"contacts": [{"value": value, "label": label}]})

        from arcana.pending_clients import get_pending_client as _get
        fresh = await _get(uid) or pending

        page_id = fresh.get("page_id")
        if page_id:
            from arcana.handlers.clients import _update_notion
            await _update_notion(page_id, fresh)

        from arcana.handlers.clients import _collecting_kb, _card
        await msg.answer(
            f"📱 Контакт добавлен\n\n{_card(fresh)}\n\nДобавь ещё или нажми Готово.",
            reply_markup=_collecting_kb(uid),
            parse_mode="HTML",
        )

    @dp.callback_query(lambda c: c.data and c.data.startswith("opt_"))
    async def on_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
        from nexus.handlers.notes import handle_note_callback
        await handle_note_callback(query)

    @dp.callback_query(lambda c: c.data and c.data.startswith("photo_"))
    async def on_photo_choice(query: CallbackQuery, user_notion_id: str = "") -> None:
        import time as _t, base64
        uid = query.from_user.id
        pending = _photo_pending.pop(uid, None)

        if not pending or _t.time() - pending[2] > _PHOTO_TTL:
            await query.answer("⏰ Время истекло")
            await query.message.edit_text("⏰ Время истекло — отправь фото ещё раз.")
            return

        msg_id, notion_id, _ = pending
        action = query.data.split(":")[0]  # photo_tarot / photo_client / photo_cancel

        if action == "photo_cancel":
            await query.answer("Отменено")
            await query.message.edit_text("❌ Фото проигнорировано.")
            return

        # Скачиваем оригинальное фото по reply / forward нет — ищем в chat history
        # В aiogram нет прямого доступа к сообщению по id без хранения,
        # поэтому используем фото из сообщения с кнопками (предыдущее)
        # reply_to_message если бот ответил на фото
        photo_msg = query.message.reply_to_message
        if not photo_msg or not photo_msg.photo:
            await query.answer("❌ Не могу найти фото")
            await query.message.edit_text("⚠️ Не нашла фото. Отправь ещё раз.")
            return

        f = await bot.get_file(photo_msg.photo[-1].file_id)
        bio = await bot.download_file(f.file_path)
        image_b64 = base64.standard_b64encode(bio.read()).decode()

        if action == "photo_tarot":
            await query.answer("🃏 Распознаю расклад")
            await query.message.edit_text("🔍 Распознаю карты...")
            from arcana.handlers.sessions import handle_tarot_photo
            # Передаём управление в tarot через photo_msg (там есть photo)
            await handle_tarot_photo(photo_msg, notion_id or user_notion_id)

        elif action == "photo_client":
            await query.answer("👤 Извлекаю контакт")
            from arcana.pending_clients import get_pending_client
            pending_client = await get_pending_client(uid)
            if not pending_client:
                await query.message.edit_text(
                    "👤 Для какого клиента этот контакт? Напиши «клиент Имя» сначала."
                )
                return
            from arcana.handlers.clients import handle_client_photo_input
            await query.message.edit_text("📸 Извлекаю контакты...")
            await handle_client_photo_input(photo_msg, image_b64, pending_client)

    return dp, bot


async def main():
    if not config.arcana.tg_token: return
    logging.basicConfig(level=logging.INFO)
    from core.logging_notion import install as _install_notion_logging
    _install_notion_logging(bot_label="🌒 Arcana")

    dp, bot = create_dp_and_bot()

    # ── Команды в меню Telegram ──────────────────────────────────────────────
    from aiogram.types import BotCommand, MenuButtonCommands
    try:
        await bot.set_my_commands([
            BotCommand(command="start",    description="🌒 Начало работы"),
            BotCommand(command="help",     description="📖 Справка по командам"),
            BotCommand(command="list",     description="🗒️ Списки расходников"),
            BotCommand(command="finance",  description="💰 Финансы практики"),
            BotCommand(command="stats",    description="📊 Статистика раскладов"),
            BotCommand(command="grimoire", description="📖 Гримуар"),
            BotCommand(command="tz",       description="🕐 Часовой пояс"),
        ])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logger.warning("set_my_commands/menu failed: %s", e)

    # ── Ежемесячный cron-напоминалка ─────────────────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from arcana.handlers.stats import get_unverified_count

        scheduler = AsyncIOScheduler()

        async def monthly_unverified_reminder() -> None:
            """1-го числа каждого месяца в 12:00 — напомнить про непроверенные расклады."""
            from core.config import config as cfg
            from core.user_manager import get_user

            for tg_id in cfg.allowed_ids:
                try:
                    user_data = await get_user(tg_id)
                    if not user_data:
                        continue
                    if not user_data.get("permissions", {}).get("arcana", False):
                        continue
                    notion_id = user_data.get("notion_page_id", "")
                    count = await get_unverified_count(notion_id, older_than_days=30)
                    if count > 0:
                        await bot.send_message(
                            tg_id,
                            f"🌒 <b>Напоминание</b>\n\n"
                            f"У тебя {count} непроверенных раскладов старше 30 дней.\n"
                            f"Напиши «Анна 5 марта — сбылось» чтобы отметить результат.\n"
                            f"Или /stats для общей статистики.",
                            parse_mode="HTML",
                        )
                except Exception as e:
                    logger.warning("monthly_reminder error for %s: %s", tg_id, e)

        scheduler.add_job(
            monthly_unverified_reminder,
            CronTrigger(day=1, hour=12, minute=0),
            id="arcana_monthly_reminder",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler started — arcana monthly reminder active")
        # Инициализируем shared ReminderScheduler — теперь cb_work_remind
        # сможет ставить APScheduler-job на дедлайн напоминания работы.
        arcana_reminder_flow.init(bot, scheduler)
        logger.info("arcana_reminder_flow ready (callback_prefix=work)")
    except ImportError:
        logger.warning("apscheduler not installed — monthly reminder disabled")

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
