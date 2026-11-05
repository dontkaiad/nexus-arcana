"""tests/test_self_dedup.py — дубли фактов «Я»: полярность, кластеры, приоритет ручного факта. Чистые функции, без БД."""
from __future__ import annotations

from core import self_dedup as sd


def test_polarity_splits_likes_from_dislikes():
    assert sd.polarity("любит чай") == "pos"
    assert sd.polarity("не любит чай") == "neg"
    assert sd.polarity("ненавидит шум") == "neg"
    assert sd.polarity("пьёт чай без сахара") == "neg"


def test_clusters_need_every_member_close_to_every_other():
    pol = {1: "pos", 2: "pos", 3: "pos", 4: "pos", 5: "pos"}
    # 1–2 и 2–3 близки, а 1–3 нет: цепочка не должна склеивать 1 и 3 (иначе сотни разных фактов сольются в одну «группу»)
    edges = [(1, 2, 0.95), (2, 3, 0.93), (1, 3, 0.6), (4, 5, 0.91)]
    got = sd.clusters(edges, pol, threshold=0.9)
    assert [4, 5] in got and len(got) == 2 and sorted(x for g in got for x in g if x in (1, 2, 3)) in ([1, 2], [2, 3])


def test_triangle_forms_one_group():
    pol = {1: "pos", 2: "pos", 3: "pos"}
    assert sd.clusters([(1, 2, 0.95), (2, 3, 0.93), (1, 3, 0.92)], pol, threshold=0.9) == [[1, 2, 3]]
    assert sd.clusters([(1, 2, 0.95), (2, 3, 0.93), (1, 3, 0.92)], pol, threshold=0.94) == [[1, 2]]


def test_opposite_polarity_never_merges():
    pol = {1: "pos", 2: "neg"}
    assert sd.clusters([(1, 2, 0.99)], pol) == []


def test_dismissed_pair_is_not_an_edge():
    pol = {1: "pos", 2: "pos", 3: "pos"}
    edges = [(1, 2, 0.95), (2, 3, 0.95)]
    assert sd.clusters(edges, pol, dismissed={(2, 3)}, threshold=0.9) == [[1, 2]]  # «Это разное» разрывает именно эту пару


def test_suggest_keep_prefers_manual_then_confirmed_then_mentions():
    rows = [{"id": 5, "source": "auto", "review_status": "confirmed", "mention_count": 9},
            {"id": 7, "source": "manual", "review_status": "pending", "mention_count": 1},
            {"id": 3, "source": "auto", "review_status": "pending", "mention_count": 20}]
    assert sd.suggest_keep(rows) == 7                                              # ручной — приоритетнее
    assert sd.suggest_keep(rows[:1] + rows[2:]) == 5                               # принятый важнее числа упоминаний
    assert sd.suggest_keep([{"id": 4, "source": "auto", "review_status": "pending", "mention_count": 2},
                            {"id": 2, "source": "auto", "review_status": "pending", "mention_count": 2}]) == 2
