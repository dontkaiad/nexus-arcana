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

    @dp.message(F.photo)
    async def p(m: Message):
        f = await bot.get_file(m.photo[-1].file_id)
        b = await bot.download_file(f.file_path)
        await m.answer("🔮 Анализирую...")
        ans = await analyze_image("Это Таро. Дай трактовку.", b.read())
        await m.answer(ans[:4000])

    @dp.callback_query(lambda c: c.data and c.data.startswith("opt_"))
    async def on_opt_callback(query: CallbackQuery, user_notion_id: str = "") -> None:
        from nexus.handlers.notes import handle_note_callback
        await handle_note_callback(query)

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())