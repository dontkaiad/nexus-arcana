"""tests/test_self_gender.py — перевод фактов «Я» в ж.р.: разбор ответа и проверка правки (LLM не вызывается)."""
from __future__ import annotations

import pytest

from core import self_gender as sg


def test_parse_reply_tolerates_noise():
    assert sg.parse_reply('```json\n{"12": "учила язык", "x": "нет", "7": ""}\n```') == {12: "учила язык"}
    assert sg.parse_reply("не json") == {} and sg.parse_reply("{}") == {}


@pytest.mark.parametrize("old,new,ok", [
    ("учился в гимназии", "училась в гимназии", True),
    ("считает себя нейроотличным", "считает себя нейроотличной", True),
    ("был готов уехать", "была готова уехать", True),
    ("учился в гимназии", "учился в гимназии", False),                       # без изменений
    ("учился в гимназии", "училась в школе", False),                          # слово заменено целиком
    ("переехал в 2019 году", "переехала в 2020 году", False),                 # цифры
    ("любит чай", "любит чай и кофе", False),                                  # число слов
    ("не любит жару", "терпеть не может холод", False),                       # перефраз
    ("знает, что он очень тяжелый человек", "знает, что она очень тяжелый человек", False),    # «он» — другой человек
    ("работа на удаленке", "работа на удалёнке", False),                      # только ё/е — не род
])
def test_validate_accepts_only_gender_endings(old, new, ok):
    assert sg.validate(old, new)[0] is ok


def test_prompt_lines():
    assert sg.build_prompt([(1, "а"), (2, "б\nв")]) == "1|а\n2|б в"


def test_script_has_tools_scope_and_backup():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "scripts" / "rewrite_self_gender.py").read_text(encoding="utf-8")
    assert 'setdefault("LLM_KEY_SCOPE", "tools")' in src and "memories_text_bak" in src
