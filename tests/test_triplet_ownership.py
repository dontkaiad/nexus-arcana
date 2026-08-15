"""tests/test_triplet_ownership.py — issue #108.

_resolve_triplet_page(short_id, user_notion_id) is the shared chokepoint used
by every triplet-correct/-remove callback (and the reply-edit path). Callers
already resolve the acting user's user_notion_id and pass it through, on the
assumption that a mismatch means "not found" — but the underlying
find_by_short_id → find_by_id query only ever filtered by id, never by
owner, so any user who could produce/guess another user's short_id could
resolve (and then edit/archive) a triplet that wasn't theirs.

_resolve_triplet_page now checks the resolved entry's own user_notion_id
against the caller's before returning it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from arcana.handlers import sessions
from arcana.repos.sessions_repo import TripletEntry


def _entry(owner: str) -> TripletEntry:
    return TripletEntry(
        id="1", question="q", cards="c", interpretation="i", deck="Уэйт",
        session_name="", client_id=None, user_notion_id=owner,
    )


@pytest.mark.asyncio
async def test_resolve_returns_entry_for_matching_owner():
    with patch.object(sessions._repo, "find_by_short_id",
                       AsyncMock(return_value=_entry("user-a"))):
        entry = await sessions._resolve_triplet_page("short1", "user-a")
    assert entry is not None
    assert entry.id == "1"


@pytest.mark.asyncio
async def test_resolve_returns_none_for_mismatched_owner():
    """Repro: a valid short_id belonging to user-a resolved even when the
    caller is user-b, because find_by_short_id never applied the filter."""
    with patch.object(sessions._repo, "find_by_short_id",
                       AsyncMock(return_value=_entry("user-a"))):
        entry = await sessions._resolve_triplet_page("short1", "user-b")
    assert entry is None


@pytest.mark.asyncio
async def test_resolve_allows_legacy_rows_with_no_recorded_owner():
    """Pre-migration rows with no owner recorded shouldn't be locked out."""
    with patch.object(sessions._repo, "find_by_short_id",
                       AsyncMock(return_value=_entry(""))):
        entry = await sessions._resolve_triplet_page("short1", "user-a")
    assert entry is not None


@pytest.mark.asyncio
async def test_resolve_none_when_not_found():
    with patch.object(sessions._repo, "find_by_short_id",
                       AsyncMock(return_value=None)):
        entry = await sessions._resolve_triplet_page("short1", "user-a")
    assert entry is None
