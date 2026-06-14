"""scripts/backfill_tasks.py — backfill nexus ✅ Задачи Notion → PG.

Usage:
    python3 scripts/backfill_tasks.py          # dry-run (shows what would be inserted)
    python3 scripts/backfill_tasks.py --apply  # actually insert
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")


async def main(apply: bool) -> None:
    from core.notion_client import query_pages, get_page
    from core.config import config
    from nexus.repos.pg_tasks_repo import PgTasksRepo, get_engine, _ensure_lookups, _match
    from nexus.repos.pg_tasks_repo import (
        _status_id, _repeat_id, _dow_id, _priority_id, _category_id,
        _parse_iso, _extract_date,
    )
    from nexus.repos.tasks_tables import tasks
    from sqlalchemy import select, text

    _ensure_lookups()
    repo = PgTasksRepo()
    db_id = config.nexus.db_tasks

    pages = await query_pages(db_id, page_size=200)
    print(f"[backfill] Notion rows fetched: {len(pages)}")

    inserted = 0
    skipped = 0

    for page in pages:
        notion_id = page["id"]
        props = page.get("properties", {})

        title_parts = props.get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else ""
        if not title:
            print(f"  SKIP (no title): {notion_id}")
            skipped += 1
            continue

        status_raw = (props.get("Статус", {}).get("status") or {}).get("name", "Not started")
        repeat_raw = (props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        dow_raw = (props.get("День недели", {}).get("select") or {}).get("name", "")
        priority_raw = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
        category_raw = (props.get("Категория", {}).get("select") or {}).get("name", "")
        rt_parts = props.get("Время повтора", {}).get("rich_text") or []
        repeat_time = rt_parts[0]["plain_text"].strip() if rt_parts else None
        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
        reminder_raw = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
        completed_raw = (props.get("Время завершения", {}).get("date") or {}).get("start", "")
        user_relations = props.get("🪪 Пользователи", {}).get("relation", [])
        user_notion_id = user_relations[0]["id"] if user_relations else ""

        status_id = _match(_status_id, status_raw, "Not started")
        repeat_id = _match(_repeat_id, repeat_raw, "Нет") if repeat_raw and repeat_raw != "Нет" else None
        dow_id = _match(_dow_id, dow_raw) if dow_raw else None
        priority_id = _match(_priority_id, priority_raw) if priority_raw else None
        category_id = _match(_category_id, category_raw) if category_raw else None
        deadline = _parse_iso(deadline_raw)
        reminder = _parse_iso(reminder_raw)
        completed_at = _parse_iso(completed_raw)

        print(
            f"  {'INSERT' if apply else 'DRY'}  '{title}' "
            f"status={status_raw} repeat={repeat_raw} "
            f"priority={priority_raw} category={category_raw} "
            f"deadline={deadline_raw or '-'} reminder={reminder_raw or '-'}"
        )

        if apply:
            with get_engine().begin() as conn:
                existing = conn.execute(
                    select(tasks.c.id).where(tasks.c.notion_id == notion_id)
                ).fetchone()
                if existing:
                    print(f"    already exists (id={existing[0]}), skipping")
                    skipped += 1
                    continue
                result = conn.execute(
                    tasks.insert().values(
                        notion_id=notion_id,
                        title=title,
                        status_id=status_id,
                        repeat_id=repeat_id,
                        day_of_week_id=dow_id,
                        priority_id=priority_id,
                        category_id=category_id,
                        deadline=deadline,
                        reminder=reminder,
                        completed_at=completed_at,
                        repeat_time=repeat_time,
                        user_notion_id=user_notion_id,
                    ).returning(tasks.c.id)
                )
                new_id = result.fetchone()[0]
                print(f"    inserted id={new_id}")
            inserted += 1
        else:
            inserted += 1  # count as "would insert"

    print(f"\n[backfill] Done. inserted={inserted} skipped={skipped}")

    if apply:
        with get_engine().connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM tasks")).fetchone()[0]
        print(f"[backfill] PG tasks count: {count}")
        notion_count = len(pages) - (skipped if not apply else 0)
        if count >= len(pages) - skipped:
            print(f"[backfill] ✅ PG count ({count}) matches expected ({len(pages) - skipped})")
        else:
            print(f"[backfill] ❌ MISMATCH: PG={count} expected={len(pages) - skipped}")
            sys.exit(1)


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("[backfill] DRY RUN — pass --apply to actually insert\n")
    asyncio.run(main(apply))
