"""tests/test_intent_arcana.py — intent split в ROUTER_SYSTEM."""
from arcana.handlers.base import ROUTER_SYSTEM


def test_router_lists_split_intents():
    s = ROUTER_SYSTEM
    for it in (
        "session_done", "session_planned",
        "ritual_done", "ritual_planned", "ritual_ambiguous",
    ):
        assert it in s, f"intent missing: {it}"


def test_router_explains_planned_vs_done_for_rituals():
    """Промпт должен явно показывать, что глагол прошедшего времени → done."""
    s = ROUTER_SYSTEM
    assert "сделала" in s
    assert "провела" in s
    assert "запланировать" in s


def test_router_documents_ambiguous_case():
    """Промпт описывает кейс неоднозначности — без глагола времени, без структуры."""
    s = ROUTER_SYSTEM
    assert "неоднозначно" in s.lower() or "ambiguous" in s.lower() \
        or "ritual_ambiguous" in s
    assert "переспросить" in s or "переспрос" in s


def test_dispatch_includes_planned_and_done():
    """В route_message dispatch должны быть entry для planned и done."""
    import inspect
    from arcana.handlers import base
    src = inspect.getsource(base)
    assert '"session_planned"' in src
    assert '"session_done"' in src
    assert '"ritual_planned"' in src
    assert '"ritual_done"' in src
