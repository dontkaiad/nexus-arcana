"""`core/config.py` — загрузка настроек из .env"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _id_list(key: str) -> List[int]:
    raw = _require(key)
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


# ── Arcana redirect keywords ───────────────────────────────────────────────────────
ARCANA_KEYWORDS = {
    # Материалы для ритуалов
    "расходники", "свечи", "масло", "травы", "камни", "благовоние",
    # Магия
    "ритуал", "практика", "таро", "гадание", "раскладка", "крест", "карты",
    # Работа с клиентами
    "клиент", "сеанс", "сессия", "сеанса", "гримуар",
    # Практики
    "ароматерапия", "медитация", "мантра", "аффирмация",
}


# ── Finance constants ──────────────────────────────────────────────────────────
FINANCE_CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "🕯️ Расходники", "📚 Хобби/Учеба",
    "💰 Зарплата", "🔮 Практика", "💳 Прочее",
]

FINANCE_SOURCES = ["💳 Карта", "💵 Наличные", "🔄 Бартер"]
FINANCE_TYPES   = ["💰 Доход", "💸 Расход"]

# ── Claude models ──────────────────────────────────────────────────────────────
MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"


@dataclass
class NexusConfig:
    tg_token: str
    db_finance: str
    db_tasks: str
    db_memory: str
    db_notes: str
    db_errors: str
    page_reports: str = ""  # Родительская страница для отчётов (опционально)


@dataclass
class ArcanaConfig:
    tg_token: str
    db_clients: str
    db_sessions: str
    db_rituals: str
    db_tasks: str
    db_stats: str
    db_notes: str
    db_finance: str


@dataclass
class AppConfig:
    allowed_ids: List[int]
    notion_token: str
    anthropic_key: str
    nexus: NexusConfig
    arcana: ArcanaConfig
    db_users: str = ""
    db_lists: str = ""
    finance_categories: List[str] = field(default_factory=lambda: FINANCE_CATEGORIES)
    finance_sources: List[str]    = field(default_factory=lambda: FINANCE_SOURCES)
    finance_types: List[str]      = field(default_factory=lambda: FINANCE_TYPES)
    model_haiku: str  = MODEL_HAIKU
    model_sonnet: str = MODEL_SONNET


def load_config() -> AppConfig:
    return AppConfig(
        allowed_ids   = _id_list("ALLOWED_TELEGRAM_IDS"),
        notion_token  = _require("NOTION_TOKEN"),
        anthropic_key = _require("ANTHROPIC_API_KEY"),
        db_users      = _optional("NOTION_DB_USERS"),
        db_lists      = _optional("NOTION_DB_LISTS"),
        nexus = NexusConfig(
            tg_token     = _require("NEXUS_BOT_TOKEN"),
            db_finance   = _require("NOTION_DB_FINANCE"),
            db_tasks     = _require("NOTION_DB_TASKS"),
            db_memory    = _require("NOTION_DB_MEMORY"),
            db_notes     = _require("NOTION_DB_NOTES"),
            db_errors    = _optional("NOTION_DB_ERRORS"),
            page_reports = _optional("NOTION_PAGE_REPORTS"),
        ),
        arcana = ArcanaConfig(
            tg_token    = _optional("ARCANA_BOT_TOKEN"),
            db_clients  = _optional("NOTION_DB_CLIENTS"),
            db_sessions = _optional("NOTION_DB_SESSIONS"),
            db_rituals  = _optional("NOTION_DB_RITUALS"),
            db_tasks    = _optional("NOTION_DB_ARCANA_TASKS"),
            db_stats    = _optional("NOTION_DB_STATS"),
            db_notes    = _optional("NOTION_DB_NOTES"),
            db_finance  = _optional("NOTION_DB_FINANCE"),
        ),
    )


# Синглтон
config: AppConfig = load_config()