"""scripts/backfill_notes.py — backfill nexus 💡 Заметки Notion → PG.

Usage:
    python3 scripts/backfill_notes.py          # dry-run
    python3 scripts/backfill_notes.py --apply  # actually insert
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")


async def main(apply: bool) -> None:
    from core.notion_client import query_pages
    from core.config import config
    from nexus.repos.pg_notes_repo import (
        get_engine, _ensure_lookups, _add_sync, _reload_lookups_sync,
    )
    from nexus.repos.notes_tables import notes
    from sqlalchemy import select, text

    _ensure_lookups()

    pages = await query_pages(config.nexus.db_notes, page_size=200)
    print(f"[backfill] Notion rows fetched: {len(pages)}")

    inserted = 0
    skipped = 0

    for page in pages:
        notion_id = page["id"]
        props = page.get("properties", {})

        title_parts = props.get("Заголовок", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else ""
        if not title:
            print(f"  SKIP (no title): {notion_id}")
            skipped += 1
            continue

        tags = [t["name"] for t in (props.get("Теги", {}).get("multi_select") or [])]
        date_str = ((props.get("Дата", {}).get("date") or {}).get("start", "") or "")[:10]
        user_rels = props.get("🪪 Пользователи", {}).get("relation", [])
        user_notion_id = user_rels[0]["id"] if user_rels else ""

        print(
            f"  {'INSERT' if apply else 'DRY'}  '{title[:50]}' "
            f"tags={tags} date={date_str or '-'}"
        )

        if apply:
            with get_engine().connect() as conn:
                existing = conn.execute(
                    select(notes.c.id).where(notes.c.notion_id == notion_id)
                ).fetchone()
            if existing:
                print(f"    already exists (id={existing[0]}), skipping")
                skipped += 1
                continue
            _add_sync(title, tags, date_str, user_notion_id, notion_id=notion_id)
            inserted += 1
        else:
            inserted += 1

    print(f"\n[backfill] Done. inserted={inserted} skipped={skipped}")

    if apply:
        with get_engine().connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM notes")).fetchone()[0]
        print(f"[backfill] PG notes count: {count}")
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
