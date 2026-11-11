"""tests/test_cat_block_eval.py — офлайн-эвал блока факта про кота: набор честный, метрики считаются, «ложное открыто» поймано."""
from __future__ import annotations

import json

from core import cat_blocks
from evals import cat_block_eval as ev
from evals.common import SYNTHETIC, load_jsonl


def _cases():
    return load_jsonl(str(SYNTHETIC / "cat_block_cases.jsonl"))


def test_dataset_covers_every_block_with_unique_ids_and_fictional_cats_only():
    cases = _cases()
    assert 15 <= len(cases) <= 20 and len({c["id"] for c in cases}) == len(cases)
    assert {c["expect"] for c in cases} == set(cat_blocks.BLOCKS)
    assert {n for c in cases for n in ev.CATS if n in c["text"]} == set(ev.CATS)     # коты набора — вымышленные из ev.CATS


def test_offline_has_no_false_open_and_only_the_known_miss():
    out = ev.run_offline()
    assert out["false_open"] == [] and out["missed_open"] == []
    assert [w["id"] for w in out["wrong"]] == ["health-symptom"]            # нет ответа модели → unsorted (закрыто), а не угадывание
    assert out["audience_acc_pct"] == 100.0 and out["before_missed_open"] == out["sitter_cases"] == 12


def test_gate_off_message_is_unsorted_whatever_the_model_said():
    case = {"id": "x", "text": "royal canin indoor 2кг", "expect": "unsorted"}
    assert ev.got_block(case, "care") == "unsorted"


def test_a_wrongly_open_answer_is_reported_as_false_open():
    rows = [{"case": {"id": "personal", "text": "ветеринар обошёлся в 12 тысяч", "expect": "owner"}, "got": "care"}]
    assert ev.summarize(rows)["false_open"] == ["personal"]
    rows = [{"case": {"id": "crit", "text": "нельзя курицу", "expect": "health"}, "got": "owner"}]
    out = ev.summarize(rows)
    assert out["false_open"] == [] and out["missed_open"] == ["crit"]


def test_whole_dataset_runs_in_the_offline_bundle():
    from evals.__main__ import run_all
    assert "cat_block" in run_all() and json.dumps(run_all()["cat_block"])
