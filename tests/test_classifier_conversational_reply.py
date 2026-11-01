"""Классификатор: ответ модели обычным текстом (без JSON) на реплику без команды — это unknown, а не parse_error."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core import classifier


def _classify(raw: str, text: str = "спасибо"):
    log = AsyncMock()
    with patch.object(classifier, "ask_claude", AsyncMock(return_value=raw)), \
         patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([], []))), \
         patch.object(classifier, "log_error", log):
        return asyncio.run(classifier.classify(text, tz_offset=3, user_id="")), log


@pytest.mark.parametrize("raw", ["Пожалуйста! Если появятся ещё сообщения — присылай.", "Понял, хорошего дня! 🌙"])
def test_conversational_reply_is_unknown_and_not_logged_as_error(raw):
    items, log = _classify(raw)
    assert items == [{"type": "unknown"}]
    log.assert_not_awaited()


def test_broken_json_is_still_parse_error():
    items, log = _classify('[{"type": "task", "title": ')
    assert items == [{"type": "parse_error"}]
    log.assert_awaited_once()


def test_valid_json_unchanged():
    items, _ = _classify('[{"type":"note","text":"x"}]')
    assert items[0]["type"] == "note"
