"""tests/test_arcana_summarize_stream.py — SSE-стриминг саммари темы (#191).

GET /api/arcana/sessions/by-slug/{slug}/summarize/stream отдаёт Sonnet-текст
по мере генерации (Server-Sent Events) вместо одного блокирующего ответа —
Mini App печатает текст в реальном времени, как в ChatGPT. Бот в Telegram
эту ветку не трогает — там как был, так и остался ask_claude() целиком
(arcana/handlers/sessions.py не менялся).

Проверяем:
- поток шлёт delta-события в порядке чанков от Claude, потом "done";
- финальный "summary" в done-событии — тот же текст (после sanitize_summary),
  что вернул бы non-streaming POST /summarize для ИДЕНТИЧНОГО ответа Claude
  (#191, требование "финальный собранный текст идентичен non-streaming пути");
- персистится то же самое (set_theme_summary + cache_set), с тем же
  анкором, что у POST-версии;
- кешированный (уже посчитанный) theme_summary отдаётся одним delta +
  done, БЕЗ обращения к Claude — тот же гейт, что у POST;
- сбой Claude на середине потока → error-событие, ничего не персистится.
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.session_cache import slugify
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pg_triplet(pid, sname, client_id="c-1", topic="Q", theme_summary=""):
    from arcana.repos.sessions_repo import TripletEntry
    return TripletEntry(
        id=pid, question=topic, cards="2 мечей, шут, маг",
        interpretation="", deck="Уэйт", session_name=sname, client_id=client_id,
        date="2026-05-01", outcome="unverified",
        amount=Decimal("0"), paid=Decimal("0"),
        category_display="", area="", triplet_summary="кратко",
        session_summary="", theme_summary=theme_summary,
        barter_what="", bottom_card="", photo_url=None,
    )


def _fake_stream(chunks, *, raises=None):
    """Замена ask_claude_stream: сама функция (не AsyncMock) — вызывающий
    код делает `async for x in ask_claude_stream(...)`, т.е. ей нужно быть
    вызываемой синхронно и возвращать async-генератор, а не быть awaitable."""
    async def _gen(*args, **kwargs):
        for c in chunks:
            yield c
        if raises:
            raise raises
    return _gen


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), block
        events.append(json.loads(block[len("data: "):]))
    return events


def _ctx(repo, fake_stream):
    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=[])
    return [
        patch("miniapp.backend.routes.arcana_sessions._sessions_repo", repo),
        patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl),
        patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
        patch("miniapp.backend.routes.arcana_sessions.cache_get", return_value=None),
        patch("miniapp.backend.routes.arcana_sessions.cache_set"),
        patch("core.claude_client.ask_claude_stream", fake_stream),
    ]


def test_stream_sends_deltas_then_done(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t2", sname, "c-1", "2) чувства"),
        _pg_triplet("t1", sname, "c-1", "1) общее"),
    ]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    repo.set_theme_summary = AsyncMock(return_value=True)
    fake_stream = _fake_stream(["Карты ", "показывают ", "движение."])

    ctx = _ctx(repo, fake_stream)
    for c in ctx:
        c.start()
    try:
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}/summarize/stream")
    finally:
        for c in ctx:
            c.stop()

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["Карты ", "показывают ", "движение."]

    done = events[-1]
    assert done["done"] is True
    assert done["cached"] is False
    assert done["summary"] == "Карты показывают движение."

    # Якорь = «1) общее» (index 1 после сортировки) → t1.
    repo.set_theme_summary.assert_awaited_once()
    assert repo.set_theme_summary.await_args.args[0] == "t1"
    assert repo.set_theme_summary.await_args.args[1] == "Карты показывают движение."


def test_stream_final_text_matches_non_streaming_for_same_claude_output(client):
    """#191 требование: собранный из дельт + sanitize_summary текст —
    ИДЕНТИЧЕН тому, что вернул бы non-streaming POST для того же raw-ответа
    Claude (тут — тот же текст, просто разбитый на чанки vs единой строкой)."""
    sname = "Маша"
    slug = f"{slugify(sname)}__c-2"
    raw_text = "Общий вектор — переход. Стоит присмотреться к финансам."
    matching_stream = [_pg_triplet("s1", sname, "c-2", "1) общее")]
    matching_post = [_pg_triplet("s1", sname, "c-2", "1) общее")]

    # non-streaming путь — как в test_arcana_session_summary.py.
    repo_post = MagicMock()
    repo_post.list_by_slug = AsyncMock(return_value=matching_post)
    repo_post.set_theme_summary = AsyncMock(return_value=True)
    ask = AsyncMock(return_value=raw_text)
    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", repo_post), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_sessions.cache_get", return_value=None), \
         patch("miniapp.backend.routes.arcana_sessions.cache_set"), \
         patch("core.claude_client.ask_claude", ask):
        r_post = client.post(f"/api/arcana/sessions/by-slug/{slug}/summarize")
    assert r_post.status_code == 200
    post_summary = r_post.json()["summary"]

    # streaming путь — тот же raw-текст Claude, но по кусочкам.
    repo_stream = MagicMock()
    repo_stream.list_by_slug = AsyncMock(return_value=matching_stream)
    repo_stream.set_theme_summary = AsyncMock(return_value=True)
    # Разбиваем raw_text на чанки по словам, как это реально делает streaming API.
    words = raw_text.split(" ")
    chunks = [w + " " for w in words[:-1]] + [words[-1]]
    fake_stream = _fake_stream(chunks)

    ctx = _ctx(repo_stream, fake_stream)
    for c in ctx:
        c.start()
    try:
        r_stream = client.get(f"/api/arcana/sessions/by-slug/{slug}/summarize/stream")
    finally:
        for c in ctx:
            c.stop()
    assert r_stream.status_code == 200
    events = _parse_sse(r_stream.text)
    stream_summary = events[-1]["summary"]

    assert stream_summary == post_summary == raw_text
    # Персистится тот же итоговый текст в обоих путях.
    assert repo_post.set_theme_summary.await_args.args[1] == post_summary
    assert repo_stream.set_theme_summary.await_args.args[1] == stream_summary


def test_stream_returns_cached_without_calling_claude(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [_pg_triplet("t1", sname, "c-1", "1) общее", theme_summary="ГОТОВОЕ")]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    repo.set_theme_summary = AsyncMock(return_value=True)
    fake_stream = _fake_stream(["НЕ ДОЛЖНО ВЫЗВАТЬСЯ"])

    ctx = _ctx(repo, fake_stream)
    for c in ctx:
        c.start()
    try:
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}/summarize/stream")
    finally:
        for c in ctx:
            c.stop()

    events = _parse_sse(r.text)
    assert events[0] == {"delta": "ГОТОВОЕ"}
    assert events[-1] == {"done": True, "cached": True, "summary": "ГОТОВОЕ"}
    repo.set_theme_summary.assert_not_awaited()


def test_stream_claude_failure_sends_error_and_persists_nothing(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [_pg_triplet("t1", sname, "c-1", "1) общее")]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    repo.set_theme_summary = AsyncMock(return_value=True)
    fake_stream = _fake_stream(["частичный "], raises=RuntimeError("boom"))

    ctx = _ctx(repo, fake_stream)
    for c in ctx:
        c.start()
    try:
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}/summarize/stream")
    finally:
        for c in ctx:
            c.stop()

    events = _parse_sse(r.text)
    assert events[0] == {"delta": "частичный "}
    assert events[-1] == {"error": "summarize failed"}
    repo.set_theme_summary.assert_not_awaited()


def test_stream_404_for_unknown_slug(client):
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=[])
    fake_stream = _fake_stream([])

    ctx = _ctx(repo, fake_stream)
    for c in ctx:
        c.start()
    try:
        r = client.get("/api/arcana/sessions/by-slug/nikogo-net__self/summarize/stream")
    finally:
        for c in ctx:
            c.stop()

    assert r.status_code == 404
