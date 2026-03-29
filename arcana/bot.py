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

async def main():
    if not config.arcana.tg_token: return
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=config.arcana.tg_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.callback_query.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.include_router(sessions_router)   # callbacks tarot_save/edit/cancel — ПЕРВЫМ
    dp.include_router(router)
    dp.include_router(memory_router)
    dp.include_router(lists_router)

    from aiogram.filters import Command as ArcanaCommand
    from arcana.handlers.lists import handle_list_command as arcana_list_cmd
    from arcana.handlers.works import handle_works_list as arcana_works_list

    @dp.message(ArcanaCommand("list"))
    async def cmd_list(msg: Message, user_notion_id: str = "") -> None:
        await arcana_list_cmd(msg, user_notion_id=user_notion_id)

    @dp.message(ArcanaCommand("works"))
    async def cmd_works(msg: Message, user_notion_id: str = "") -> None:
        await arcana_works_list(msg, user_notion_id)

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

        # Передать в base router как текст — имитируем текстовое сообщение
        # Base router использует message.text, поэтому просто отвечаем подсказкой
        await msg.answer("🌒 Распознано. Отправь этот текст сообщением для обработки.")

    @dp.message(F.photo)
    async def handle_photo(msg: Message, user_notion_id: str = "") -> None:
        """Фото расклада → Vision → справочник → трактовка → pending + кнопки."""
        from arcana.handlers.sessions import handle_tarot_photo
        await handle_tarot_photo(msg, user_notion_id)

    @dp.callback_query(lambda c: c.data and c.data.startswith("opt_"))
    async def on_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
        from nexus.handlers.notes import handle_note_callback
        await handle_note_callback(query)

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
