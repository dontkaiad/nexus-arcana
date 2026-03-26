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

async def main():
    if not config.arcana.tg_token: return
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=config.arcana.tg_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.callback_query.middleware(WhitelistMiddleware(require_feature="arcana"))
    dp.include_router(router)
    dp.include_router(memory_router)
    dp.include_router(lists_router)

    from aiogram.filters import Command as ArcanaCommand
    from arcana.handlers.lists import handle_list_command as arcana_list_cmd

    @dp.message(ArcanaCommand("list"))
    async def cmd_list(msg: Message, user_notion_id: str = "") -> None:
        await arcana_list_cmd(msg, user_notion_id=user_notion_id)

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
        """Фото: если есть caption → base router, иначе → трактовка таро."""
        if msg.caption:
            # Caption → передать как подсказку
            await msg.answer(f"📸 Подпись: <i>«{msg.caption}»</i>\nОтправь текстом для обработки.", parse_mode="HTML")
            return

        # Без подписи → таро (как раньше)
        f = await bot.get_file(msg.photo[-1].file_id)
        b = await bot.download_file(f.file_path)
        await msg.answer("🔮 Анализирую...")
        ans = await analyze_image("Это Таро. Дай трактовку.", b.read())
        await msg.answer(ans[:4000])

    @dp.callback_query(lambda c: c.data and c.data.startswith("opt_"))
    async def on_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
        from nexus.handlers.notes import handle_note_callback
        await handle_note_callback(query)

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
