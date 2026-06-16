"""scripts/backfill_memories.py — backfill 🧠 Память Notion → PG.

Usage:
    python3 scripts/backfill_memories.py          # dry-run
    python3 scripts/backfill_memories.py --apply  # insert
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, ".")

_BOT_TO_SCOPE = {"☀️ Nexus": "nexus", "🌒 Arcana": "arcana"}
_SRC_TO_SOURCE = {"📝 Вручную": "manual", "🤖 Авто": "auto"}


async def main(apply: bool) -> None:
    from core.notion_client import query_pages
    from core.repos.pg_memory_repo import _add_sync, get_engine
    from core.repos.memories_table import memories
    from sqlalchemy import select, text

    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        print("[backfill] ERROR: NOTION_DB_MEMORY not set")
        sys.exit(1)

    pages = await query_pages(db_id, page_size=200)
    print(f"[backfill] Notion rows fetched: {len(pages)}")

    inserted = 0
    skipped = 0

    for page in pages:
        notion_id = page["id"]
        props = page.get("properties", {})

        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""
        if not fact:
            print(f"  SKIP (no fact): {notion_id}")
            skipped += 1
            continue

        key_parts = props.get("Ключ", {}).get("rich_text", [])
        key = key_parts[0]["plain_text"] if key_parts else ""

        cat_sel = props.get("Категория", {}).get("select") or {}
        category = cat_sel.get("name", "")

        bot_sel = props.get("Бот", {}).get("select") or {}
        bot_label = bot_sel.get("name", "")
        scope = _BOT_TO_SCOPE.get(bot_label, "global")

        src_sel = props.get("Источник", {}).get("select") or {}
        src_label = src_sel.get("name", "")
        source = _SRC_TO_SOURCE.get(src_label, "manual")

        related_parts = props.get("Связь", {}).get("rich_text", [])
        related_to = related_parts[0]["plain_text"] if related_parts else ""

        actual = props.get("Актуально", {}).get("checkbox", True)

        user_rels = props.get("🪪 Пользователи", {}).get("relation", [])
        user_notion_id = user_rels[0]["id"] if user_rels else ""

        print(
            f"  {'INSERT' if apply else 'DRY'}  {key!r:30} cat={category!r:20} "
            f"scope={scope!r} fact={fact[:35]!r}"
        )

        if apply:
            with get_engine().connect() as conn:
                existing = conn.execute(
                    select(memories.c.id).where(memories.c.notion_id == notion_id)
                ).fetchone()
            if existing:
                print(f"    already exists (id={existing[0]}), skipping")
                skipped += 1
                continue

            mid = _add_sync(fact, key, category, scope, related_to, source, user_notion_id,
                            notion_id=notion_id)
            # Если запись неактуальная — пометить is_current=False
            if not actual:
                from core.repos.pg_memory_repo import _set_current_sync
                _set_current_sync([mid], False)
            inserted += 1
        else:
            inserted += 1

    print(f"\n[backfill] Done. inserted={inserted} skipped={skipped}")

    if apply:
        with get_engine().connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM memories")).fetchone()[0]
        print(f"[backfill] PG memories count: {count}")
        expected = len(pages) - skipped
        if count >= expected:
            print(f"[backfill] ✅ PG count ({count}) matches expected ({expected})")
        else:
            print(f"[backfill] ❌ MISMATCH: PG={count} expected={expected}")
            sys.exit(1)


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("[backfill] DRY RUN — pass --apply to actually insert\n")
    asyncio.run(main(apply))
