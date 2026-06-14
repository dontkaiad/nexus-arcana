"""tests/test_memory_aliases.py — alias resolver для core/memory.py (v1.2.4).

Контекст: до фикса Haiku в _parse_fact возвращал связь дословно из текста.
Если в Памяти уже была запись «у X кличка Y», новое сообщение с алиасом Y
расщепляло связь — создавалась запись со связью=Y вместо канонического X.

Фикс: пост-обработка через _resolve_alias до page_create.

Privacy: фикстуры используют generic X/Y/Z имена, никаких реальных.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


from core.memory import _ALIAS_DEPTH_LIMIT, _resolve_alias
from core.repos.pg_memory_repo import Memory


def _mk_page(text: str) -> Memory:
    """Минимальный Memory-объект для alias-тестов."""
    return Memory(id=str(abs(hash(text)) % 100000), fact=text)


def _patched_find(return_value):
    """Helper: patch _find_pages_by_hint с фиксированным или callable-возвратом."""
    return patch(
        "core.memory._find_pages_by_hint",
        AsyncMock(side_effect=return_value)
        if callable(return_value)
        else AsyncMock(return_value=return_value),
    )


# ── Главные кейсы ────────────────────────────────────────────────────────────


def test_alias_kratkaya_klichka():
    """«у X краткая кличка Y» + связь=Y → X."""
    pages = [_mk_page("у X краткая кличка Y")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "x"


def test_alias_prozvische():
    """«у Z прозвище W» + связь=W → Z."""
    pages = [_mk_page("у Z прозвище W")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("W"))
    assert result == "z"


def test_alias_brackets_on_zhe():
    """«X (он же Y)» + связь=Y → X."""
    pages = [_mk_page("X (он же Y)")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "x"


def test_alias_brackets_ona_zhe():
    """«X (она же Y)»."""
    pages = [_mk_page("X (она же Y)")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "x"


def test_alias_equality():
    """«A = B» + связь=B → A."""
    pages = [_mk_page("A = B")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("B"))
    assert result == "a"


def test_alias_em_dash_marker():
    """«A — B» (em-dash) + связь=B → A."""
    pages = [_mk_page("A — B")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("B"))
    assert result == "a"


def test_alias_korotko():
    """«X коротко Y» + связь=Y → X."""
    pages = [_mk_page("X коротко Y")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "x"


def test_alias_takzhe_nazyvaetsya():
    """«у X также называется Y»."""
    pages = [_mk_page("у X также называется Y")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "x"


# ── Регистр ──────────────────────────────────────────────────────────────────


def test_alias_mixed_case():
    """«у Маша кличка Маня» + связь=маня → маша."""
    pages = [_mk_page("у Маша кличка Маня")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("маня"))
    assert result == "маша"


def test_alias_uppercase_input():
    """связь=МАНЯ (ALL CAPS) тоже резолвится."""
    pages = [_mk_page("у Маша кличка Маня")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("МАНЯ"))
    assert result == "маша"


# ── Не алиас ─────────────────────────────────────────────────────────────────


def test_no_alias_marker():
    """«X не любит Y» — нет маркера алиаса → связь не меняется."""
    pages = [_mk_page("X не любит Y")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Y"))
    assert result == "Y"


def test_no_pages_returned():
    """_find_pages_by_hint вернул [] → связь без изменений."""
    with _patched_find([]):
        result = asyncio.run(_resolve_alias("Z"))
    assert result == "Z"


def test_empty_link():
    """Пустая связь → пустая, без обращения к Notion."""
    with _patched_find([]) as m:
        result = asyncio.run(_resolve_alias(""))
    assert result == ""


def test_whitespace_link():
    """Связь из пробелов → возвращается как есть."""
    with _patched_find([]):
        result = asyncio.run(_resolve_alias("   "))
    assert result.strip() == ""


# ── Цепочки ──────────────────────────────────────────────────────────────────


def test_alias_chain_two_hops():
    """A→B→C. Запись «у A коротко B» + «у B коротко C». связь=C → A."""
    pages_for_C = [_mk_page("у B коротко C")]
    pages_for_B = [_mk_page("у A коротко B")]
    pages_for_A: list = []  # В Notion нет дальнейшей записи про A → останавливаемся

    async def find_pages(hint, *args, **kwargs):
        h = hint.lower()
        if h == "c":
            return pages_for_C
        if h == "b":
            return pages_for_B
        if h == "a":
            return pages_for_A
        return []

    with patch("core.memory._find_pages_by_hint",
               AsyncMock(side_effect=find_pages)):
        result = asyncio.run(_resolve_alias("C"))
    assert result == "a"


def test_alias_chain_depth_limit():
    """Бесконечная цепочка X1→X2→X3→X4→… — должна остановиться на лимите."""
    # Каждый pages_for_Xn говорит «у X(n-1) коротко Xn»
    async def find_pages(hint, *args, **kwargs):
        h = hint.lower()
        # генерируем «у previous коротко current» для любого current=xN
        if h.startswith("x") and h[1:].isdigit():
            n = int(h[1:])
            if n > 1:
                return [_mk_page(f"у x{n-1} коротко x{n}")]
        return []

    with patch("core.memory._find_pages_by_hint",
               AsyncMock(side_effect=find_pages)):
        result = asyncio.run(_resolve_alias("x10"))
    # Должны успеть пройти ровно _ALIAS_DEPTH_LIMIT шагов и остановиться
    expected_n = 10 - _ALIAS_DEPTH_LIMIT
    assert result == f"x{expected_n}"


def test_alias_cycle_protection():
    """A→B→A (цикл) — должно остановиться без зависания."""
    async def find_pages(hint, *args, **kwargs):
        h = hint.lower()
        if h == "a":
            return [_mk_page("у B коротко A")]
        if h == "b":
            return [_mk_page("у A коротко B")]
        return []

    with patch("core.memory._find_pages_by_hint",
               AsyncMock(side_effect=find_pages)):
        result = asyncio.run(_resolve_alias("A"))
    # Без зависания, какое-то конечное значение в цикле
    assert result in ("a", "b")


# ── Защита от ошибок ────────────────────────────────────────────────────────


def test_find_pages_failure_returns_original():
    """Если _find_pages_by_hint бросает исключение — возвращаем исходную связь."""
    with patch("core.memory._find_pages_by_hint",
               AsyncMock(side_effect=RuntimeError("notion down"))):
        result = asyncio.run(_resolve_alias("Z"))
    assert result == "Z"


def test_dash_token_not_swallowed():
    """Имена с дефисом («Анна-Мария») должны парситься как один токен."""
    pages = [_mk_page("у Анна-Мария кличка Аня")]
    with _patched_find(pages):
        result = asyncio.run(_resolve_alias("Аня"))
    assert result == "анна-мария"


# ── Регресс save_memory: алиасов нет → ничего не меняется ────────────────────


def test_save_memory_no_alias_does_not_change_link():
    """Регресс: save_memory с category!=Лимит, без алиасных записей,
    оставляет связь и ключ как вернул Haiku. _find_pages_by_hint вернул [].
    """
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    fact = "тестовый факт"
    category = "👥 Люди"
    связь = "vasya"
    ключ = "vasya_fact"

    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, category, связь, ключ))), \
         patch("core.memory._find_pages_by_hint",
               AsyncMock(return_value=[])), \
         patch.object(cmem._mem_repo, "add", AsyncMock(return_value="page-id-1")) as mock_add:
        asyncio.run(save_memory(msg, "вася любит чай", "user-notion-uid", "☀️ Nexus"))

    mock_add.assert_awaited_once()
    # _mem_repo.add(fact, key, category, scope, related_to, source, user_notion_id)
    args = mock_add.await_args.args
    assert args[0] == fact          # fact unchanged
    assert args[1] == ключ          # key unchanged
    assert args[4] == связь         # related_to unchanged (связь = vasya)


def test_save_memory_alias_canonicalizes_link_and_key():
    """Главный кейс: existing «у X кличка Y», save_memory с Haiku возвратом
    связь=Y, ключ=Y_fact → канонизировано в связь=x, ключ=x_vitaminy.
    """
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    pages = [_mk_page("у X краткая кличка Y")]

    fact = "y нужны витамины"
    category = "🐾 Коты"
    связь = "y"
    ключ = "y_vitaminy"

    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, category, связь, ключ))), \
         patch("core.memory._find_pages_by_hint",
               AsyncMock(return_value=pages)), \
         patch.object(cmem._mem_repo, "add", AsyncMock(return_value="page-id-2")) as mock_add:
        asyncio.run(save_memory(msg, "y нужны витамины", "user-uid", "☀️ Nexus"))

    mock_add.assert_awaited_once()
    args = mock_add.await_args.args
    # related_to (pos 4) and key (pos 1) must be canonicalized
    related_to = args[4]
    key_arg = args[1]
    assert related_to == "x", f"expected 'x', got {related_to!r}"
    assert key_arg == "x_vitaminy", f"expected 'x_vitaminy', got {key_arg!r}"


def test_save_memory_limit_category_skips_resolver():
    """Регресс: для category=💰 Лимит резолвер НЕ вызывается."""
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    fact = "обязательно: 🏠 Ж*** — 25000₽/мес"
    category = "💰 Лимит"
    связь = "ж***"
    ключ = "обязательно_ж***"

    find_mock = AsyncMock(return_value=[_mk_page("у foo коротко ж***")])

    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, category, связь, ключ))), \
         patch("core.memory._find_pages_by_hint", find_mock), \
         patch.object(cmem._mem_repo, "upsert", AsyncMock(return_value=("page-id-3", False))):
        asyncio.run(save_memory(msg, "обязательный расход квартира 25000",
                                 "user-uid", "☀️ Nexus"))

    find_mock.assert_not_called(), \
        "alias resolver не должен дёргаться для category=💰 Лимит"
