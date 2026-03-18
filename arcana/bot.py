import asyncio, logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from core.config import config
from core.middleware import WhitelistMiddleware
from core.claude_client import analyze_image
from arcana.handlers.base import router

async def main():
    if not config.arcana.tg_token: return
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=config.arcana.tg_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(WhitelistMiddleware())
    dp.include_router(router)

    @dp.message(F.photo)
    async def p(m: Message):
        f = await bot.get_file(m.photo[-1].file_id)
        b = await bot.download_file(f.file_path)
        await m.answer("🔮 Анализирую...")
        ans = await analyze_image("Это Таро. Дай трактовку.", b.read())
        await m.answer(ans[:4000])

    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())