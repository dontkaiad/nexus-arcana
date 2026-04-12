"""E2E тест-раннер для Telegram ботов."""
import asyncio
import time
from datetime import datetime
from telethon import TelegramClient, events
from e2e_config import API_ID, API_HASH, RESPONSE_TIMEOUT, LONG_TIMEOUT, DELAY_BETWEEN

class BotTester:
    def __init__(self):
        self.client = TelegramClient('e2e_session', API_ID, API_HASH)
        self.results = []
        self.last_response = None
        self.response_event = asyncio.Event()

    async def start(self):
        await self.client.start()
        print(f"✅ Подключено как: {(await self.client.get_me()).first_name}")

    async def stop(self):
        await self.client.disconnect()

    async def send_and_wait(self, bot: str, message: str,
                            timeout: int = RESPONSE_TIMEOUT,
                            expect_callback: bool = False) -> str:
        """Отправить сообщение боту и дождаться ответа."""
        self.last_response = None
        self.response_event.clear()

        # Слушаем ответ
        @self.client.on(events.NewMessage(from_users=bot))
        async def handler(event):
            self.last_response = event.message.text or ""
            # Если есть inline кнопки — добавить их текст
            if event.message.buttons:
                buttons_text = []
                for row in event.message.buttons:
                    for btn in row:
                        buttons_text.append(btn.text)
                self.last_response += "\n[BUTTONS: " + " | ".join(buttons_text) + "]"
            self.response_event.set()

        await self.client.send_message(bot, message)

        try:
            await asyncio.wait_for(self.response_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.last_response = "[TIMEOUT]"

        self.client.remove_event_handler(handler)
        return self.last_response or "[EMPTY]"

    async def send_command(self, bot: str, command: str,
                          timeout: int = RESPONSE_TIMEOUT) -> str:
        """Отправить команду (/start, /help и т.д.)."""
        return await self.send_and_wait(bot, command, timeout)

    async def click_button(self, bot: str, button_text: str,
                          timeout: int = RESPONSE_TIMEOUT) -> str:
        """Нажать inline-кнопку по тексту."""
        self.last_response = None
        self.response_event.clear()

        @self.client.on(events.MessageEdited(from_users=bot))
        async def edit_handler(event):
            self.last_response = event.message.text or ""
            self.response_event.set()

        @self.client.on(events.NewMessage(from_users=bot))
        async def new_handler(event):
            self.last_response = event.message.text or ""
            self.response_event.set()

        # Найти последнее сообщение с кнопками
        async for msg in self.client.iter_messages(bot, limit=5):
            if msg.buttons:
                for row in msg.buttons:
                    for btn in row:
                        if button_text.lower() in btn.text.lower():
                            await btn.click()
                            try:
                                await asyncio.wait_for(
                                    self.response_event.wait(), timeout=timeout
                                )
                            except asyncio.TimeoutError:
                                self.last_response = "[TIMEOUT after click]"
                            self.client.remove_event_handler(edit_handler)
                            self.client.remove_event_handler(new_handler)
                            return self.last_response or "[EMPTY]"

        self.client.remove_event_handler(edit_handler)
        self.client.remove_event_handler(new_handler)
        return "[BUTTON NOT FOUND: " + button_text + "]"

    def check(self, test_name: str, response: str,
              must_contain: list = None, must_not_contain: list = None):
        """Проверить ответ."""
        passed = True
        details = []

        if response == "[TIMEOUT]":
            passed = False
            details.append("⏰ Таймаут — бот не ответил")

        if must_contain:
            for keyword in must_contain:
                if keyword.lower() not in response.lower():
                    passed = False
                    details.append(f"❌ Нет '{keyword}'")

        if must_not_contain:
            for keyword in must_not_contain:
                if keyword.lower() in response.lower():
                    passed = False
                    details.append(f"⚠️ Содержит '{keyword}' (не должно)")

        status = "✅ PASS" if passed else "❌ FAIL"
        self.results.append((test_name, passed, details))

        # Вывод сразу
        print(f"  {status}  {test_name}")
        if not passed:
            for d in details:
                print(f"         {d}")
            if len(response) < 500:
                print(f"         Ответ: {response[:200]}")

        return passed

    def report(self):
        """Итоговый отчёт."""
        total = len(self.results)
        passed = sum(1 for _, p, _ in self.results if p)
        failed = total - passed

        print("\n" + "="*50)
        print(f"📊 ИТОГО: {passed}/{total} passed, {failed} failed")
        print("="*50)

        if failed > 0:
            print("\n❌ ПРОВАЛЕНЫ:")
            for name, p, details in self.results:
                if not p:
                    print(f"  • {name}")
                    for d in details:
                        print(f"    {d}")

        return failed == 0
