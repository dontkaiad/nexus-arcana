"""tests/test_self_dedup.py — дубли фактов «Я»: полярность, кластеры, приоритет ручного факта. Чистые функции, без БД."""
from __future__ import annotations

from core import self_dedup as sd


def test_polarity_splits_likes_from_dislikes():
    assert sd.polarity("любит чай") == "pos"
    assert sd.polarity("не любит чай") == "neg"
    assert sd.polarity("ненавидит шум") == "neg"
    assert sd.polarity("пьёт чай без сахара") == "neg"


def test_clusters_are_connected_components_above_threshold():
    pol = {1: "pos", 2: "pos", 3: "pos", 4: "pos", 5: "pos"}
    edges = [(1, 2, 0.9), (2, 3, 0.85), (4, 5, 0.7), (1, 3, 0.6)]
    assert sd.clusters(edges, pol, threshold=0.82) == [[1, 2, 3]]                 # 4–5 ниже порога
    assert sd.clusters(edges, pol, threshold=0.65) == [[1, 2, 3], [4, 5]]


def test_opposite_polarity_never_merges():
    pol = {1: "pos", 2: "neg"}
    assert sd.clusters([(1, 2, 0.99)], pol) == []


def test_dismissed_pair_is_not_an_edge():
    pol = {1: "pos", 2: "pos", 3: "pos"}
    edges = [(1, 2, 0.95), (2, 3, 0.95)]
    assert sd.clusters(edges, pol, dismissed={(2, 3)}) == [[1, 2]]                 # «Это разное» разрывает именно эту пару


def test_suggest_keep_prefers_manual_then_confirmed_then_mentions():
    rows = [{"id": 5, "source": "auto", "review_status": "confirmed", "mention_count": 9},
            {"id": 7, "source": "manual", "review_status": "pending", "mention_count": 1},
            {"id": 3, "source": "auto", "review_status": "pending", "mention_count": 20}]
    assert sd.suggest_keep(rows) == 7                                              # ручной — приоритетнее
    assert sd.suggest_keep(rows[:1] + rows[2:]) == 5                               # принятый важнее числа упоминаний
    assert sd.suggest_keep([{"id": 4, "source": "auto", "review_status": "pending", "mention_count": 2},
                            {"id": 2, "source": "auto", "review_status": "pending", "mention_count": 2}]) == 2
