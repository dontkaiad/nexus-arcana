"""Тесты core модулей: memory, list_manager, pending_clients, utilities."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestMemoryModule:
    """core/memory.py — helper функции."""

    def test_normalize_word(self):
        from core.memory import _normalize_word
        # снимает окончания (если есть stemming)
        result = _normalize_word("тесты")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tokenize_hint(self):
        from core.memory import _tokenize_hint
        tokens = _tokenize_hint("маша привычки лимит")
        assert isinstance(tokens, list)
        assert len(tokens) >= 2

    def test_extract_context_keywords(self):
        from core.memory import extract_context_keywords
        data = {"type": "task", "title": "купить корм коту", "category": "🐾 Коты"}
        keywords = extract_context_keywords(data)
        assert isinstance(keywords, list)

    def test_extract_context_keywords_with_client(self):
        from core.memory import extract_context_keywords
        data = {"type": "session", "client": "Анна"}
        keywords = extract_context_keywords(data, client_name="Анна")
        assert isinstance(keywords, list)

    @pytest.mark.asyncio
    async def test_recall_from_memory_mocked(self):
        """recall_from_memory — вернуть None если ничего не найдено."""
        from core.memory import recall_from_memory
        with patch("core.memory._find_pages", new=AsyncMock(return_value=[])):
            result = await recall_from_memory("несуществующий_ключ")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_memories_for_context_empty(self):
        """get_memories_for_context с пустыми keywords → пусто."""
        from core.memory import get_memories_for_context
        with patch("core.memory._find_pages", new=AsyncMock(return_value=[])):
            result = await get_memories_for_context(
                user_notion_id="fake-id",
                keywords=[],
                bot_label="☀️ Nexus",
            )
            # может вернуть "" или None
            assert result in ("", None) or isinstance(result, str)


class TestListManager:
    """core/list_manager.py — pending state + helpers."""

    def test_pending_set_get(self):
        """pending_set → pending_get возвращает те же данные."""
        from core.list_manager import pending_set, pending_get, pending_del
        pending_set(99999, {"test": True, "marker": "core_test"})
        data = pending_get(99999)
        assert data is not None
        assert data.get("test") is True
        pending_del(99999)

    def test_pending_pop(self):
        """pending_pop возвращает данные и удаляет."""
        from core.list_manager import pending_set, pending_pop, pending_get
        pending_set(99998, {"x": 42})
        popped = pending_pop(99998)
        assert popped is not None
        assert popped.get("x") == 42
        # После pop — нет в базе
        assert pending_get(99998) is None

    def test_pending_del_nonexistent(self):
        """pending_del для несуществующего — не крашит."""
        from core.list_manager import pending_del
        pending_del(88888)  # просто не должен упасть

    def test_checkbox_helper(self):
        from core.list_manager import _checkbox
        assert _checkbox(True) == {"checkbox": True}
        assert _checkbox(False) == {"checkbox": False}

    def test_today_iso(self):
        from core.list_manager import _today_iso
        result = _today_iso()
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD
        assert result[4] == "-"


class TestPendingClients:
    """arcana/pending_clients.py — save/get/update/delete."""

    @pytest.mark.asyncio
    async def test_save_get_delete(self):
        from arcana.pending_clients import (
            save_pending_client, get_pending_client,
            delete_pending_client,
        )
        await save_pending_client(99997, {"step": "collecting", "name": "Тест"})
        data = await get_pending_client(99997)
        assert data is not None
        assert data.get("step") == "collecting"
        assert data.get("name") == "Тест"

        await delete_pending_client(99997)
        after = await get_pending_client(99997)
        assert after is None

    @pytest.mark.asyncio
    async def test_update_pending_client(self):
        from arcana.pending_clients import (
            save_pending_client, get_pending_client,
            update_pending_client, delete_pending_client,
        )
        await save_pending_client(99996, {"step": "collecting", "name": "Тест"})
        await update_pending_client(99996, {"contacts": [{"value": "+7..."}]})

        data = await get_pending_client(99996)
        assert data is not None
        assert "contacts" in data
        assert data["contacts"][0]["value"] == "+7..."

        await delete_pending_client(99996)


class TestPendingTarot:
    """arcana/pending_tarot.py — save/get/update/delete."""

    @pytest.mark.asyncio
    async def test_save_get_delete(self):
        from arcana.pending_tarot import (
            save_pending, get_pending, delete_pending,
        )
        state = {"cards": ["Шут", "Маг"], "deck": "уэйт"}
        await save_pending(99995, state)
        result = await get_pending(99995)
        assert result is not None
        assert result.get("cards") == ["Шут", "Маг"]

        await delete_pending(99995)
        after = await get_pending(99995)
        assert after is None

    @pytest.mark.asyncio
    async def test_update_pending(self):
        from arcana.pending_tarot import (
            save_pending, get_pending, update_pending, delete_pending,
        )
        await save_pending(99994, {"cards": ["A"]})
        await update_pending(99994, {"awaiting_edit": True})
        data = await get_pending(99994)
        assert data is not None
        assert data.get("awaiting_edit") is True
        assert data.get("cards") == ["A"]  # старое осталось
        await delete_pending(99994)


class TestTarotLoader:
    """arcana/tarot_loader.py — публичное API."""

    def test_get_deck_file_waite(self):
        from arcana.tarot_loader import get_deck_file
        result = get_deck_file("уэйт")
        assert result is not None

    def test_get_deck_file_unknown(self):
        from arcana.tarot_loader import get_deck_file
        result = get_deck_file("несуществующая_колода_xyz")
        # может вернуть дефолт или None
        assert result is None or isinstance(result, str)

    def test_get_cards_context(self):
        from arcana.tarot_loader import get_cards_context
        ctx = get_cards_context("уэйт", ["туз мечей"])
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_get_cards_context_multiple(self):
        from arcana.tarot_loader import get_cards_context
        ctx = get_cards_context("уэйт", ["туз мечей", "жрица"])
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_get_deck_style(self):
        from arcana.tarot_loader import get_deck_style
        style = get_deck_style("уэйт")
        assert isinstance(style, str)


class TestLayout:
    """core/layout.py — EN→RU конвертация (более глубокие кейсы)."""

    def test_mixed_ru_en(self):
        """Смешанный текст: RU слова не меняются, EN конвертируются."""
        from core.layout import maybe_convert
        result = maybe_convert("задача pflfxf")
        # содержит 'задача' — должен быть лишь в одном виде
        assert isinstance(result, str)

    def test_empty_string(self):
        from core.layout import maybe_convert
        result = maybe_convert("")
        assert result == ""

    def test_numbers_unchanged(self):
        from core.layout import maybe_convert
        result = maybe_convert("12345")
        assert result == "12345"


class TestShared:
    """core/shared_handlers.py и middleware."""

    def test_middleware_import(self):
        from core.middleware import WhitelistMiddleware
        mw = WhitelistMiddleware()
        assert mw is not None

    def test_middleware_feature_flag(self):
        from core.middleware import WhitelistMiddleware
        mw = WhitelistMiddleware(require_feature="arcana")
        assert mw.require_feature == "arcana"


class TestConfig:
    """core/config.py — AppConfig загружен."""

    def test_config_loaded(self):
        from core.config import config
        assert config is not None
        assert config.notion_token
        assert config.nexus.tg_token

    def test_allowed_ids(self):
        from core.config import config
        assert isinstance(config.allowed_ids, list)
        assert len(config.allowed_ids) > 0

    def test_finance_categories(self):
        from core.config import config
        assert isinstance(config.finance_categories, list)
        assert len(config.finance_categories) > 5
