"""tests/test_session_category_resolve.py — unit tests for phase 2 of #174.

Tests cover:
- CATEGORY_CODE_MAP exact + substring matching
- _resolve_category priority: client anchor → haiku hint → None
- Shape strings (триплет / кельтский крест) produce None (not stored)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — mock _repo so we don't touch the DB
# ──────────────────────────────────────────────────────────────────────────────

def _make_repo(*, anchor=None, resolve_code=None):
    """Return a mock SessionsRepo with controllable responses."""
    repo = MagicMock()
    repo.get_mode_category_for_client = AsyncMock(return_value=anchor)
    repo.resolve_category_code = AsyncMock(return_value=resolve_code)
    return repo


# ──────────────────────────────────────────────────────────────────────────────
# CATEGORY_CODE_MAP tests
# ──────────────────────────────────────────────────────────────────────────────

def test_category_code_map_known_keys():
    from arcana.handlers.sessions import CATEGORY_CODE_MAP
    assert CATEGORY_CODE_MAP["сфера жизни"] == "sphere"
    assert CATEGORY_CODE_MAP["отношения"] == "sphere"
    assert CATEGORY_CODE_MAP["работа"] == "sphere"
    assert CATEGORY_CODE_MAP["финансы"] == "sphere"
    assert CATEGORY_CODE_MAP["здоровье"] == "sphere"
    assert CATEGORY_CODE_MAP["род"] == "ancestral"
    assert CATEGORY_CODE_MAP["родовое"] == "ancestral"
    assert CATEGORY_CODE_MAP["магические воздействия"] == "magical"
    assert CATEGORY_CODE_MAP["диагностика"] == "diag_ritual"
    assert CATEGORY_CODE_MAP["диагностика способностей"] == "diag_ability"


def test_category_code_map_no_shape_keys():
    """Shape strings must NOT appear in CATEGORY_CODE_MAP (shape is dropped)."""
    from arcana.handlers.sessions import CATEGORY_CODE_MAP
    assert "триплет" not in CATEGORY_CODE_MAP
    assert "кельтский крест" not in CATEGORY_CODE_MAP
    assert "🔺 триплет" not in {k.lower() for k in CATEGORY_CODE_MAP}


# ──────────────────────────────────────────────────────────────────────────────
# _resolve_category tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_category_client_anchor_wins():
    """Client anchor (id=3) is returned even when haiku hint would map differently."""
    repo = _make_repo(anchor=3, resolve_code=1)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result = await _resolve_category("client-abc", "сфера жизни")
    assert result == 3
    repo.get_mode_category_for_client.assert_awaited_once_with("client-abc")
    repo.resolve_category_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_category_haiku_hint_fallback():
    """When anchor is absent, haiku hint resolves via code lookup."""
    repo = _make_repo(anchor=None, resolve_code=2)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result = await _resolve_category("client-xyz", "родовое")
    assert result == 2
    repo.resolve_category_code.assert_awaited_once_with("ancestral")


@pytest.mark.asyncio
async def test_resolve_category_no_client_haiku_hint():
    """No client_id — haiku hint still works."""
    repo = _make_repo(anchor=None, resolve_code=3)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result = await _resolve_category(None, "магические воздействия")
    assert result == 3
    repo.get_mode_category_for_client.assert_not_awaited()
    repo.resolve_category_code.assert_awaited_once_with("magical")


@pytest.mark.asyncio
async def test_resolve_category_shape_hint_returns_none():
    """Shape strings like 'триплет' produce None — not stored in phase 2."""
    repo = _make_repo(anchor=None, resolve_code=None)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result_triplet = await _resolve_category(None, "триплет")
        result_cross = await _resolve_category(None, "🔺 Триплет")
        result_celtic = await _resolve_category(None, "кельтский крест")
    assert result_triplet is None
    assert result_cross is None
    assert result_celtic is None
    repo.resolve_category_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_category_no_client_no_hint_returns_none():
    """Vision path with no history — category_id stays NULL."""
    repo = _make_repo(anchor=None, resolve_code=None)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result = await _resolve_category(None, None)
    assert result is None
    repo.get_mode_category_for_client.assert_not_awaited()
    repo.resolve_category_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_category_substring_hint():
    """Haiku may return 'родовой узел' — substring match finds 'ancestral'."""
    repo = _make_repo(anchor=None, resolve_code=2)
    with patch("arcana.handlers.sessions._repo", repo):
        from arcana.handlers.sessions import _resolve_category
        result = await _resolve_category(None, "родовой узел")
    # 'родовое' is in CATEGORY_CODE_MAP and 'родовое' is substring of 'родовой узел'
    # OR 'родовой узел' contains 'род' — either way code='ancestral'
    assert result == 2
