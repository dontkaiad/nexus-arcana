"""tests/test_task_card_reply_persist.py — reply на карточку после рестарта,
персистентные БД в data/, явные даты побеждают Haiku на создании.

Баг (прод, 13.11): reply «напоминание 20 числа в 15» на карточку задачи
создал НОВУЮ задачу с датой 2026-12-20. Причины:
1. message_pages.db / pending_tasks.db лежали в корне репо, вне docker-volume
   (/app/data) → каждый деплой стирал маппинг карточки и last_task.
2. гейт last_task не смотрел на reply (правка могла уйти в чужую задачу).
3. handle_task_parsed брал ISO от Haiku напрямую (день месяца → след. месяц).

Без Notion/LLM/PG. Privacy: нейтральные названия, синтетические id.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import data_paths

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = 3
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone(timedelta(hours=TZ)))


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


# ── 1. пути и миграция ───────────────────────────────────────────────────────

def test_both_dbs_live_in_data_dir():
    from core import message_pages
    from nexus.handlers import tasks

    d = data_paths.data_dir()
    assert os.path.dirname(os.path.abspath(message_pages.DB_PATH)) == d
    assert os.path.dirname(os.path.abspath(tasks._PENDING_DB)) == d
    assert os.path.basename(d) == "data" or os.environ.get("DATA_DIR")


def test_migrate_noop_when_legacy_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert data_paths.migrate_legacy_db("message_pages.db", legacy_dir=str(legacy)) is None
    assert not (tmp_path / "data" / "message_pages.db").exists()


def test_migrate_moves_legacy_when_target_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "pending_tasks.db").write_bytes(b"legacy-bytes")
    out = data_paths.migrate_legacy_db("pending_tasks.db", legacy_dir=str(legacy))
    assert out == str(tmp_path / "data" / "pending_tasks.db")
    assert (tmp_path / "data" / "pending_tasks.db").read_bytes() == b"legacy-bytes"
    assert not (legacy / "pending_tasks.db").exists()


def test_migrate_never_overwrites_existing_data_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "message_pages.db").write_bytes(b"fresh-in-volume")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "message_pages.db").write_bytes(b"old-in-root")
    assert data_paths.migrate_legacy_db("message_pages.db", legacy_dir=str(legacy)) is None
    assert (tmp_path / "data" / "message_pages.db").read_bytes() == b"fresh-in-volume"
    assert (legacy / "message_pages.db").read_bytes() == b"old-in-root"  # корень тоже не тронут


def test_startup_log_reports_paths_and_data_flag(caplog):
    with caplog.at_level("INFO", logger="core.data_paths"):
        data_paths.log_persistent_dbs()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "message_pages" in text and "pending_tasks" in text
    assert text.count("in_data_dir=True") == 2
    assert os.path.abspath(data_paths.data_dir()) in text


# ── 2. reply на карточку находит задачу после «перезапуска» ──────────────────

def _run_py(code: str, data_dir: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATA_DIR": data_dir, "PYTHONPATH": REPO}
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.asyncio
async def test_reply_to_card_finds_task_after_restart(tmp_path):
    data_dir = str(tmp_path / "data")
    # «процесс 1»: бот создал карточку и зарегистрировал маппинг, затем умер (деплой)
    p1 = _run_py(
        "import asyncio; from core.message_pages import save_message_page;"
        "asyncio.run(save_message_page(111, 4242, 'pgtask-santa', 'task', 'nexus'))",
        data_dir,
    )
    assert p1.returncode == 0, p1.stderr

    # «процесс 2»: новый процесс/соединение к ТОМУ ЖЕ файлу в data/
    from core import message_pages
    from nexus.handlers.reply_update import handle_reply_update

    reply_msg = MagicMock()
    reply_msg.reply_to_message = MagicMock()
    reply_msg.reply_to_message.from_user = MagicMock(is_bot=True)
    reply_msg.reply_to_message.message_id = 4242
    reply_msg.chat = MagicMock(id=111)
    reply_msg.text = "напоминание 20 числа в 15"
    reply_msg.caption = None
    reply_msg.from_user = MagicMock(id=700000001)
    reply_msg.answer = AsyncMock()

    task = SimpleNamespace(title="доделать отчёт", reminder="", deadline="", deadline_all_day=False)
    with patch.object(message_pages, "DB_PATH", os.path.join(data_dir, "message_pages.db")), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=TZ)), \
         patch("nexus.handlers.reply_update.parse_reply",
               AsyncMock(return_value={"reminder": "2026-12-20 15:00"})), \
         patch("nexus.handlers.reply_update.apply_updates",
               AsyncMock(return_value={"Напоминание": "2026-11-20T15:00"})) as m_apply, \
         patch("nexus.handlers.reply_update.format_applied", AsyncMock(return_value="ok")), \
         patch("nexus.repos.tasks_repo._repo.retrieve_page", AsyncMock(return_value=task)), \
         patch("nexus.handlers.tasks._schedule_reminder", AsyncMock()), \
         patch("nexus.handlers.tasks._schedule_deadline_check", AsyncMock()):
        handled = await handle_reply_update(reply_msg, user_id="u")

    assert handled is True  # reply ушёл в reply_update, а не в создание новой задачи
    args = m_apply.await_args.args
    assert args[0] == "pgtask-santa" and args[1] == "task"
    assert args[3]["reminder"] == "2026-11-20 15:00"  # 20-е ТЕКУЩЕГО месяца, не 12-20


@pytest.mark.asyncio
async def test_unmapped_card_is_not_handled(tmp_path):
    """Без файла маппинга (старое поведение после деплоя) reply не находит задачу."""
    from core import message_pages
    from nexus.handlers.reply_update import handle_reply_update

    reply_msg = MagicMock()
    reply_msg.reply_to_message = MagicMock(message_id=1)
    reply_msg.chat = MagicMock(id=111)
    reply_msg.text = "напоминание 20 числа в 15"
    reply_msg.caption = None
    with patch.object(message_pages, "DB_PATH", str(tmp_path / "empty.db")):
        assert await handle_reply_update(reply_msg, user_id="u") is False


@pytest.mark.asyncio
async def test_reply_to_mapped_card_wins_over_last_task(tmp_path):
    from core import message_pages
    from nexus.nexus_bot import _is_reply_to_task_card

    db = str(tmp_path / "message_pages.db")
    with patch.object(message_pages, "DB_PATH", db):
        await message_pages.save_message_page(111, 77, "pgtask-a", "task", "nexus")
        msg = MagicMock()
        msg.chat = MagicMock(id=111)
        msg.reply_to_message = MagicMock(message_id=77)
        msg.reply_to_message.from_user = MagicMock(is_bot=True)
        with patch("core.task_reminder_msg.get_task_reminder_by_message", AsyncMock(return_value=None)):
            assert await _is_reply_to_task_card(msg) is True
            msg.reply_to_message.message_id = 78  # не карточка
            assert await _is_reply_to_task_card(msg) is False
            msg.reply_to_message = None  # не reply
            assert await _is_reply_to_task_card(msg) is False


# ── 3. создание: явные дата/время побеждают Haiku ────────────────────────────

def _ov(data, text):
    from nexus.handlers.tasks import _override_when_from_text
    _override_when_from_text(data, text, NOW)
    return data


def test_create_day_of_month_stays_in_current_month():
    d = _ov({"reminder": "2026-12-20T15:00", "deadline": None}, "напоминание 20 числа в 15")
    assert d["reminder"] == "2026-11-20T15:00"  # не 2026-12-20


def test_create_reminder_and_deadline_both_overridden():
    d = _ov(
        {"reminder": "2026-12-20T09:00", "deadline": "2026-12-27"},
        "доделать отчёт напоминание 20 числа дедлайн 27 ноября",
    )
    assert d["reminder"] == "2026-11-20T09:00"  # дата из текста, время Haiku (в тексте не названо)
    assert d["deadline"] == "2026-11-27"        # весь день: без времени


def test_create_date_placed_in_deadline_by_haiku_is_fixed():
    """«напомни <дата>»: Haiku кладёт дату в deadline (потом копируется в reminder)."""
    d = _ov({"reminder": None, "deadline": "2026-12-20T15:00"}, "напомни 20 числа в 15")
    assert d["deadline"] == "2026-11-20T15:00"


def test_create_without_explicit_when_keeps_haiku_values():
    d = _ov({"reminder": None, "deadline": "2026-11-18"}, "купить корм коту")
    assert d["deadline"] == "2026-11-18"


@pytest.mark.asyncio
async def test_handle_task_parsed_uses_override():
    from nexus.handlers import tasks

    msg = MagicMock()
    msg.from_user = MagicMock(id=900_100)
    msg.chat = MagicMock(id=900_100)
    msg.text = "доделать отчёт напоминание 20 числа в 15"
    msg.answer = AsyncMock()
    data = {
        "type": "task", "title": "доделать отчёт", "category": "💳 Прочее", "priority": "Важно",
        "deadline": None, "reminder": "2026-12-20T15:00", "repeat": "Нет",
    }
    with patch.object(tasks, "datetime", _FrozenDT), \
         patch.object(tasks, "_get_effective_tz", AsyncMock(return_value=(TZ, "UTC+3"))), \
         patch.object(tasks, "_do_save_task", AsyncMock()) as m_save:
        await tasks.handle_task_parsed(msg, data, msg.text)

    m_save.assert_awaited_once()
    saved = m_save.await_args.args[1]
    assert saved["reminder_time"] == "2026-11-20T15:00"
