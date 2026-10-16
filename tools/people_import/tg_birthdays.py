"""ДР из Telegram: контакты + диалоги → GetFullUserRequest → birthday → staging.

.env: TG_API_ID (или API_ID), TG_API_HASH (или API_HASH).
Первый запуск спросит телефон/код; сессия — data/tg_people.session (в .gitignore).
Запуск: python3 -m tools.people_import.tg_birthdays
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import User

from tools.people_import.common import DATA_DIR, append_staging, birthday_fact, env


def _name(u: User) -> str:
    return " ".join(p for p in (u.first_name, u.last_name) if p).strip()


async def main() -> None:
    client = TelegramClient(
        str(DATA_DIR / "tg_people"), int(env("TG_API_ID", "API_ID")), env("TG_API_HASH", "API_HASH"),
    )
    async with client:
        users = {}
        res = await client(GetContactsRequest(hash=0))
        for u in res.users:
            users[u.id] = u
        async for d in client.iter_dialogs():
            if d.is_user and isinstance(d.entity, User):
                users.setdefault(d.entity.id, d.entity)

        records = []
        for u in users.values():
            if u.bot or u.is_self or u.deleted or not _name(u):
                continue
            try:
                full = await client(GetFullUserRequest(u))
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
                full = await client(GetFullUserRequest(u))
            except Exception:
                continue
            b = getattr(full.full_user, "birthday", None)
            if not b:
                continue
            aliases = [u.first_name or "", u.last_name or ""]
            if u.username:
                aliases.append("@" + u.username)
            records.append({
                "name": _name(u), "aliases": aliases, "type": "birthday",
                "fact": birthday_fact(b.day, b.month, getattr(b, "year", None)),
                "quote": "", "source": "tg",
            })
            await asyncio.sleep(0.3)
        print("TG: людей=%d, с ДР=%d, добавлено в staging=%d" % (len(users), len(records), append_staging(records)))


if __name__ == "__main__":
    asyncio.run(main())
