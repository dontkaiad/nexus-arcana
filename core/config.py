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


def _int_or_zero(value: str) -> int:
    """Целое из env; пусто или мусор → 0 (функция выключена, без падения при старте)."""
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


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
    "ритуал", "практика", "таро", "гадание", "расклад", "раскладка", "крест", "карты",
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
    # ── Общая TG-группа логов (общий лог-бот). Всё опционально:
    # если token/chat пусты — отправка ошибок в группу выключена, бот
    # стартует и работает как раньше. Топики форума — per-bot.
    log_bot_token: str = ""
    log_chat_id: str = ""
    log_thread_nexus: str = ""
    log_thread_arcana: str = ""
    log_thread_booking: str = ""  # топик Zarya/Booking в лог-группе (#23)
    # ── Единый вход heylark.dev: cookie hl_session ставит login.heylark.dev,
    # nexus-arcana только проверяет её (см. miniapp/backend/auth.py) и
    # редиректит неавторизованных на LOGIN_BASE_URL/login?next=... ──
    session_secret: str = ""
    cookie_domain: str = ".heylark.dev"
    login_base_url: str = "https://login.heylark.dev"
    # ── Shared heylark auth DB (grants/people) — separate Postgres. Unset →
    # every non-owner resolves to guest (see core/auth_grants.py, #23). ──
    auth_database_url: str = ""
    # ── Service-to-service secret: the Zarya booking bot → Booking API
    # (`X-Booking-Service-Token`). Same value in the bot's env. #23. ──
    booking_service_token: str = ""
    # ── ⭐ Zarya booking bot (@heylark_booking_bot). Пусто → бот не стартует. ──
    booking_bot_token: str = ""
    booking_api_base: str = "http://nexus-bot:8000"
    # ── Служебное чтение карточек котов (GET /api/cats): приложение heylark_cats ходит с
    # `Authorization: Bearer <CATS_READ_TOKEN>` от имени владельца CATS_READ_OWNER_TG_ID (его tg_id должен быть
    # в ALLOWED_TELEGRAM_IDS). Пусто / короче 32 символов / владелец не задан → доступ по токену выключен. ──
    cats_read_token: str = ""
    cats_read_owner_tg_id: int = 0
    # Служебное чтение списков «для ситтера» по ОТДЕЛЬНОМУ Bearer-токену (не взаимозаменяем с CATS_READ_TOKEN).
    sitter_lists_token: str = ""
    sitter_lists_owner_tg_id: int = 0
    # Запись ситтера в расписание ухода (выполнить цикл / перенести): ОТДЕЛЬНЫЙ токен, не равный остальным.
    care_write_token: str = ""
    care_write_owner_tg_id: int = 0
    care_period_token: str = ""            # ОТДЕЛЬНЫЙ токен cats → Nexus: только PUT /api/care/period/auto
    # Здоровье котов (вес, визиты / прививки, курсы лекарств) для приложения cats от имени владельца: ОТДЕЛЬНЫЙ токен,
    # открывает только маршруты /api/cat-health/*. Приложение само проверяет владельца по своему initData, токен в браузер не попадает.
    cats_write_token: str = ""
    cats_write_owner_tg_id: int = 0
    care_period_owner_tg_id: int = 0
    # Чтение для своего MCP-сервера (kai-mcp, Claude): ОТДЕЛЬНЫЙ токен, только GET /api/tasks. Пусто / короче 32 /
    # равен другому служебному токену / владелец не в ALLOWED_TELEGRAM_IDS → доступ по токену выключен.
    mcp_read_token: str = ""
    mcp_read_owner_tg_id: int = 0
    # Запись для MCP (ADR-0039): ОТДЕЛЬНЫЙ токен, только POST /api/mcp/tasks и /api/mcp/tasks/{id}/reschedule. Не равен
    # ни одному другому служебному токену, включая MCP_READ_TOKEN. Владелец обязан быть в ALLOWED_TELEGRAM_IDS.
    mcp_write_token: str = ""
    mcp_write_owner_tg_id: int = 0
    # Создание личной практики из MCP (ADR-0040): ОТДЕЛЬНЫЙ токен, только POST /api/mcp/practice. Не равен ни одному другому
    # служебному токену, включая MCP_READ_TOKEN и MCP_WRITE_TOKEN. Владелец обязан быть в ALLOWED_TELEGRAM_IDS.
    mcp_practice_token: str = ""
    mcp_practice_owner_tg_id: int = 0
    log_thread_kaimcp: str = ""            # топик лог-группы для действий Claude (создал / перенёс задачу); пусто → топик Nexus
    # Пуши «у кого сегодня ДР» (core.birthdays): ОДИН получатель — личный tg_id Кай, без fan-out. Пусто → пуши выключены.
    birthday_push_tg_id: int = 0
    # Свободный текст о Кай для Zarya-промпта (#228). НИКОГДА не хардкодить
    # реальные факты в коде — репо публичный. Живёт только в .env на сервере,
    # правится напрямую там; пусто → Заря просто без доп. контекста о Кай.
    zarya_kai_context: str = ""


