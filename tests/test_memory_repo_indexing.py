"""tests/test_memory_repo_indexing.py — эмбеддинг-хук в PgMemoryRepo.add/upsert
(#186): единая точка индексации для ВСЕХ писателей памяти (save_memory,
auto-suggest confirm, бюджет, локация), не только core/memory.py:save_memory.

SQLite-фикстура (как tests/test_find_by_exact_key.py) + реальный event loop,
чтобы _spawn_index() увидел running loop и создал задачу по-настоящему.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.pool import StaticPool


def _register_now(eng):
    """_upsert_sync использует text('now()') (Postgres) — SQLite его не знает,
    регистрируем no-op stand-in под тесты обновления существующей строки."""
    @event.listens_for(eng, "connect")
    def _add_now(dbapi_conn, _rec):
        dbapi_conn.create_function("now", 0, lambda: "2026-08-21T00:00:00")


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _register_now(eng)
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "notion_id TEXT UNIQUE, "
            "fact_text TEXT NOT NULL DEFAULT '', "
            "key_name TEXT NOT NULL DEFAULT '', "
            "value_text TEXT NOT NULL DEFAULT '', "
            "category TEXT NOT NULL DEFAULT '', "
            "scope TEXT NOT NULL DEFAULT 'global', "
            "source TEXT NOT NULL DEFAULT 'manual', "
            "related_to TEXT NOT NULL DEFAULT '', "
            "is_current INTEGER NOT NULL DEFAULT 1, "
            "is_archived INTEGER NOT NULL DEFAULT 0, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "embedding TEXT, "  # SQLite: просто колонка, не pgvector — под NULL-reset
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return eng


async def _drain_index_tasks():
    """Даёт спавненным fire-and-forget task'ам (_spawn_index) реально выполниться —
    без этого asyncio может не успеть их прокрутить до конца теста."""
    import core.repos.pg_memory_repo as pgmod
    if pgmod._index_tasks:
        await asyncio.gather(*list(pgmod._index_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_add_spawns_index_task():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id = await repo.add("новый факт", key="k1", category="🏠 Быт", related_to="связь")
        await _drain_index_tasks()

    idx.assert_called_once()
    called_id, embed_text = idx.call_args[0]
    assert called_id == mem_id
    assert embed_text == "новый факт связь 🏠 Быт"


@pytest.mark.asyncio
async def test_upsert_create_path_spawns_index_task():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id, was_updated = await repo.upsert("лимит: кафе 5000", key="лимит_кафе", category="💰 Лимит")
        await _drain_index_tasks()

    assert was_updated is False
    idx.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_update_path_nulls_stale_embedding_then_reindexes():
    """Апдейт существующей строки: старый embedding сначала NULL-ится (защита от
    протухшего вектора), потом спавнится переиндексация на новый текст."""
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, embedding, is_current) "
            "VALUES ('лимит_кафе', '💰 Лимит', 'лимит: кафе 3000', 'old-vector', 1)"
        ))

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id, was_updated = await repo.upsert("лимит: кафе 5000", key="лимит_кафе", category="💰 Лимит")
        await _drain_index_tasks()

    assert was_updated is True
    idx.assert_called_once()
    called_id, embed_text = idx.call_args[0]
    assert called_id == mem_id
    assert embed_text == "лимит: кафе 5000 💰 Лимит"  # переиндексирован НОВЫЙ текст


@pytest.mark.asyncio
async def test_upsert_update_path_survives_missing_embedding_column():
    """Если embedding-колонки нет (миграция не накатана) — NULL-reset падает
    graceful, но сам апдейт факта НЕ откатывается (#186)."""
    eng = sa.create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _register_now(eng)
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, notion_id TEXT UNIQUE, "
            "fact_text TEXT NOT NULL DEFAULT '', key_name TEXT NOT NULL DEFAULT '', "
            "value_text TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', "
            "scope TEXT NOT NULL DEFAULT 'global', source TEXT NOT NULL DEFAULT 'manual', "
            "related_to TEXT NOT NULL DEFAULT '', is_current INTEGER NOT NULL DEFAULT 1, "
            "is_archived INTEGER NOT NULL DEFAULT 0, user_notion_id TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"  # НЕТ embedding
        ))
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, is_current) "
            "VALUES ('лимит_кафе', '💰 Лимит', 'лимит: кафе 3000', 1)"
        ))

    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id, was_updated = await repo.upsert("лимит: кафе 5000", key="лимит_кафе", category="💰 Лимит")
        await _drain_index_tasks()

    assert was_updated is True
    with eng.connect() as conn:
        row = conn.execute(sa.text("SELECT fact_text FROM memories WHERE id=:id"), {"id": int(mem_id)}).fetchone()
    assert row[0] == "лимит: кафе 5000"  # апдейт факта прошёл, несмотря на отсутствие колонки


