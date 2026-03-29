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

logger = logging.getLogger("arcana.bot")

async def main():
    if not config.arcana.tg_token: return
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=config.arcana.tg_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.callback_query.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.include_router(sessions_router)   # callbacks tarot_save/edit/cancel — ПЕРВЫМ
    dp.include_router(grimoire_router)   # callbacks grim_* — до base router
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

    # ── Ежемесячный cron-напоминалка ─────────────────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

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
    except ImportError:
        logger.warning("apscheduler not installed — monthly reminder disabled")

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

        # Pending: правка трактовки таро
        from arcana.pending_tarot import get_pending
        pending = await get_pending(msg.from_user.id)
        if pending and pending.get("awaiting_edit"):
            from arcana.handlers.base import _handle_tarot_correction
            msg.text = text
            await _handle_tarot_correction(msg, text, pending, user_notion_id)
            return

        # Полный pipeline — подменяем text и роутим
        msg.text = text
        from arcana.handlers.base import route_message
        await route_message(msg, user_notion_id=user_notion_id)

    @dp.message(F.photo)
    async def handle_photo(msg: Message, user_notion_id: str = "") -> None:
        """Фото с подписью → route_message. Фото без подписи → таро (Vision)."""
        if msg.caption:
            # Подпись есть — роутим как текст (может быть чек, расклад с именем и т.п.)
            msg.text = msg.caption
            from arcana.handlers.base import route_message
            await route_message(msg, user_notion_id=user_notion_id)
            return
        # Без подписи → таро
        from arcana.handlers.sessions import handle_tarot_photo
        await handle_tarot_photo(msg, user_notion_id)

    @dp.callback_query(lambda c: c.data and c.data.startswith("opt_"))
    async def on_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
        from nexus.handlers.notes import handle_note_callback
        await handle_note_callback(query)

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
