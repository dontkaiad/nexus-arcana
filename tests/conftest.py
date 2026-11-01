"""Общие фикстуры для тестов."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import os
import sys

# Генерация ответа по памяти (этап 4) в обычных тестах выключена: без сети и без реальных вызовов LLM.
# Тесты генерации включают её явно (monkeypatch.setenv) и мокают ask_claude.
os.environ.setdefault("MEMORY_GENERATE_ANSWER", "0")

# Добавить корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Мок .env до импорта config — ПРИНУДИТЕЛЬНО перезаписываем чтобы не зависеть от .env файла
_TEST_ENV = {
    "NEXUS_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "ARCANA_BOT_TOKEN": "987654321:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "ALLOWED_TELEGRAM_IDS": "700000001",
    "ANTHROPIC_API_KEY": "fake-key",
    "CLAUDE_HAIKU": "claude-haiku-4-5-20251001",
    "CLAUDE_SONNET": "claude-sonnet-4-20250514",
    "NOTION_TOKEN": "fake-token",
    "NOTION_DB_TASKS": "fake-db-id",
    "NOTION_DB_FINANCE": "fake-db-id",
    "NOTION_DB_MEMORY": "fake-db-id",
    "NOTION_DB_NOTES": "fake-db-id",
    "NOTION_DB_LISTS": "fake-db-id",
    "NOTION_DB_ERRORS": "fake-db-id",
    "NOTION_DB_USERS": "fake-db-id",
    "NOTION_DB_CLIENTS": "fake-db-id",
    "NOTION_DB_SESSIONS": "fake-db-id",
    "NOTION_DB_RITUALS": "fake-db-id",
    "NOTION_DB_WORKS": "fake-db-id",
    "NOTION_DB_GRIMOIRE": "fake-db-id",
    "OPENAI_API_KEY": "fake-key",
    "LOG_LEVEL": "WARNING",
    "LOG_FILE": "/dev/null",
}
for k, v in _TEST_ENV.items():
    if k not in os.environ or not os.environ[k]:
        os.environ[k] = v


# ── Исключить E2E-раннеры (Telethon, живые боты) из pytest collection ────────
collect_ignore = ["e2e"]


# Приложение (pydantic-модели с полями date/datetime) импортируем ДО любой заморозки: иначе модель, впервые
# собранная под freezegun, видит подменённый datetime.date и падает «Unable to generate pydantic-core schema».
try:  # noqa: SIM105
    import miniapp.backend.app  # noqa: F401
    from pydantic import BaseModel as _BM

    # Модели с отложенными аннотациями (`from __future__ import annotations`) достраиваются лениво — достроим сейчас,
    # пока `date`/`datetime` в модулях настоящие (под freezegun имя `date` в модуле — это FakeDate).
    _seen, _stack = set(), [_BM]
    while _stack:
        for _sub in _stack.pop().__subclasses__():
            if _sub not in _seen:
                _seen.add(_sub)
                _stack.append(_sub)
                try:
                    _sub.model_rebuild()
                except Exception:
                    pass
    # FastAPI строит TypeAdapter поля тела/параметров лениво, при ПЕРВОМ запросе. Под freezegun `datetime.date`
    # подменён, и pydantic не узнаёт свой же тип date. Поэтому «прогреваем» адаптеры заранее, на настоящих часах.
    from miniapp.backend.app import app as _app

    def _warm(field):
        try:
            if field is not None:
                field._type_adapter  # noqa: B018  (cached_property)
        except Exception:
            pass

    try:
        from fastapi._compat import get_cached_model_fields as _gcmf
    except Exception:  # pragma: no cover
        _gcmf = None

    for _r in getattr(_app, "routes", []):
        _dep = getattr(_r, "dependant", None)
        _bf = getattr(_r, "body_field", None)
        _bt = getattr(_bf, "type_", None)
        if _gcmf and isinstance(_bt, type) and issubclass(_bt, _BM):
            try:
                _gcmf(_bt)          # поля модели тела строятся лениво при первом запросе (кеш lru) — греем заранее
            except Exception:
                pass
        _warm(_bf)
        _warm(getattr(_r, "response_field", None))
        if _dep is not None:
            for _group in (_dep.path_params, _dep.query_params, _dep.header_params, _dep.cookie_params, _dep.body_params):
                for _f in _group:
                    _warm(_f)
except Exception:  # pragma: no cover — отдельные наборы тестов могут жить без зависимостей Mini App
    pass


# ── Часы тестов ──────────────────────────────────────────────────────────────
# Тесты не должны зависеть от даты запуска (раньше CI краснел 1-го числа: периоды бюджета, «сегодня», календарь).
# Все тесты идут на ЗАМОРОЖЕННЫХ часах (freezegun, tick=True: время идёт вперёд от точки старта, так что таймауты,
# TTL и сортировки по времени работают). Дата по умолчанию — середина месяца, будний день, 12:00 МСК.
# TEST_NOW="2026-12-31 09:00:00" — прогнать набор на другой дате (UTC); TEST_REAL_CLOCK=1 — настоящие часы;
# @pytest.mark.real_clock — настоящие часы для конкретного теста.
DEFAULT_TEST_NOW = "2026-11-18 09:00:00"          # среда, 12:00 МСК


def clock_base():
    """Точка старта тестовых часов (aware UTC): TEST_NOW / дата по умолчанию, либо настоящее время при TEST_REAL_CLOCK=1.
    Для констант уровня модуля, которые считаются при импорте теста (до заморозки)."""
    from datetime import datetime, timezone
    if os.environ.get("TEST_REAL_CLOCK") == "1":
        return datetime.now(timezone.utc).replace(microsecond=0)
    return datetime.fromisoformat(os.environ.get("TEST_NOW", DEFAULT_TEST_NOW)).replace(tzinfo=timezone.utc)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_clock: тест идёт на настоящих часах (без freezegun)")


@pytest.fixture(autouse=True)
def _frozen_clock(request):
    if os.environ.get("TEST_REAL_CLOCK") == "1" or request.node.get_closest_marker("real_clock"):
        yield None
        return
    from freezegun import freeze_time
    with freeze_time(os.environ.get("TEST_NOW", DEFAULT_TEST_NOW), tick=True) as frozen:
        yield frozen


@pytest.fixture(autouse=True)
def _mute_bot_notify():
    """Заглушить notify_user во всех роут-модулях Mini App.

    Иначе любой тест write-роута дёрнул бы реальный Telegram sendMessage
    на FAKE_TG_ID (= реальный tg Кай). notify_user импортирован по имени
    в каждый модуль, поэтому патчим каждую ссылку.
    """
    targets = [
        "miniapp.backend.routes.writes.notify_user",
        "miniapp.backend.routes.writes.clear_task_reminder",
        "miniapp.backend.routes.arcana_inventory.notify_user",
        "miniapp.backend.routes.arcana_finance.notify_user",
        "miniapp.backend.routes.arcana_today.notify_user",
    ]
    patchers = []
    for t in targets:
        try:
            p = patch(t, AsyncMock(return_value=True))
            p.start()
            patchers.append(p)
        except (ImportError, AttributeError, ModuleNotFoundError):
            pass
    try:
        yield
    finally:
        for p in patchers:
            p.stop()


@pytest.fixture
def mock_message():
    """Создать мок aiogram Message."""
    def _make(text="", from_id=700000001, first_name="Кай", chat_id=700000001):
        msg = AsyncMock()
        msg.text = text
        msg.from_user = MagicMock()
        msg.from_user.id = from_id
        msg.from_user.first_name = first_name
        msg.from_user.language_code = "ru"
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat.type = "private"
        msg.message_id = 12345
        msg.date = datetime.now(timezone.utc)
        msg.answer = AsyncMock(return_value=MagicMock(message_id=12346))
        msg.reply = AsyncMock(return_value=MagicMock(message_id=12347))
        msg.bot = AsyncMock()
        msg.bot.set_message_reaction = AsyncMock()
        msg.bot.send_message = AsyncMock()
        msg.photo = None
        msg.voice = None
        msg.document = None
        msg.content_type = "text"
        return msg
    return _make


@pytest.fixture
def mock_callback():
    """Создать мок CallbackQuery."""
    def _make(data="", from_id=700000001, message_text=""):
        cb = AsyncMock()
        cb.data = data
        cb.from_user = MagicMock()
        cb.from_user.id = from_id
        cb.message = MagicMock()
        cb.message.text = message_text
        cb.message.message_id = 12345
        cb.message.chat = MagicMock()
        cb.message.chat.id = from_id
        cb.message.edit_text = AsyncMock()
        cb.message.edit_reply_markup = AsyncMock()
        cb.message.answer = AsyncMock()
        cb.message.bot = AsyncMock()
        cb.answer = AsyncMock()
        return cb
    return _make


@pytest.fixture
def mock_notion():
    """Мок всех Notion операций — патчим NotionClient синглтон."""
    mock_client = MagicMock()
    mock_client.create_page = AsyncMock(return_value="fake-page-id")
    mock_client.update_page = AsyncMock()
    mock_client.query_database = AsyncMock(return_value=[])
    mock_client._client = MagicMock()
    mock_client._client.pages.create = AsyncMock(return_value={
        "id": "fake-page-id",
        "url": "https://notion.so/fake-page"
    })
    mock_client._client.pages.update = AsyncMock(return_value={"id": "fake-page-id"})
    mock_client._client.databases.query = AsyncMock(return_value={"results": []})

    with patch("core.notion_client._notion", return_value=mock_client), \
         patch("core.notion_client._instance", mock_client):
        yield mock_client


@pytest.fixture
def mock_claude():
    """Мок Claude API ответов."""
    async def fake_classify(text, *args, **kwargs):
        """Предсказуемая классификация."""
        text_lower = text.lower()

        if any(w in text_lower for w in ["задача", "задач", "напомни"]):
            return {
                "type": "task",
                "title": text,
                "category": "💳 Прочее",
                "priority": "⚪ Можно потом",
                "deadline": None,
                "reminder": None,
                "repeat": "Нет"
            }
        elif any(w in text_lower for w in ["потратила", "расход", "₽", "р "]):
            return {
                "type": "expense",
                "amount": 100,
                "category": "🍜 Продукты",
                "description": "тест",
                "source": "💳 Карта"
            }
        elif any(w in text_lower for w in ["доход", "получила", "заработала"]):
            return {
                "type": "income",
                "amount": 1000,
                "category": "💰 Зарплата",
                "description": "тест"
            }
        elif any(w in text_lower for w in ["заметка", "заметк"]):
            return {"type": "note", "text": text, "tags": ["тест"]}
        elif any(w in text_lower for w in ["купить", "покупк"]):
            return {
                "type": "list_buy",
                "items": [{"name": "тест", "category": "🍜 Продукты"}]
            }
        else:
            return {"type": "unknown"}

    return fake_classify


@pytest.fixture
def user_id():
    """Фейковый Notion user ID."""
    return "fake-user-notion-id"
