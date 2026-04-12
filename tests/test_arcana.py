"""E2E тесты 🌒 Arcana."""
import asyncio
from e2e_runner import BotTester
from e2e_config import ARCANA_BOT, DELAY_BETWEEN, LONG_TIMEOUT

async def test_arcana():
    t = BotTester()
    await t.start()
    bot = ARCANA_BOT

    print("\n" + "="*50)
    print("🌒 ARCANA E2E ТЕСТЫ")
    print("="*50)

    # ═══════════════════════════════════════
    # БЛОК 1: КОМАНДЫ
    # ═══════════════════════════════════════
    print("\n📌 КОМАНДЫ")

    r = await t.send_command(bot, "/start")
    t.check("/start", r, must_contain=["Arcana"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/help")
    t.check("/help", r, must_contain=["клиент", "расклад"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/stats")
    t.check("/stats", r)
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/finance")
    t.check("/finance", r, must_contain=["₽"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/list")
    t.check("/list", r)
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/grimoire")
    t.check("/grimoire", r, must_contain=["гримуар"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/tz")
    t.check("/tz", r, must_contain=["часов", "UTC", "пояс"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 2: CRM КЛИЕНТЫ
    # ═══════════════════════════════════════
    print("\n📌 CRM КЛИЕНТЫ")

    # Создание клиента
    r = await t.send_and_wait(bot, "клиент Тест_E2E, женщина, 30 лет")
    t.check("Создание клиента", r,
            must_contain=["клиент", "Тест_E2E"],
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Повторный поиск — не дублировать
    r = await t.send_and_wait(bot, "клиент Тест_E2E")
    t.check("Find-or-create (не дублирует)", r,
            must_contain=["Тест_E2E"],
            must_not_contain=["создан"])  # не "создан новый", а "найден"
    await asyncio.sleep(DELAY_BETWEEN)

    # Досье клиента
    r = await t.send_and_wait(bot, "что у Тест_E2E?")
    t.check("Досье клиента", r, must_contain=["Тест_E2E"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Долги
    r = await t.send_and_wait(bot, "сколько мне должны?")
    t.check("Сводка долгов", r, must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 3: РАСКЛАДЫ (ТЕКСТ)
    # ═══════════════════════════════════════
    print("\n📌 РАСКЛАДЫ (текст)")

    # Простой расклад — три карты
    r = await t.send_and_wait(bot,
        "три карты, уэйт, отношения — туз мечей, жрица, десятка пентаклей",
        timeout=LONG_TIMEOUT)
    t.check("Расклад текстом (простой)", r,
            must_contain=["BUTTONS"],  # должны быть кнопки ✅/✏️/❌
            must_not_contain=["ошибк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Сохранить расклад
    r = await t.click_button(bot, "✅")
    t.check("Сохранение расклада", r,
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Расклад с клиентом и оплатой
    r = await t.send_and_wait(bot,
        "Тест_E2E, кельтский крест, dark wood, карьера, 1р карта — "
        "шут, маг, жрица, императрица, император, иерофант, "
        "влюбленные, колесница, сила, отшельник",
        timeout=LONG_TIMEOUT)
    t.check("Расклад с клиентом + оплата", r,
            must_contain=["BUTTONS"],
            must_not_contain=["ошибк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Сохранить
    r = await t.click_button(bot, "✅")
    t.check("Сохранение расклада с клиентом", r,
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Расклад с правкой
    r = await t.send_and_wait(bot,
        "триплет, ленорман — всадник, клевер, корабль",
        timeout=LONG_TIMEOUT)
    t.check("Расклад для правки", r, must_contain=["BUTTONS"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Нажать ✏️ (редактировать)
    r = await t.click_button(bot, "✏️")
    t.check("Кнопка редактирования", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # Отправить правку
    r = await t.send_and_wait(bot,
        "это для Тест_E2E, область финансы, 1р наличные",
        timeout=LONG_TIMEOUT)
    t.check("Правка расклада текстом", r,
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Если есть кнопка сохранения — нажать
    r2 = await t.click_button(bot, "✅")
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 4: РИТУАЛЫ
    # ═══════════════════════════════════════
    print("\n📌 РИТУАЛЫ")

    r = await t.send_and_wait(bot,
        "ритуал: тестовый ритуал e2e, очищение, дома, свечи белые 3шт")
    t.check("Создание ритуала", r,
            must_contain=["ритуал"],
            must_not_contain=["ошибк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Ритуал для клиента с оплатой
    r = await t.send_and_wait(bot,
        "ритуал для Тест_E2E: защита, дома, 1р карта")
    t.check("Ритуал для клиента", r,
            must_contain=["ритуал"],
            must_not_contain=["ошибк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 5: РАБОТЫ (CRUD)
    # ═══════════════════════════════════════
    print("\n📌 РАБОТЫ")

    r = await t.send_and_wait(bot, "работа: тестовая работа e2e расклад")
    t.check("Создание работы", r,
            must_contain=["работа", "создан"],
            must_not_contain=["ошибк"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Закрытие работы
    r = await t.send_and_wait(bot, "сделала тестовая работа e2e")
    t.check("Закрытие работы", r, must_contain=["✅"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 6: СТАТИСТИКА
    # ═══════════════════════════════════════
    print("\n📌 СТАТИСТИКА")

    # Верификация расклада
    r = await t.send_and_wait(bot, "Тест_E2E — сбылось")
    t.check("Верификация расклада", r,
            must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # /stats
    r = await t.send_command(bot, "/stats")
    t.check("/stats с данными", r, must_contain=["%"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 7: ПАМЯТЬ
    # ═══════════════════════════════════════
    print("\n📌 ПАМЯТЬ")

    r = await t.send_and_wait(bot, "запомни: тест arcana e2e память 99999")
    t.check("Сохранение в память", r, must_contain=["запомн", "память"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_and_wait(bot, "что я помню о тест arcana e2e")
    t.check("Поиск в памяти", r)
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_and_wait(bot, "забудь тест arcana e2e память 99999")
    t.check("Забыть", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 8: ГРИМУАР
    # ═══════════════════════════════════════
    print("\n📌 ГРИМУАР")

    # Запись в гримуар
    r = await t.send_and_wait(bot,
        "запиши в гримуар: тестовый заговор e2e на деньги — "
        "читать на растущую луну три раза")
    t.check("Запись в гримуар", r, must_contain=["гримуар"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Поиск в гримуаре
    r = await t.send_command(bot, "/grimoire")
    t.check("/grimoire", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 9: СПИСКИ (расходники)
    # ═══════════════════════════════════════
    print("\n📌 СПИСКИ")

    r = await t.send_and_wait(bot, "купить ладан тестовый e2e")
    t.check("Добавить расходник", r, must_contain=["список", "покупк"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_and_wait(bot, "инвентарь: свечи тестовые e2e 10шт")
    t.check("Инвентарь добавить", r, must_contain=["инвентар"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_and_wait(bot, "есть ли свечи тестовые e2e")
    t.check("Инвентарь поиск", r)
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 10: ФИНАНСЫ ПРАКТИКИ
    # ═══════════════════════════════════════
    print("\n📌 ФИНАНСЫ ПРАКТИКИ")

    r = await t.send_and_wait(bot, "потратила 1р расходники тест e2e")
    t.check("Расход расходники", r, must_contain=["₽"])
    await asyncio.sleep(DELAY_BETWEEN)

    r = await t.send_command(bot, "/finance")
    t.check("/finance с данными", r, must_contain=["₽"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # БЛОК 11: EDGE CASES
    # ═══════════════════════════════════════
    print("\n📌 EDGE CASES")

    # EN раскладка
    r = await t.send_and_wait(bot, "rkbtyn Ntcn_E2E")  # "клиент Тест_E2E"
    t.check("EN→RU раскладка", r, must_not_contain=["ошибк", "error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # Неизвестная команда
    r = await t.send_and_wait(bot, "абракадабра хтонь 12345")
    t.check("Неизвестный ввод", r, must_not_contain=["Traceback", "Error"])
    await asyncio.sleep(DELAY_BETWEEN)

    # ═══════════════════════════════════════
    # ОТЧЁТ
    # ═══════════════════════════════════════
    t.report()
    await t.stop()

if __name__ == "__main__":
    asyncio.run(test_arcana())
