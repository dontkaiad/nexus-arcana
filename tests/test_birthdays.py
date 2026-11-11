"""tests/test_birthdays.py — «поздравить с ДР» день в день: матчинг дат, идемпотентность, один получатель."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core import birthdays as bd

TODAY = date(2026, 11, 11)


def _arun(coro):
    """Отдельный loop: asyncio.run() сбрасывает текущий loop в None и ломает соседние тесты с get_event_loop()."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
RECIPIENT = 111


def _p(pid, name, birthday, **kw):
    base = {"id": pid, "name": name, "birthday": birthday, "birthday_year_known": True,
            "status": "confirmed", "is_self": False, "relation": None}
    base.update(kw)
    return base


def test_matches_same_day_any_year():
    assert bd.is_birthday_today(date(1980, 11, 11), TODAY)
    assert bd.is_birthday_today(date(1904, 11, 11), TODAY)        # заглушка «год неизвестен»
    assert not bd.is_birthday_today(date(1980, 11, 12), TODAY)
    assert not bd.is_birthday_today(None, TODAY)


def test_feb29_celebrated_on_feb28_in_non_leap_year():
    leap_kid = date(2000, 2, 29)
    assert bd.is_birthday_today(leap_kid, date(2026, 2, 28))
    assert not bd.is_birthday_today(leap_kid, date(2026, 3, 1))
    assert bd.is_birthday_today(leap_kid, date(2028, 2, 29))
    assert not bd.is_birthday_today(leap_kid, date(2028, 2, 28))


def test_due_people_filters_status_self_and_sorts():
    people = [
        _p(3, "Тучка", date(1990, 11, 11)),
        _p(1, "Пончик", date(1985, 11, 11)),
        _p(2, "Рыжик", date(1985, 11, 11), status="rejected"),
        _p(4, "Я", date(1985, 11, 11), is_self=True),
        _p(5, "Другой", date(1985, 5, 5)),
    ]
    assert [p["id"] for p in bd.due_people(people, TODAY)] == [1, 3]


def test_push_text_has_age_when_year_known_and_escapes():
    t = bd.push_text(_p(1, "Тучка <b>", date(1980, 11, 11), relation="семья"), TODAY)
    assert "46" in t and "&lt;b&gt;" in t and "семья" in t
    t2 = bd.push_text(_p(1, "Пончик", date(1904, 11, 11), birthday_year_known=False), TODAY)
    assert "—" not in t2.split("\n")[0]


def _run(people, existing_titles=(), recipient=RECIPIENT):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    create = AsyncMock(return_value="42")
    tasks = SimpleNamespace(
        list_all=AsyncMock(return_value=[SimpleNamespace(title=t, deadline=TODAY.isoformat()) for t in existing_titles]),
        create=create)
    cfg = SimpleNamespace(birthday_push_tg_id=recipient, nexus=SimpleNamespace(db_tasks="db"))
    with patch("core.config.config", cfg), \
         patch("core.user_manager.get_user", AsyncMock(return_value={"user_id": "u1"})), \
         patch("core.repos.pg_people_repo.list_people", return_value=people), \
         patch("nexus.repos.tasks_repo._repo", tasks):
        sent = _arun(bd.send_birthday_pushes(bot, today=TODAY))
    return sent, bot, create


def test_creates_task_and_pushes_only_to_one_recipient():
    sent, bot, create = _run([_p(1, "Пончик", date(1985, 11, 11))])
    assert sent == 1
    props = create.await_args.args[1]
    assert props["Задача"]["title"][0]["text"]["content"] == "🎂 Поздравить Пончик"
    assert props["Дедлайн"]["date"]["start"] == "2026-11-11"          # день в день, без времени
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == RECIPIENT


def test_idempotent_when_task_already_exists_today():
    sent, bot, create = _run([_p(1, "Пончик", date(1985, 11, 11))], existing_titles=["🎂 Поздравить Пончик"])
    assert sent == 0
    create.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_disabled_without_recipient():
    sent, bot, create = _run([_p(1, "Пончик", date(1985, 11, 11))], recipient=0)
    assert sent == 0
    create.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_failure_for_one_person_does_not_block_others():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[RuntimeError("boom"), None])
    tasks = SimpleNamespace(list_all=AsyncMock(return_value=[]), create=AsyncMock(return_value="1"))
    cfg = SimpleNamespace(birthday_push_tg_id=RECIPIENT, nexus=SimpleNamespace(db_tasks="db"))
    people = [_p(1, "Пончик", date(1985, 11, 11)), _p(2, "Тучка", date(1990, 11, 11))]
    with patch("core.config.config", cfg), \
         patch("core.user_manager.get_user", AsyncMock(return_value={"user_id": "u1"})), \
         patch("core.repos.pg_people_repo.list_people", return_value=people), \
         patch("nexus.repos.tasks_repo._repo", tasks):
        sent = _arun(bd.send_birthday_pushes(bot, today=TODAY))
    assert sent == 1
    assert bot.send_message.await_count == 2
