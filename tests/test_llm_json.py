import json

import pytest

from core.llm_json import parse_llm_json


def test_plain_and_fenced():
    assert parse_llm_json('{"a": 1}') == {"a": 1}
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_around_object():
    assert parse_llm_json('Вот ответ: {"deadline": null} готово') == {"deadline": None}


@pytest.mark.parametrize("raw", ['{"title": "обрез', "", "[1, 2]", "просто текст"])
def test_bad_input_raises_jsondecodeerror(raw, caplog):
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json(raw, label="t")
