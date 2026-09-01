"""tests/test_task_clarify_category.py — категория в clarify-ветке задач
(`handle_last_task_clarify`, 5-минутное окно после создания).

Баг (со скриншота): «категория прочее или какие там есть (это доставка
посылки)» — примитивный substring-цикл `if cat_raw.lower() in tc.lower()`
не находил совпадения (длинная строка не входит в «💳 прочее»), брал
`real_cat = cat_raw` (всё предложение) и писал его в Notion/PG как
категорию + отвечал успехом.

Фикс: тот же механизм, что в `handle_edit_record` («измени категорию на X») —
`_match_code` → `resolve_task_category`, с уточняющим вопросом и БЕЗ записи,
если реального совпадения нет.

Privacy: синтетические id, generic тексты.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _msg(uid: int, text: str):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.chat = MagicMock()
    m.chat.id = uid
    m.text = text
    sent = MagicMock()
    sent.message_id = 555_001
    m.answer = AsyncMock(return_value=sent)
    return m, sent


def _patches(tasks, capture):
    from nexus.repos import pg_tasks_repo
    return [
        patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)),
        patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture)),
        patch.object(tasks._repo, "retrieve_page", AsyncMock(return_value=None)),
        patch.object(tasks, "_scheduler", None),
        patch.object(tasks, "save_task_reminder", AsyncMock()),
        patch.object(pg_tasks_repo, "_ensure_lookups", MagicMock()),
        patch.object(pg_tasks_repo, "_category_id", {}, create=False),
    ]


@pytest.mark.asyncio
async def test_screenshot_bug_matches_prochee_not_whole_sentence():
    from nexus.handlers import tasks

    uid = 880_101
    tasks._last_task_set(uid, "task-cat-1")
    calls = []

    async def capture(pid, props):
        calls.append((pid, props))

    try:
        with _stack(_patches(tasks, capture)):
            msg, _ = _msg(uid, "категория прочее или какие там есть (это доставка посылки)")
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is True
        assert calls, "set_props должен быть вызван — категория распозналась"
        _, props = calls[-1]
        assert props["Категория"]["select"]["name"] == "💳 Прочее"
        # ни в коем случае не всё предложение
        assert "доставка посылки" not in props["Категория"]["select"]["name"]
    finally:
        tasks._last_task_del(uid)


@pytest.mark.asyncio
async def test_clean_category_value_matches():
    from nexus.handlers import tasks

    uid = 880_102
    tasks._last_task_set(uid, "task-cat-2")
    calls = []

    async def capture(pid, props):
        calls.append((pid, props))

    try:
        with _stack(_patches(tasks, capture)):
            msg, _ = _msg(uid, "категория коты")
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is True
        _, props = calls[-1]
        assert props["Категория"]["select"]["name"] == "🐾 Коты"
    finally:
        tasks._last_task_del(uid)


@pytest.mark.asyncio
async def test_unknown_category_asks_and_writes_nothing():
    from nexus.handlers import tasks

    uid = 880_103
    tasks._last_task_set(uid, "task-cat-3")
    calls = []

    async def capture(pid, props):
        calls.append((pid, props))

    try:
        with _stack(_patches(tasks, capture)):
            msg, _ = _msg(uid, "категория квантовая криптография блокчейн")
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is True  # сообщение обработано (задан вопрос)
        assert not calls, "в Notion/PG ничего писать нельзя при непонятой категории"
        answers = [c.args[0] for c in msg.answer.await_args_list]
        assert any("Не нашла" in a for a in answers)
        # окно уточнения не закрыли — можно ответить снова
        assert tasks._last_task_get(uid) == "task-cat-3"
    finally:
        tasks._last_task_del(uid)


# ── helper: контекст-менеджер поверх списка патчей ──────────────────────────

import contextlib


@contextlib.contextmanager
def _stack(patchers):
    started = [p.start() for p in patchers]
    try:
        yield started
    finally:
        for p in patchers:
            p.stop()