def load_config() -> AppConfig:
    return AppConfig(
        allowed_ids      = _id_list("ALLOWED_TELEGRAM_IDS"),
        notion_token     = os.getenv("NOTION_TOKEN"),  # scripts: notion_client read-adapter
        anthropic_key    = _require("ANTHROPIC_API_KEY"),
        db_lists         = _optional("NOTION_DB_LISTS"),
        openai_key       = _optional("OPENAI_API_KEY"),
        miniapp_base_url = _optional("MINIAPP_BASE_URL", "https://core.heylark.dev"),
        log_bot_token    = _optional("TG_LOG_BOT_TOKEN"),
        log_chat_id      = _optional("TG_LOG_CHAT_ID"),
        log_thread_nexus = _optional("TG_LOG_THREAD_NEXUS"),
        log_thread_arcana = _optional("TG_LOG_THREAD_ARCANA"),
        log_thread_booking = _optional("TG_LOG_THREAD_BOOKING"),
        session_secret       = _optional("SESSION_SECRET"),
        cookie_domain        = _optional("COOKIE_DOMAIN", ".heylark.dev"),
        login_base_url       = _optional("LOGIN_BASE_URL", "https://login.heylark.dev"),
        auth_database_url    = _optional("AUTH_DATABASE_URL"),
        booking_service_token = _optional("BOOKING_SERVICE_TOKEN"),
        cats_read_token      = _optional("CATS_READ_TOKEN").strip(),
        cats_read_owner_tg_id = _int_or_zero(_optional("CATS_READ_OWNER_TG_ID")),
        sitter_lists_token   = _optional("SITTER_LISTS_TOKEN").strip(),
        sitter_lists_owner_tg_id = _int_or_zero(_optional("SITTER_LISTS_OWNER_TG_ID")),
        care_write_token     = _optional("CARE_WRITE_TOKEN").strip(),
        care_write_owner_tg_id = _int_or_zero(_optional("CARE_WRITE_OWNER_TG_ID")),
        care_period_token    = _optional("CARE_PERIOD_TOKEN").strip(),
        cats_write_token     = _optional("CATS_WRITE_TOKEN").strip(),
        cats_write_owner_tg_id = _int_or_zero(_optional("CATS_WRITE_OWNER_TG_ID")),
        mcp_read_token       = _optional("MCP_READ_TOKEN").strip(),
        mcp_read_owner_tg_id = _int_or_zero(_optional("MCP_READ_OWNER_TG_ID")),
        mcp_write_token      = _optional("MCP_WRITE_TOKEN").strip(),
        mcp_write_owner_tg_id = _int_or_zero(_optional("MCP_WRITE_OWNER_TG_ID")),
        mcp_practice_token   = _optional("MCP_PRACTICE_TOKEN").strip(),
        mcp_practice_owner_tg_id = _int_or_zero(_optional("MCP_PRACTICE_OWNER_TG_ID")),
        log_thread_kaimcp    = _optional("TG_LOG_THREAD_KAIMCP"),
        care_period_owner_tg_id = _int_or_zero(_optional("CARE_PERIOD_OWNER_TG_ID")),
        birthday_push_tg_id  = _int_or_zero(_optional("BIRTHDAY_PUSH_TG_ID")),
        booking_bot_token    = _optional("BOOKING_BOT_TOKEN"),
        booking_api_base     = _optional("BOOKING_API_BASE", "http://nexus-bot:8000"),
        zarya_kai_context    = _optional("ZARYA_KAI_CONTEXT"),
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