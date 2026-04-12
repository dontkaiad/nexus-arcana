"""Конфигурация E2E тестов."""
import os

# Telegram API (получить на my.telegram.org)
API_ID = int(os.environ.get("TG_API_ID", 0))
API_HASH = os.environ.get("TG_API_HASH", "")

# Боты
NEXUS_BOT = "@nexus_kailark_bot"
ARCANA_BOT = "@arcana_kailark_bot"

# Таймаут ожидания ответа (сек)
RESPONSE_TIMEOUT = 15
LONG_TIMEOUT = 30  # для Sonnet-запросов (budget, tarot)

# Пауза между тестами (сек) — чтобы не нагружать бота
DELAY_BETWEEN = 2
