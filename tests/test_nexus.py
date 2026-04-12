"""E2E тесты ☀️ Nexus."""
import asyncio
from e2e_runner import BotTester
from e2e_config import NEXUS_BOT, DELAY_BETWEEN, LONG_TIMEOUT

async def test_nexus():
    t = BotTester()
    await t.start()
    bot = NEXUS_BOT

    print("\n" + "="*50)
    print("☀️ NEXUS E2E ТЕСТЫ")
    print("="*50)

    # ═══════════════════════════════════════
    # БЛОК 1: КОМАНДЫ
    # ═══════════════════════════════════════
    print("\n📌 КОМАНДЫ")

    r = await t.send_command(bot, "/start")
    t.check("/start", r, must_contain=["Nexus", "ассистент"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/help")
    t.check("/help", r, must_contain=["задач", "финанс"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/today")
    t.check("/today", r, must_contain=["сегодня"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/tasks")
    t.check("/tasks", r)  # любой ответ = ок
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/stats")
    t.check("/stats", r, must_contain=["статистик"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/finance")
    t.check("/finance", r, must_contain=["₽"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/budget")
    t.check("/budget", r, timeout=LONG_TIMEOUT)
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/list")
    t.check("/list", r, must_contain=["BUTTONS"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/memory")
    t.check("/memory", r)
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/adhd")
    t.check("/adhd", r, timeout=LONG_TIMEOUT, must_contain=["СДВГ", "🦋"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/notes")
    t.check("/notes", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 2: ЗАДАЧИ
    # ═══════════════════════════════════════
    print("\n📌 ЗАДАЧИ")

    # Создание задачи
    r = await t.send_and_wait(bot, "задача тестовая задача e2e")
    t.check("Создание задачи", r, must_contain=["задача", "создан"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Задача с дедлайном
    r = await t.send_and_wait(bot, "задача тестовый дедлайн завтра напомни за час")
    t.check("Задача с дедлайном", r, must_contain=["задача"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Закрытие задачи текстом
    r = await t.send_and_wait(bot, "сделала тестовая задача e2e")
    t.check("Закрытие задачи текстом", r,
            must_contain=["✅"],
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Повторяющаяся задача
    r = await t.send_and_wait(bot, "задача тест повтор каждый день в 23:59")
    t.check("Повторяющаяся задача", r, must_contain=["задача"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Интервальная задача
    r = await t.send_and_wait(bot, "задача тест интервал каждые 3 дня в 23:58")
    t.check("Интервальная задача", r, must_contain=["задача", "3"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Отмена задачи
    r = await t.send_and_wait(bot, "отмени задачу тест повтор")
    t.check("Отмена задачи", r, must_contain=["отмен", "архив"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Отмена интервальной
    r = await t.send_and_wait(bot, "отмени тест интервал")
    t.check("Отмена интервальной", r, must_contain=["отмен", "архив"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 3: ФИНАНСЫ
    # ═══════════════════════════════════════
    print("\n📌 ФИНАНСЫ")

    # Расход
    r = await t.send_and_wait(bot, "потратила 1р тест e2e продукты")
    t.check("Расход", r, must_contain=["₽", "продукт"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Доход
    r = await t.send_and_wait(bot, "доход 1р тест")
    t.check("Доход", r, must_contain=["₽", "доход"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Лимит
    r = await t.send_and_wait(bot, "лимит на кафе 5000")
    t.check("Установка лимита", r, must_contain=["лимит"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 4: СПИСКИ
    # ═══════════════════════════════════════
    print("\n📌 СПИСКИ")

    # Добавить в покупки
    r = await t.send_and_wait(bot, "купить тестовый айтем e2e")
    t.check("Добавить в покупки", r, must_contain=["список", "покупк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Инвентарь добавить
    r = await t.send_and_wait(bot, "инвентарь: тестовый предмет 5шт")
    t.check("Инвентарь добавить", r, must_contain=["инвентар"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Инвентарь поиск
    r = await t.send_and_wait(bot, "есть ли тестовый предмет")
    t.check("Инвентарь поиск", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 5: ЗАМЕТКИ И ПАМЯТЬ
    # ═══════════════════════════════════════
    print("\n📌 ЗАМЕТКИ И ПАМЯТЬ")

    # Заметка
    r = await t.send_and_wait(bot, "заметка: тестовая заметка e2e 12345")
    t.check("Создание заметки", r, must_contain=["заметк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Запомнить
    r = await t.send_and_wait(bot, "запомни: тест e2e память 67890")
    t.check("Сохранение в память", r, must_contain=["запомн", "память"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Поиск в памяти
    r = await t.send_and_wait(bot, "что я помню о тест e2e")
    t.check("Поиск в памяти", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # Забыть
    r = await t.send_and_wait(bot, "забудь тест e2e память 67890")
    t.check("Забыть", r, must_contain=["деактивирован", "забыт", "удален"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 6: EDGE CASES
    # ═══════════════════════════════════════
    print("\n📌 EDGE CASES")

    # Опечатка / EN раскладка
    r = await t.send_and_wait(bot, "pflfxf ntcn hfcrkflrb")  # "задача тест раскладки"
    t.check("EN→RU раскладка", r, must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Пустое сообщение (пробелы)
    r = await t.send_and_wait(bot, "   ")
    t.check("Пробелы", r, must_not_contain=["Traceback", "Error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Очень длинное сообщение
    r = await t.send_and_wait(bot, "задача " + "тест " * 200)
    t.check("Длинное сообщение", r, must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Отмена тестовых данных
    r = await t.send_and_wait(bot, "отмени тестовый дедлайн")
    await asyncio.sleep(DELAY_BETWEEN)
    r = await t.send_and_wait(bot, "отмени тест раскладки")
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 7: TIMEZONE
    # ═══════════════════════════════════════
    print("\n📌 TIMEZONE")

    r = await t.send_command(bot, "/tz")
    t.check("/tz показ", r, must_contain=["часов", "UTC", "пояс"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # ОТЧЁТ
    # ═══════════════════════════════════════
    t.report()
    await t.stop()

if __name__ == "__main__":
    asyncio.run(test_nexus())
