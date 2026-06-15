"""scripts/backfill_identity.py — бэкфилл 🪪 Пользователи Notion → core_identity PG.

N=2 строки на момент создания. Скрипт фетчит их из Notion и пишет в PG.
Идемпотентен: upsert по notion_id PRIMARY KEY.

Запуск: python3 scripts/backfill_identity.py [--apply]
Без --apply: показывает записи, не пишет в PG.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import httpx

APPLY = "--apply" in sys.argv
TOKEN = os.environ.get("NOTION_TOKEN", "")
DB_USERS = os.environ.get("NOTION_DB_USERS", "")


async def fetch_users():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api.notion.com/v1/databases/{DB_USERS}/query",
            headers=headers, json={"page_size": 100},
        )
        data = r.json()
        if r.status_code != 200:
            print(f"ERROR {r.status_code}: {data.get('message')}")
            return []
        for p in data.get("results", []):
            props = p.get("properties", {})
            name_items = props.get("Имя", {}).get("title", [])
            name = name_items[0].get("plain_text", "") if name_items else ""
            tg_id = props.get("TG ID", {}).get("number") or 0
            role = (props.get("Роль", {}).get("select") or {}).get("name", "Тест")
            perm_nexus = props.get("☀️ Nexus", {}).get("checkbox", False)
            perm_arcana = props.get("🌒 Arcana", {}).get("checkbox", False)
            perm_finance = props.get("💰 Финансы", {}).get("checkbox", False)
            rows.append({
                "notion_id": p["id"],
                "tg_id": int(tg_id),
                "name": name,
                "role": role,
                "perm_nexus": perm_nexus,
                "perm_arcana": perm_arcana,
                "perm_finance": perm_finance,
            })
    return rows


async def main():
    rows = await fetch_users()
    N = len(rows)
    print(f"N (Notion Пользователи) = {N}")
    for r in rows:
        print(f"  notion_id={r['notion_id']}, tg_id={r['tg_id']}, name={r['name']!r}, "
              f"role={r['role']!r}, nexus={r['perm_nexus']}, arcana={r['perm_arcana']}, "
              f"finance={r['perm_finance']}")

    if N == 0:
        print("Nothing to backfill.")
        return

    if not APPLY:
        print("\nDry run. Pass --apply to insert into core_identity.")
        return

    from core.repos.pg_identity_repo import PgIdentityRepo
    repo = PgIdentityRepo()
    for r in rows:
        await repo.upsert(**r)
        print(f"  upserted: tg_id={r['tg_id']} name={r['name']!r}")

    # Verify
    all_pg = await repo.get_all()
    print(f"\nPG count: {len(all_pg)}")
    if len(all_pg) >= N:
        print(f"✅ count(core_identity)={len(all_pg)} >= N={N}")
    else:
        print(f"❌ MISMATCH: PG={len(all_pg)} < N={N}")


asyncio.run(main())
