"""staging (approved=true) → 🧠 Память через MemoryRepo.save_parsed.

Категория=👥 Люди, Бот=☀️ Nexus, Источник=auto, Связь=все формы имени через " | ".
.env: PEOPLE_IMPORT_USER_ID. Запуск: python3 -m tools.people_import.import_memory [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from typing import Dict, List

from tools.people_import.common import clean_aliases, env, load_staging, memory_key

from core.repos.memory_repo import MemoryRepo

CATEGORY = "👥 Люди"
BOT = "☀️ Nexus"
SOURCE = "auto"


def build(rows: List[Dict]) -> List[Dict]:
    """Склеивает aliases всех approved-записей одного имени в единую Связь."""
    aliases = defaultdict(list)  # type: Dict[str, List[str]]
    for r in rows:
        aliases[r["name"]] += r.get("aliases") or []
    out = []
    for r in rows:
        связь = " | ".join([r["name"]] + clean_aliases(r["name"], aliases[r["name"]]))
        out.append({"fact": r["fact"], "key": memory_key(r["name"], r["type"], r["fact"]), "link": связь})
    return out


async def main(dry: bool) -> None:
    rows = [r for r in load_staging() if r.get("approved") is True]
    items = build(rows)
    print("approved=%d" % len(items))
    repo, user_id = MemoryRepo(), env("PEOPLE_IMPORT_USER_ID")
    for it in items:
        print("%s  [%s]  %s" % (it["key"], it["link"], it["fact"]))
        if not dry:
            mid = await repo.save_parsed(it["fact"], CATEGORY, it["link"], it["key"], BOT,
                                         user_id=user_id, upsert=True, source=SOURCE)
            if not mid:
                print("  ⚠️ не записано")
    print("DRY-RUN, в БД ничего не писал" if dry else "Готово")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
