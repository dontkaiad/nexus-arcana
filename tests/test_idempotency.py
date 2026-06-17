"""tests/test_idempotency.py — idempotency guard for financial POST endpoints (#7).

Тестирует:
  1. Тот же (tg_id, key) дважды → ОДНА финзапись, второй вызов возвращает cached result
     с _replay=True.
  2. Разные key, одинаковые значения → ДВЕ записи (легитимные повторы НЕ режутся).
  3. /checkout на уже-done айтеме → вторая финзапись НЕ создаётся.
  4. Запрос без заголовка Idempotency-Key → работает как раньше (не падает).
  5. Race: fetch_result None → poll → значение на 2-й попытке → fn() НЕ вызван, replay.
  6. Race: fetch_result None все 5 попыток → fallback fn() вызван один раз.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.repos.idempotency_repo as _idem_mod
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

FAKE_TG_ID = 99887766
FAKE_NOTION_USER = "user-idem-test"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _make_idem_store():
    """In-process idem store: try_reserve / fetch_result / store_result mocks."""
    store = {}

    async def try_reserve(tg_id, key):
        k = (tg_id, key)
        if k not in store:
            store[k] = None  # reserved, no result yet
            return True
        return False

    async def fetch_result(tg_id, key):
        return store.get((tg_id, key))

    async def store_result(tg_id, key, result):
        store[(tg_id, key)] = result

    return (
        AsyncMock(side_effect=try_reserve),
        AsyncMock(side_effect=fetch_result),
        AsyncMock(side_effect=store_result),
    )


# ── Test 1: same key → single write, second call returns replay ───────────────

def test_same_key_twice_creates_one_finance_record(client):
    from miniapp.backend.routes import writes as _writes_mod
    from datetime import date

    call_count = 0

    async def fake_add(**kwargs):
        nonlocal call_count
        call_count += 1
        return f"fin-{call_count}"

    try_reserve_mock, fetch_result_mock, store_result_mock = _make_idem_store()

    with patch.object(_idem_mod._idem_repo, "try_reserve", try_reserve_mock), \
         patch.object(_idem_mod._idem_repo, "fetch_result", fetch_result_mock), \
         patch.object(_idem_mod._idem_repo, "store_result", store_result_mock), \
         patch.object(_writes_mod._fin_repo, "add", AsyncMock(side_effect=fake_add)), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(date(2026, 6, 17), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):

        r1 = client.post(
            "/api/finance",
            json={"type": "expense", "amount": 500, "cat": "🍜 Продукты"},
            headers={"Idempotency-Key": "test-key-abc"},
        )
        r2 = client.post(
            "/api/finance",
            json={"type": "expense", "amount": 500, "cat": "🍜 Продукты"},
            headers={"Idempotency-Key": "test-key-abc"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert call_count == 1, "fin_repo.add должен быть вызван ровно один раз"
    assert r1.json()["id"] == r2.json()["id"], "оба вызова должны вернуть одинаковый id"
    assert r2.json().get("_replay") is True, "второй вызов должен содержать _replay=True"


# ── Test 2: different keys → two finance records ──────────────────────────────

def test_different_keys_create_two_finance_records(client):
    from miniapp.backend.routes import writes as _writes_mod
    from datetime import date

    call_count = 0

    async def fake_add(**kwargs):
        nonlocal call_count
        call_count += 1
        return f"fin-{call_count}"

    try_reserve_mock, fetch_result_mock, store_result_mock = _make_idem_store()

    with patch.object(_idem_mod._idem_repo, "try_reserve", try_reserve_mock), \
         patch.object(_idem_mod._idem_repo, "fetch_result", fetch_result_mock), \
         patch.object(_idem_mod._idem_repo, "store_result", store_result_mock), \
         patch.object(_writes_mod._fin_repo, "add", AsyncMock(side_effect=fake_add)), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(date(2026, 6, 17), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):

        r1 = client.post(
            "/api/finance",
            json={"type": "expense", "amount": 500, "cat": "🍜 Продукты"},
            headers={"Idempotency-Key": "key-one"},
        )
        r2 = client.post(
            "/api/finance",
            json={"type": "expense", "amount": 500, "cat": "🍜 Продукты"},
            headers={"Idempotency-Key": "key-two"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert call_count == 2, "разные ключи должны создать две записи"
    assert r1.json()["id"] != r2.json()["id"]
    assert "_replay" not in r1.json()
    assert "_replay" not in r2.json()


# ── Test 3: /checkout on already-done item → no second finance record ─────────

def test_checkout_already_done_item_skips_finance(client):
    from miniapp.backend.routes import writes as _writes_mod

    add_mock = AsyncMock(return_value="fin-999")
    done_item = MagicMock()
    done_item.name = "молоко"
    done_item.category = "🍜 Продукты"
    done_item.status = "done"  # already done
    done_item.price_plan = None
    done_item.user_notion_id = FAKE_NOTION_USER

    with patch.object(_writes_mod._fin_repo, "add", add_mock), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.writes._get_list_item_pg",
               AsyncMock(return_value=(done_item, False))), \
         patch.object(_writes_mod._nexus_lists_repo, "update", AsyncMock()):

        r = client.post(
            "/api/lists/some-item-id/checkout",
            json={"price": 89.0},
            headers={"Idempotency-Key": "checkout-key-x"},
        )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["finance_created"] is False, "уже done → финзапись не должна создаваться"
    add_mock.assert_not_awaited()


# ── Test 4: no header → works without dedup ───────────────────────────────────

def test_finance_without_idempotency_key_works(client):
    from miniapp.backend.routes import writes as _writes_mod
    from datetime import date

    async def fake_add(**kwargs):
        return "fin-no-key"

    with patch.object(_writes_mod._fin_repo, "add", AsyncMock(side_effect=fake_add)), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(date(2026, 6, 17), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):

        # No Idempotency-Key header at all
        r = client.post(
            "/api/finance",
            json={"type": "income", "amount": 5000},
        )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["id"] == "fin-no-key"
    assert "_replay" not in data


# ── Test 5: race resolves on 2nd poll ─────────────────────────────────────────

def test_race_resolves_on_second_poll():
    """Race: initial fetch None, poll[0] None, poll[1] value → fn() NOT called, replay returned."""
    call_count = 0
    winner_result = {"ok": True, "id": "fin-winner"}

    # Sequence of fetch_result returns: initial-check=None, poll[0]=None, poll[1]=winner
    fetch_sequence = [None, None, winner_result]
    fetch_idx = 0

    async def fake_fetch(tg_id, key):
        nonlocal fetch_idx
        val = fetch_sequence[fetch_idx] if fetch_idx < len(fetch_sequence) else winner_result
        fetch_idx += 1
        return val

    async def fn():
        nonlocal call_count
        call_count += 1
        return {"ok": True, "id": "fin-fn"}

    async def run():
        with patch.object(_idem_mod._idem_repo, "try_reserve", AsyncMock(return_value=False)), \
             patch.object(_idem_mod._idem_repo, "fetch_result", AsyncMock(side_effect=fake_fetch)), \
             patch("core.repos.idempotency_repo.asyncio.sleep", AsyncMock()):
            return await _idem_mod.idempotent(99, "race-key-poll", fn)

    result = asyncio.run(run())
    assert result.get("_replay") is True
    assert result["id"] == "fin-winner"
    assert call_count == 0, "fn() не должен вызываться если поллинг нашёл результат"


# ── Test 6: race exhausted → fallback fn() ────────────────────────────────────

def test_race_exhausted_falls_back_to_fn():
    """Race: fetch_result None все 5 попыток поллинга → fallback fn() вызван ровно один раз."""
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        return {"ok": True, "id": "fin-fallback"}

    async def run():
        with patch.object(_idem_mod._idem_repo, "try_reserve", AsyncMock(return_value=False)), \
             patch.object(_idem_mod._idem_repo, "fetch_result", AsyncMock(return_value=None)), \
             patch("core.repos.idempotency_repo.asyncio.sleep", AsyncMock()):
            return await _idem_mod.idempotent(99, "race-exhausted-key", fn)

    result = asyncio.run(run())
    assert result["id"] == "fin-fallback"
    assert call_count == 1, "fn() должен быть вызван ровно один раз как fallback"
    assert "_replay" not in result