# ── upsert matching: key (+owner), NOT key+category (#193 follow-up) ────────

@pytest.mark.asyncio
async def test_upsert_finds_row_by_key_even_after_category_renamed():
    """Регрессия: core/location.py раньше писал tz_{tg_id} под category=
    "Настройки", потом переехал на "🏠 Быт" — апдейт с новой категорией
    матчился по key+category, не находил старую строку (category не
    совпадал), создавал ВТОРУЮ и оставлял первую висеть в БД навсегда.
    Матч теперь только по key_name (+ owner) — должен найти и обновить
    ту же строку, поправив category заодно."""
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, user_notion_id, is_current) "
            "VALUES ('tz_67686090', 'Настройки', '5', 'u1', 1)"
        ))

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id, was_updated = await repo.upsert(
            "5", key="tz_67686090", category="🏠 Быт", user_notion_id="u1",
        )
        await _drain_index_tasks()

    assert was_updated is True
    with eng.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT category FROM memories WHERE key_name='tz_67686090'"
        )).fetchall()
    # Одна строка, не две — старая обновлена на месте, не осиротела.
    assert len(rows) == 1
    assert rows[0][0] == "🏠 Быт"


@pytest.mark.asyncio
async def test_upsert_does_not_cross_user_boundary():
    """Без user_notion_id в матче совпадение key_name у ДВУХ разных юзеров
    (напр. одинаковый сгенерированный ключ лимита) перезаписало бы чужую
    запись. user_notion_id теперь часть матча — чужая строка не трогается,
    для нового юзера создаётся своя."""
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, user_notion_id, is_current) "
            "VALUES ('лимит_еда', '💰 Лимит', 'лимит: еда 3000', 'user-A', 1)"
        ))

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        mem_id, was_updated = await repo.upsert(
            "лимит: еда 4000", key="лимит_еда", category="💰 Лимит", user_notion_id="user-B",
        )
        await _drain_index_tasks()

    assert was_updated is False  # новая строка для user-B, не апдейт чужой
    with eng.connect() as conn:
        rows = {r[0]: r[1] for r in conn.execute(sa.text(
            "SELECT user_notion_id, fact_text FROM memories WHERE key_name='лимит_еда'"
        )).fetchall()}
    assert rows["user-A"] == "лимит: еда 3000"   # не тронута
    assert rows["user-B"] == "лимит: еда 4000"   # новая своя


# ── update_fields (#188: reply-исправление по id) ────────────────────────────

@pytest.mark.asyncio
async def test_update_fields_by_id_updates_fact_and_reindexes():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, related_to, embedding, is_current) "
            "VALUES ('s7', '🐾 Коты', 'старый текст', 'S7', 'old-vector', 1)"
        ))

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        repo = PgMemoryRepo()
        ok = await repo.update_fields("1", fact="новый текст")
        await _drain_index_tasks()

    assert ok is True
    with eng.connect() as conn:
        row = conn.execute(sa.text("SELECT fact_text, category FROM memories WHERE id=1")).fetchone()
    assert row[0] == "новый текст"
    assert row[1] == "🐾 Коты"  # category не трогали
    idx.assert_called_once()
    called_id, embed_text = idx.call_args[0]
    assert called_id == "1"
    assert embed_text == "новый текст S7 🐾 Коты"  # переиндексирован свежий (не старый) факт


@pytest.mark.asyncio
async def test_update_fields_by_id_updates_category_only():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, is_current) "
            "VALUES ('s7', '🐾 Коты', 'факт', 1)"
        ))

    with patch.object(pgmod, "get_engine", return_value=eng), \
         patch("core.memory_rag.index_memory", return_value=True):
        repo = PgMemoryRepo()
        ok = await repo.update_fields("1", category="🛒 Предпочтения")
        await _drain_index_tasks()

    assert ok is True
    with eng.connect() as conn:
        row = conn.execute(sa.text("SELECT fact_text, category FROM memories WHERE id=1")).fetchone()
    assert row == ("факт", "🛒 Предпочтения")


@pytest.mark.asyncio
async def test_update_fields_nonexistent_id_returns_false():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with patch.object(pgmod, "get_engine", return_value=eng):
        repo = PgMemoryRepo()
        ok = await repo.update_fields("999", fact="что угодно")

    assert ok is False


@pytest.mark.asyncio
async def test_update_fields_nothing_passed_returns_false():
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with eng.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, category, fact_text, is_current) "
            "VALUES ('s7', '🐾 Коты', 'факт', 1)"
        ))
    with patch.object(pgmod, "get_engine", return_value=eng):
        repo = PgMemoryRepo()
        ok = await repo.update_fields("1")  # ни fact, ни category, ни related_to

    assert ok is False
