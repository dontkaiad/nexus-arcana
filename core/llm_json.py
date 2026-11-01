"""core/llm_json.py — толерантный разбор JSON-ответа LLM (общий для ботов).

Терпит ```-ограждения и текст вокруг объекта (как core/router._parse_json),
но, в отличие от него, не глотает ошибку молча: при неудаче логирует хвост
сырого ответа и бросает json.JSONDecodeError — вызывающий сам решает fallback.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("core.llm_json")

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_llm_json(raw: str, *, label: str = "") -> dict:
    """Вернуть dict из ответа LLM или бросить json.JSONDecodeError."""
    text = (raw or "").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = _OBJ_RE.search(text)
        try:
            if not m:
                raise
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            logger.warning("llm json parse failed%s: len=%d tail=%r",
                           f" [{label}]" if label else "", len(text), text[-120:])
            raise e
    if not isinstance(parsed, dict):
        logger.warning("llm json is not an object%s: %s",
                       f" [{label}]" if label else "", type(parsed).__name__)
        raise json.JSONDecodeError("expected a JSON object", text, 0)
    return parsed
