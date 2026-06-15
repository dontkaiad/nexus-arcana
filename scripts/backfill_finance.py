"""scripts/backfill_finance.py — бэкфилл 💰 Финансы Notion → PG.

N=0 в Notion на момент создания (база пустая), но скрипт сохранён
на случай если строки будут добавлены до полного cutover.

Запуск: python3 scripts/backfill_finance.py [--apply]
Без --apply: только показывает count.
С --apply: копирует строки в nexus_budget / arcana_pnl.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load env
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
DB_FIN = os.environ.get("NOTION_DB_FINANCE", "")


async def fetch_all():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    pages = []
    has_more = True
    cursor = None
    async with httpx.AsyncClient(timeout=30) as client:
        while has_more:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            r = await client.post(
                f"https://api.notion.com/v1/databases/{DB_FIN}/query",
                headers=headers, json=payload,
            )
            data = r.json()
            if r.status_code != 200:
                print(f"ERROR {r.status_code}: {data.get('message')}")
                break
            results = data.get("results", [])
            pages.extend(results)
            has_more = data.get("has_more", False)
            cursor = data.get("next_cursor")
    return pages


async def main():
    pages = await fetch_all()
    print(f"N (Notion Финансы) = {len(pages)}")

    nexus_rows = []
    arcana_rows = []

    for p in pages:
        props = p.get("properties", {})
        bot_name = ((props.get("Бот") or {}).get("select") or {}).get("name", "")
        description_parts = (props.get("Описание") or {}).get("title") or []
        description = "".join(t.get("plain_text", "") for t in description_parts)
        amount = ((props.get("Сумма") or {}).get("number")) or 0
        category = ((props.get("Категория") or {}).get("select") or {}).get("name", "")
        type_ = ((props.get("Тип") or {}).get("select") or {}).get("name", "")
        source = ((props.get("Источник") or {}).get("select") or {}).get("name", "")
        date_val = (((props.get("Дата") or {}).get("date")) or {}).get("start", "") or ""
        user_rel = ((props.get("🪪 Пользователи") or {}).get("relation") or [{}])
        user_notion_id = user_rel[0].get("id", "") if user_rel else ""

        row = {
            "description": description,
            "amount": float(amount),
            "category": category,
            "type_": type_,
            "source": source,
            "date_iso": date_val[:10] if date_val else "",
            "user_notion_id": user_notion_id,
        }

        if "Arcana" in bot_name or "🌒" in bot_name:
            arcana_rows.append(row)
        else:
            # GUARD: barter source in nexus → sanitise
            if source == "🔄 Бартер":
                print(f"  GUARD: sanitising barter source for nexus row: {description!r}")
                row["source"] = "💳 Карта"
            nexus_rows.append(row)

    print(f"  → nexus_budget: {len(nexus_rows)}")
    print(f"  → arcana_pnl:   {len(arcana_rows)}")
    print(f"  SUM = {len(nexus_rows) + len(arcana_rows)} (must == N)")
    assert len(nexus_rows) + len(arcana_rows) == len(pages), "COUNT MISMATCH — STOP"

    if len(pages) == 0:
        print("Nothing to backfill.")
        return

    if not APPLY:
        print("\nDry run. Pass --apply to insert.")
        return

    from core.repos.pg_finance_repo import PgNexusBudgetRepo, PgArcanaPnlRepo
    nr = PgNexusBudgetRepo()
    ar = PgArcanaPnlRepo()

    inserted_n = inserted_a = 0
    for row in nexus_rows:
        await nr.add_entry(**row)
        inserted_n += 1
    for row in arcana_rows:
        await ar.add_entry(**row)
        inserted_a += 1

    print(f"\nInserted nexus_budget: {inserted_n}")
    print(f"Inserted arcana_pnl:   {inserted_a}")
    print(f"Total inserted: {inserted_n + inserted_a}")

    # Verify
    from core.repos.pg_finance_repo import _get_engine
    from sqlalchemy import text as _text
    with _get_engine().connect() as conn:
        n = conn.execute(_text("SELECT COUNT(*) FROM nexus_budget")).scalar()
        a = conn.execute(_text("SELECT COUNT(*) FROM arcana_pnl")).scalar()
    print(f"\nPG counts: nexus_budget={n}, arcana_pnl={a}, sum={n+a}")
    if n + a == len(pages):
        print("✅ count(nexus_budget) + count(arcana_pnl) == N")
    else:
        print(f"❌ MISMATCH: {n+a} != {len(pages)}")


asyncio.run(main())
