"""tests/test_care_block_ui.py — блок «последний раз» в Mini App: чистые функции форматирования (в настоящем node) и подключение в списке котов."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "miniapp" / "frontend" / "src" / "cats"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node не установлен")


def run(expr: str, payload):
    code = "import * as m from %s; const p = JSON.parse(process.argv[1]); console.log(JSON.stringify(%s))" % (json.dumps((SRC / "careBlock.js").as_uri()), expr)
    r = subprocess.run([NODE, "--input-type=module", "-e", code, json.dumps(payload)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# «сейчас» — 9 ноября 2026, 19:30 по Москве (16:30 UTC)
NOW = "2026-11-09T16:30:00Z"


def fmt(iso, now=NOW):
    return run("m.formatLastDone(p.iso, new Date(p.now))", {"iso": iso, "now": now})


@needs_node
@pytest.mark.parametrize("iso,expected", [
    ("2026-11-09T13:13:00Z", "сегодня 16:13"),            # 16:13 МСК
    ("2026-11-09T00:00:00Z", "сегодня 03:00"),
    ("2026-11-08T21:00:00Z", "сегодня 00:00"),            # ровно полночь МСК: уже сегодня
    ("2026-11-08T20:59:00Z", "вчера 23:59"),
    ("2026-11-08T15:50:46Z", "вчера 18:50"),
    ("2026-11-07T21:00:00Z", "вчера 00:00"),
    ("2026-11-07T20:59:00Z", "07.11 23:59"),
    ("2026-11-05T09:05:00Z", "05.11 12:05"),
    ("2025-06-01T09:00:00Z", "01.06.2025 12:00"),         # другой год: с годом
    ("2025-12-31T21:30:00Z", "01.01 00:30"),             # московский Новый год уже в этом году
    ("2026-11-09T18:00:00Z", "сегодня 21:00"),            # в будущем относительно «сейчас» (часы расходятся): не ломаемся
    ("2026-11-09T13:13:23.123456+00:00", "сегодня 16:13"),
    ("2026-11-09T16:13:00+03:00", "сегодня 16:13"),       # со смещением
])
def test_format_last_done_in_moscow_time(iso, expected):
    assert fmt(iso) == expected


@needs_node
@pytest.mark.parametrize("iso", [None, "", "не время", "2026-13-45T00:00:00Z", 12345, {}, []])
def test_format_last_done_is_null_for_missing_or_garbage(iso):
    assert fmt(iso) is None


@needs_node
def test_moscow_day_does_not_depend_on_the_viewers_timezone():
    # то же мгновение при «сейчас» 22:30 UTC (01:30 МСК следующего дня): вчера
    assert fmt("2026-11-09T13:13:00Z", now="2026-11-09T22:30:00Z") == "вчера 16:13"


@needs_node
def test_care_line_formats_and_hides():
    now = NOW
    cases = [({"label": "лотки", "last_done_at": "2026-11-09T13:13:00Z", "last_done_by": "owner"}, "лотки · сегодня 16:13"),
             ({"label": "вода", "last_done_at": None, "last_done_by": None}, "вода · ещё не отмечено"),
             ({"label": "вода", "last_done_at": "мусор"}, "вода · ещё не отмечено"),
             ({"label": "  лотки  ", "last_done_at": "2026-11-08T15:50:46Z"}, "лотки · вчера 18:50"),
             ({"label": "", "last_done_at": None}, None), ({"label": "   "}, None), ({}, None), (None, None), ({"label": 5}, None)]
    for entry, expected in cases:
        assert run("m.careLine(p.e, new Date(p.now))", {"e": entry, "now": now}) == expected, entry


@needs_node
def test_care_lines_skip_bad_entries_and_tolerate_a_missing_key():
    care = [{"label": "лотки", "last_done_at": "2026-11-09T13:13:00Z"}, {"label": ""}, None, {"label": "вода"}]
    assert run("m.careLines(p.c, new Date(p.now))", {"c": care, "now": NOW}) == ["лотки · сегодня 16:13", "вода · ещё не отмечено"]
    for missing in (None, "нет", {}, 5):
        assert run("m.careLines(p.c, new Date(p.now))", {"c": missing, "now": NOW}) == []


def test_the_block_is_wired_above_the_cat_list_and_hidden_without_care():
    src = (SRC / "CatsTab.jsx").read_text(encoding="utf-8")
    assert "from './careBlock'" in src and "careLines(data?.care).length > 0" in src
    assert src.index("careLines(data.care)") < src.index("(data?.cats || []).map")             # над списком котов
    assert "dangerouslySetInnerHTML" not in src                                                 # текст блока выводится как текст


def test_careblock_module_has_no_dom_or_network_access():
    code = (SRC / "careBlock.js").read_text(encoding="utf-8")
    for bad in ("document.", "window.", "fetch(", "import ", "localStorage"):
        assert bad not in code
