"""Помощник тестов People: человек с аккаунтами (person_accounts) одной вставкой.

После PR-7 у `people` нет колонок tg_id/tg_username/vk_id — они в `person_accounts`. Тесты по-прежнему
хотят писать `mk_person("Имя", tg_id=1, tg_username="x", vk_id=2)`, поэтому разбираем эти kw здесь.
"""
from __future__ import annotations

from sqlalchemy import insert

from core.db import get_engine
from core.repos.people_tables import people, person_accounts


def insert_person(user_id: str, name: str, *, tg_id=None, tg_username=None, vk_id=None, accounts=None, **cols) -> int:
    """accounts: [{"platform": "tg", "external_id": 1, "username": "u", "is_primary": True}, …] — доп. аккаунты."""
    accs = []
    if tg_id:
        accs.append({"platform": "tg", "external_id": tg_id, "username": tg_username, "is_primary": True})
    if vk_id:
        accs.append({"platform": "vk", "external_id": vk_id, "username": None, "is_primary": True})
    accs += list(accounts or [])
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=user_id, name=name, **cols).returning(people.c.id)).scalar_one()
        for a in accs:
            c.execute(insert(person_accounts).values(
                user_id=user_id, person_id=pid, platform=a["platform"], external_id=a["external_id"],
                username=a.get("username"), is_primary=bool(a.get("is_primary", False))))
    return pid


def add_account_row(user_id: str, person_id: int, platform: str, external_id: int, username=None, primary=False):
    with get_engine().begin() as c:
        c.execute(insert(person_accounts).values(user_id=user_id, person_id=person_id, platform=platform,
                                                 external_id=external_id, username=username, is_primary=primary))
