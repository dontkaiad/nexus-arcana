"""tests/test_evals_offline.py — офлайн-эвалы в CI (синтетика, без LLM и сети; Postgres как в остальных тестах) и
тесты самой обвязки эвалов. Реальные данные в репозитории не лежат: приватные кейсы — только data/evals/ (gitignore).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from evals import classifier_eval, common, grounding_eval, memory_eval
from evals.__main__ import run_all


def test_offline_evals_meet_thresholds():
    res = run_all()
    assert res["classifier"]["accuracy_pct"] == 100.0 and res["classifier"]["llm_calls"] == 0
    assert res["memory_retrieval"]["recall_at_5_pct"] == 100.0 and res["memory_retrieval"]["route_acc_pct"] == 100.0
    mr = res["memory_retrieval"]
    assert mr["hints"]["pass_pct"] == 100.0 and mr["hints"]["cases"] == 5, mr["hints"]     # «как обычно»: ловушки с отрицанием не подсказываются
    assert mr["ambiguity_acc_pct"] == 100.0                                      # имя кота = имя человека: показаны оба
    assert mr["by_group"]["people"]["questions"] == 9 and mr["by_group"]["cats"]["questions"] == 10
    for g in ("people", "cats"):                                                 # новая группа не маскирует регресс старой
        assert mr["by_group"][g]["recall_at_5_pct"] == 100.0 and mr["by_group"][g]["route_acc_pct"] == 100.0
    assert res["grounding"]["accept_rate_pct"] == 100.0 and res["grounding"]["reject_recall_pct"] == 100.0
    assert res["grounding"]["check_match_pct"] == 100.0 and res["grounding"]["wrong"] == []


def test_synthetic_files_contain_no_private_markers():
    """Публичный репо: в синтетике не должно быть реальных tg-id, телефонов, e-mail и т. п."""
    import re
    for p in (common.SYNTHETIC).glob("*"):
        if p.suffix not in (".json", ".jsonl", ".py"):
            continue
        t = p.read_text(encoding="utf-8")
        assert not re.search(r"\b\d{9,}\b", t), f"{p.name}: длинное число (похоже на tg_id / телефон)"
        assert "@" not in t.replace("@contextmanager", ""), f"{p.name}: '@' (e-mail / username)"


# ── метрики поиска ───────────────────────────────────────────────────────────

def test_recall_precision_math():
    assert memory_eval.score([1, 2, 3], [1, 2, 3, 9, 9]) == {"recall": 1.0, "precision": 0.6}
    s = memory_eval.score([1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 8])               # ожидаемых больше 5 → делим на 5
    assert s["recall"] == 1.0
    assert memory_eval.score([1, 2], [2, 7])["recall"] == 0.5
    assert memory_eval.score([], []) == {"recall": 1.0, "precision": 1.0}          # «правильно ничего не нашла»
    assert memory_eval.score([], [5])["recall"] == 0.0
    assert memory_eval.score([1], [])["recall"] == 0.0


def test_recall_is_sensitive_to_a_broken_pipeline():
    """Эвал должен ПАДАТЬ, если поиск сломан (иначе он ничего не защищает): без понимания вопроса 'что не любит тёма' → гибрид."""
    with patch.dict("os.environ", {"MEMORY_QUERY_UNDERSTANDING": "0"}):
        res = memory_eval.run_offline()
    assert res["recall_at_5_pct"] < 100.0 or res["route_acc_pct"] < 100.0


def test_cat_questions_fail_the_eval_when_cat_resolution_is_broken():
    """Эвал котов не должен быть декоративным: без резолва котов вопросы про них уходят в гибрид, а «кот = человек»
    решается в пользу человека — recall, route_acc и ambiguity_acc падают."""
    from core import memory_search as ms
    with patch.object(ms, "_resolve_cats", return_value=[]):
        res = memory_eval.run_offline()
    assert res["by_group"]["cats"]["route_acc_pct"] < 100.0
    assert res["ambiguity_acc_pct"] == 0.0
    assert res["by_group"]["people"]["recall_at_5_pct"] == 100.0                 # люди от этого не зависят


def test_ambiguity_metric_counts_only_flagged_cases():
    import asyncio
    from evals.synthetic.memory_world import CASES_CATS, SYN_USER, world
    from unittest.mock import patch as _p
    from core import memory_search as ms
    flagged = [c for c in CASES_CATS if c[3].get("ambiguous")]
    assert len(flagged) == 2 and all(c[3]["ambiguous"] == 2 for c in flagged)
    with world() as ids, _p.object(ms, "_embed", return_value=[]):
        plain = [{"query": q, "expected_ids": [ids[k] for k in exp], "route": r} for q, exp, r, ex in CASES_CATS if not ex]
        res = asyncio.run(memory_eval._run(plain, SYN_USER, rerank=False))
    assert res["ambiguity_acc_pct"] is None                                      # без помеченных вопросов метрики нет


# ── классификатор: сбор и A/B ────────────────────────────────────────────────

def test_collect_extracts_distinct_recent_messages_from_logs():
    log = io.StringIO(
        "2026-11-01 [INFO] nexus.classifier: classify: input text='купить молоко' tz_offset=3\n"
        "2026-11-01 [INFO] other: noise\n"
        "2026-11-01 [INFO] nexus.classifier: classify: input text='купить молоко' tz_offset=3\n"
        "2026-11-01 [INFO] nexus.classifier: classify: input text=\"что я не ем\" tz_offset=3\n"
        "2026-11-01 [INFO] nexus.classifier: classify: input text='ок' tz_offset=3\n")
    got = classifier_eval.collect(log, limit=10)
    assert [c["text"] for c in got] == ["купить молоко", "что я не ем"]               # дубль и слишком короткое отброшены
    assert len(classifier_eval.collect(io.StringIO("".join(
        f"classify: input text='сообщение номер {i}' x\n" for i in range(50))), limit=7)) == 7


def test_ab_summary_math_with_fake_models(monkeypatch):
    def fake(text, model, user_id=""):
        a = model == "A"
        items = [{"type": "task"}] if "задача" in text else [{"type": "note" if a else "unknown"}]
        return {"items": items, "stat": {"llm": "кэш" not in text, "ms": 100 if a else 50, "in": 1000, "cw": 0, "cr": 0, "out": 10}}

    async def afake(text, model, user_id=""):
        return fake(text, model, user_id)

    cases = [{"text": "задача 1"}, {"text": "заметка 2"}, {"text": "заметка 3"}, {"text": "кэш без llm"}]
    monkeypatch.setattr(classifier_eval, "classify_once", afake)
    s = classifier_eval.run_ab(cases, "A", "B", "", None)
    assert s["messages"] == 4 and s["reached_llm"] == 3
    assert s["agree_types_pct"] == 33.3                                              # совпало 1 из 3 дошедших до LLM
    assert s["disagreements"] == 3                                                   # заметка 2, заметка 3 и «кэш…» различаются
    assert s["latency_ms_a"]["p50"] == 100 and s["latency_ms_b"]["p50"] == 50


# ── защита приватных данных ──────────────────────────────────────────────────

def test_private_outputs_can_only_go_to_data_evals(tmp_path):
    with pytest.raises(SystemExit):
        common.dump_jsonl([{"x": 1}], str(tmp_path / "leak.jsonl"))
    ok = common.PRIVATE / "_pytest_probe.jsonl"
    try:
        assert common.dump_jsonl([{"x": 1}], str(ok)) == 1
    finally:
        ok.unlink(missing_ok=True)


def test_gitignore_covers_private_eval_dir():
    gi = (common.ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/data/evals/" in gi


# ── сбор сообщений для эвала: подробные тесты в tests/test_eval_capture.py ─────


def test_synthetic_ab_cases_cover_the_llm_path():
    """Синтетические сообщения для A/B должны идти в LLM (а не ловиться regex-префильтрами) — иначе A/B ничего не меряет."""
    import asyncio
    from unittest.mock import AsyncMock
    from core import classifier
    cases = common.load_jsonl(str(common.SYNTHETIC / "classifier_ab_cases.jsonl"))
    assert len(cases) >= 40
    called = AsyncMock(return_value='[{"type":"unknown"}]')
    reached = 0
    with patch.object(classifier, "ask_claude", called), \
         patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([], []))):
        for c in cases:
            before = called.await_count
            asyncio.run(classifier.classify(c["text"], tz_offset=3, user_id=""))
            reached += int(called.await_count > before)
    assert reached / len(cases) >= 0.7, f"только {reached}/{len(cases)} дошли до LLM"


# ── A/B коротких ролей: обвязка ──────────────────────────────────────────────

def test_role_eval_harness_compares_normalized_outputs(monkeypatch):
    import asyncio
    from evals import role_eval

    async def fn_factory(text):
        # «модель» задаётся переменной окружения роли — как в боте
        m = __import__("os").environ["MODEL_PARSE"]
        return {"title": text.upper() if m == "A" else text.upper() + " ", "confidence": "high" if m == "A" else "low"}

    monkeypatch.setattr(role_eval, "_functions", lambda: {"parse_delete": ("parse", fn_factory)})
    res = asyncio.run(role_eval.run({"parse_delete": ["один", "два"]}, "A", "B"))
    assert res["parse_delete"]["agree_pct"] == 100.0               # пробелы и «volatile»-поля не считаются расхождением
    assert res["_overall"]["n"] == 2 and res["_diffs"] == []
    assert "MODEL_PARSE" not in __import__("os").environ        # переменная роли после прогона убрана


def test_role_eval_targets_live_functions_only():
    """Эвал ролей должен гонять функции, которые бот реально вызывает (иначе цифры ничего не значат)."""
    import re
    from evals import role_eval
    names = set(role_eval._functions())
    assert {"route_intent", "parse_delete", "parse_fact", "task_category", "spell"} <= names
    src = (common.ROOT / "evals" / "role_eval.py").read_text(encoding="utf-8")
    assert not re.search(r"parse_finance|parse_task\(|analyze_message|parse_time_only", src), "мёртвые функции в эвале"
    cases = json.loads((common.SYNTHETIC / "role_cases.json").read_text(encoding="utf-8"))
    assert names <= set(cases)


def test_sonnet_sidebyside_helpers():
    from evals import sonnet_sidebyside as sbs
    assert sbs._split_cards("Шут, Маг; Жрица / Император\nСила") == ["Шут", "Маг", "Жрица", "Император", "Сила"]
    import os
    sbs._set_role("narrative", "m-x", "low")
    assert os.environ["MODEL_NARRATIVE"] == "m-x" and os.environ["THINKING_NARRATIVE"] == "low"
    sbs._set_role("narrative", "m-y")
    assert os.environ["MODEL_NARRATIVE"] == "m-y" and "THINKING_NARRATIVE" not in os.environ
    os.environ.pop("MODEL_NARRATIVE", None)
    assert [v[1] for v in sbs.VARIANTS] == ["claude-sonnet-4-6", "claude-sonnet-5-5", "claude-sonnet-5-5"]
