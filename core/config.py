"""`core/config.py` — загрузка настроек из .env"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

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
    seen: set[int] = set()
    out: List[int] = []
    for x in raw.split(","):
        s = x.strip()
        if not s:
            continue
        v = int(s)
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


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
# Nexus personal finance — расходы и доходы раздельно (используются miniapp /categories API).
EXPENSE_CATEGORIES = [
    "🐾 Коты", "🏠 Жильё", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба", "💳 Прочее",
]

INCOME_CATEGORIES = [
    "💰 Зарплата", "💼 Фриланс", "🎁 Подарок",
    "💵 Возврат/кэшбэк", "💱 Продажа", "💳 Прочее",
]

# Arcana-domain categories — не показываются юзеру в Nexus-фильтрах.
ARCANA_CATEGORIES = ["🔮 Практика", "🕯️ Расходники"]

# Полная вселенная для LLM-парсера: EXPENSE + INCOME + ARCANA, без дублей.
# «💳 Прочее» есть в EXPENSE и INCOME — дедуплицируется.
_seen: set = set()
_all: list = []
for _cat in EXPENSE_CATEGORIES + INCOME_CATEGORIES + ARCANA_CATEGORIES:
    if _cat not in _seen:
        _seen.add(_cat)
        _all.append(_cat)
FINANCE_CATEGORIES: list = _all
del _seen, _all, _cat

FINANCE_SOURCES = ["💳 Карта", "💵 Наличные", "🔄 Бартер"]
FINANCE_TYPES   = ["💰 Доход", "💸 Расход"]

# ── Claude models ──────────────────────────────────────────────────────────────
MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"


# Notion-removal: оставлены только db-id, которые ещё читают рантайм (как
# backward-compat db_id для PG-репо) или backfill/migration-скрипты в scripts/.
# Мёртвые NOTION_DB_* (memory/errors/page_reports/clients/rituals/stats/
# notes-arcana/finance-arcana/grimoire/users) удалены — 0 читателей.
@dataclass
class NexusConfig:
    tg_token: str
    db_finance: str   # runtime: _save_finance backward-compat db_id
    db_tasks: str     # runtime: tasks_repo.create backward-compat db_id
    db_notes: str     # runtime: handle_note backward-compat db_id


@dataclass
class ArcanaConfig:
    tg_token: str
    db_sessions: str  # scripts: migrate/normalize source db
    db_tasks: str     # runtime: arcana lists fallback db_id
    db_works: str = ""  # runtime: arcana lists db_id


@dataclass
class AppConfig:
    allowed_ids: List[int]
    notion_token: Optional[str]
    anthropic_key: str
    nexus: NexusConfig
    arcana: ArcanaConfig
    db_lists: str = ""  # scripts: backfill_lists source db
    finance_categories: List[str] = field(default_factory=lambda: FINANCE_CATEGORIES)
    finance_sources: List[str]    = field(default_factory=lambda: FINANCE_SOURCES)
    finance_types: List[str]      = field(default_factory=lambda: FINANCE_TYPES)
    model_haiku: str  = MODEL_HAIKU
    model_sonnet: str = MODEL_SONNET
    openai_key: str = ""
    miniapp_base_url: str = "https://core.heylark.dev"


def load_config() -> AppConfig:
    return AppConfig(
        allowed_ids      = _id_list("ALLOWED_TELEGRAM_IDS"),
        notion_token     = os.getenv("NOTION_TOKEN"),  # scripts: notion_client read-adapter
        anthropic_key    = _require("ANTHROPIC_API_KEY"),
        db_lists         = _optional("NOTION_DB_LISTS"),
        openai_key       = _optional("OPENAI_API_KEY"),
        miniapp_base_url = _optional("MINIAPP_BASE_URL", "https://core.heylark.dev"),
        nexus = NexusConfig(
            tg_token     = _require("NEXUS_BOT_TOKEN"),
            db_finance   = _optional("NOTION_DB_FINANCE"),
            db_tasks     = _optional("NOTION_DB_TASKS"),
            db_notes     = _optional("NOTION_DB_NOTES"),
        ),
        arcana = ArcanaConfig(
            tg_token    = _optional("ARCANA_BOT_TOKEN"),
            db_sessions = _optional("NOTION_DB_SESSIONS"),
            db_tasks    = _optional("NOTION_DB_ARCANA_TASKS"),
            db_works    = _optional("NOTION_DB_WORKS"),
        ),
    )


# Синглтон
config: AppConfig = load_config()