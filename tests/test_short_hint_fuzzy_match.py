"""tests/test_short_hint_fuzzy_match.py — issue #94 and its Nexus twins.

Bug class: fuzzy-match hint parsers dropped words of length <=2 before
scoring. Stop-words (prepositions like "в"/"к"/"на") are already filtered
by name, so the extra length filter only ever throws out legitimate short
content words — a 2-letter nickname ("лу"), a brand/system abbreviation
("1С") — leaving an empty word set, score=0 for every candidate, and a
false "not found" even on an exact match.

- arcana/handlers/works.py:handle_work_done (tracked as #94)
- nexus/handlers/tasks.py:_hint_words (shared by handle_task_done and
  handle_edit_record) — same anti-pattern, not separately filed
- nexus/handlers/tasks.py:handle_task_cancel — duplicates the same inline
  filter instead of calling _hint_words
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── nexus/handlers/tasks.py: _hint_words (shared helper) ──────────────────────

def test_hint_words_keeps_two_letter_content_words():
    from nexus.handlers.tasks import _hint_words
    assert _hint_words("лу") == {"лу"}
    assert _hint_words("1С") == {"1с"}


def test_hint_words_still_drops_stop_words_and_single_letters():
    from nexus.handlers.tasks import _hint_words
    # "я"/"в"/"на"/"к" остаются в стоп-листе — >=2 не открывает им дорогу
    assert _hint_words("я в") == set()
    assert _hint_words("уже сделала") == set()


@pytest.mark.asyncio
async def test_handle_task_done_finds_exact_two_letter_hint(mock_message):
    """Repro of the #94-class bug in Nexus: a 2-letter hint that exactly
    matches a task title used to score 0 for every candidate → false
    'не нашёл', identical symptom to works.py's handle_work_done."""
    import nexus.handlers.tasks as tasks_mod
    from nexus.repos.pg_tasks_repo import Task

    msg = mock_message(text="лу готово")
    task = Task(id="t1", title="написать лу", repeat="Нет")

    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod._repo, "set_status", AsyncMock(return_value=True)), \
         patch.object(tasks_mod, "_remove_task_jobs", MagicMock()), \
         patch.object(tasks_mod, "_update_streak_line", AsyncMock(return_value="")):
        await tasks_mod.handle_task_done(msg, "лу", user_notion_id="")

    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert "не нашёл" not in reply.lower()
    assert "написать лу" in reply


@pytest.mark.asyncio
async def test_handle_task_cancel_finds_exact_two_letter_hint(mock_message):
    import nexus.handlers.tasks as tasks_mod
    from nexus.repos.pg_tasks_repo import Task

    msg = mock_message(text="отмени лу")
    task = Task(id="t1", title="написать лу", repeat="Нет")

    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod._repo, "set_archived", AsyncMock(return_value=True)), \
         patch.object(tasks_mod, "_scheduler", None):
        await tasks_mod.handle_task_cancel(msg, "лу", user_notion_id="")

    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert "не нашёл" not in reply.lower()
    assert "написать лу" in reply


# ── arcana/handlers/works.py: handle_work_done (#94) ───────────────────────────

@pytest.mark.asyncio
async def test_handle_work_done_finds_exact_two_letter_hint():
    import arcana.handlers.works as works_mod
    from arcana.repos.works_repo import Work

    msg = AsyncMock()
    msg.answer = AsyncMock()
    item = Work(id="w1", title="сделала 1С", priority="Важно",
                deadline_str="", category_str="", has_client=False)

    with patch.object(works_mod, "ask_claude", AsyncMock(return_value="1С")), \
         patch.object(works_mod._repo, "list_open", AsyncMock(return_value=[item])), \
         patch.object(works_mod._repo, "mark_done", AsyncMock(return_value=True)):
        await works_mod.handle_work_done(msg, "сделала 1С", user_notion_id="")

    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert "не нашла" not in reply.lower()
    assert "1С" in reply
