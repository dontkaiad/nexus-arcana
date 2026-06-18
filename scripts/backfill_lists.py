"""scripts/backfill_lists.py — backfill 🗒️ Списки Notion → PG (split by Бот).

All 80 existing rows are Nexus → nexus_lists.
arcana_inventory stays empty (for future Arcana barter/inventory items).

Usage:
    python3 scripts/backfill_lists.py            # dry-run
    python3 scripts/backfill_lists.py --apply    # insert into PG
    python3 scripts/backfill_lists.py --diagnose # read-only: why checklists vanished
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, ".")

NOTION_TYPE_TO_PG = {
    "🛒 Покупки": "покупки",
    "📋 Чеклист": "чеклист",
    "📦 Инвентарь": "инвентарь",
}
NOTION_STATUS_TO_PG = {
    "Not started": "not_started",
    "In progress": "in_progress",
    "Done": "done",
    "Archived": "archived",
}
NOTION_PRIORITY_TO_PG = {
    "⚪ Можно потом": "можно_потом",
    "🟡 Важно": "важно",
    "🔴 Срочно": "срочно",
}

WORK_REL_PROP = "🔮 Работы "  # trailing space — exact Notion schema name


def _extract_text(prop: dict) -> str:
    parts = prop.get("rich_text") or prop.get("title", [])
    return parts[0]["plain_text"] if parts else ""


def _extract_select(prop: dict) -> str:
    sel = prop.get("select") or {}
    return sel.get("name", "")


def _extract_status(prop: dict) -> str:
    st = prop.get("status") or {}
    return st.get("name", "")


def _extract_number(prop: dict):
    return prop.get("number")


def _extract_date(prop: dict) -> str:
    d = prop.get("date") or {}
    return (d.get("start") or "")[:10]


def _extract_relation_first(prop: dict) -> str:
    rels = prop.get("relation") or []
    return rels[0]["id"] if rels else ""


def _parse_row(page: dict) -> dict:
    props = page.get("properties", {})

    work_rel_prop = props.get(WORK_REL_PROP, {}) or props.get("🔮 Работы", {})

    notion_type = _extract_select(props.get("Тип", {}))
    notion_status = _extract_status(props.get("Статус", {}))
    notion_priority = _extract_select(props.get("Приоритет", {}))
    bot = _extract_select(props.get("Бот", {}))

    expiry_str = _extract_date(props.get("Срок годности", {}))
    expiry_date = None
    if expiry_str:
        try:
            expiry_date = date.fromisoformat(expiry_str)
        except ValueError:
            pass

    remind_raw = _extract_number(props.get("Напомнить за", {}))
    stage_raw = _extract_number(props.get("Этап", {}))
    qty_raw = _extract_number(props.get("Количество", {}))

    return {
        "notion_id": page["id"],
        "bot": bot,
        "name": _extract_text(props.get("Название", {})),
        "list_type": NOTION_TYPE_TO_PG.get(notion_type, "покупки"),
        "status": NOTION_STATUS_TO_PG.get(notion_status, "not_started"),
        "category": _extract_select(props.get("Категория", {})),
        "quantity": float(qty_raw) if qty_raw is not None else None,
        "note": _extract_text(props.get("Заметка", {})),
        "price_actual": float(_extract_number(props.get("Цена", {})) or 0) or None,
        "price_plan": float(_extract_number(props.get("Цена план", {})) or 0) or None,
        "store": _extract_text(props.get("Магазин", {})),
        "priority": NOTION_PRIORITY_TO_PG.get(notion_priority, ""),
        "group_name": _extract_text(props.get("Группа", {})),
        "is_recurring": bool(props.get("Повторяющийся", {}).get("checkbox")),
        "remind_days": int(remind_raw) if remind_raw is not None else None,
        "expires_at": expiry_date,
        "stage": int(stage_raw) if stage_raw is not None else None,
        "task_id": _extract_relation_first(props.get("✅ Задачи", {})),
        "works_id": _extract_relation_first(work_rel_prop),
        "user_notion_id": _extract_relation_first(props.get("🪪 Пользователи", {})),
    }


async def _diagnose(pages: list, engine) -> None:
    """Read-only: почему подзадачи (📋 Чеклист) пропали.

    Ничего не пишет. Сначала смотрит PG напрямую (Списки давно в PG, бот
    пишет подзадачи прямо туда) — это не зависит от Notion-токена. Если Notion
    доступен — дополнительно сверяет исходник с PG.
    """
    from core.repos.lists_table import nexus_lists, arcana_inventory
    from sqlalchemy import select as sa_select

    # ── PG-side (не требует Notion) ───────────────────────────────────────────
    with engine.connect() as conn:
        nx = conn.execute(sa_select(
            nexus_lists.c.notion_id, nexus_lists.c.list_type,
            nexus_lists.c.task_id, nexus_lists.c.works_id,
            nexus_lists.c.name, nexus_lists.c.group_name,
        )).fetchall()
        ar = conn.execute(sa_select(
            arcana_inventory.c.notion_id, arcana_inventory.c.list_type,
            arcana_inventory.c.works_id,
            arcana_inventory.c.name, arcana_inventory.c.group_name,
        )).fetchall()

    def _by_type(rows, idx=1):
        d: dict = {}
        for r in rows:
            d[r[idx] or "(пусто)"] = d.get(r[idx] or "(пусто)", 0) + 1
        return d

    print(f"[diagnose] PG rows: nexus_lists={len(nx)}, arcana_inventory={len(ar)}")
    print(f"[diagnose] nexus_lists by list_type: {_by_type(nx)}")
    print(f"[diagnose] arcana_inventory by list_type: {_by_type(ar)}")

    nx_check = [r for r in nx if r[1] == "чеклист"]
    ar_check = [r for r in ar if r[1] == "чеклист"]
    nx_check_rel = sum(1 for r in nx_check if (r[2] or r[3]))   # task_id|works_id
    ar_check_rel = sum(1 for r in ar_check if r[2])             # works_id
    print(f"[diagnose] PG checklist rows: nexus_lists={len(nx_check)} (с relation={nx_check_rel}), "
          f"arcana_inventory={len(ar_check)} (с works_id={ar_check_rel})")
    # nexus_lists checklist: (notion_id, list_type, task_id, works_id, name, group_name)
    for r in nx_check[:20]:
        print(f"    nx-check | name={(r[4] or '<EMPTY>')[:30]:30} | group={(r[5] or '-')[:20]:20} "
              f"| task={'Y' if r[2] else '-'} works={'Y' if r[3] else '-'}")
    # arcana_inventory checklist: (notion_id, list_type, works_id, name, group_name)
    for r in ar_check[:20]:
        print(f"    ar-check | name={(r[3] or '<EMPTY>')[:30]:30} | group={(r[4] or '-')[:20]:20} "
              f"| works={'Y' if r[2] else '-'}")
    if not nx_check and not ar_check:
        print("[diagnose] ⚠️ В PG НЕТ ни одной checklist-строки → подзадачи в PG отсутствуют "
              "(не доехали при бэкфилле ИЛИ пишутся не туда).")

    pg_ids = {r[0] for r in nx if r[0]} | {r[0] for r in ar if r[0]}

    # ── Notion-side (опционально; нужен валидный токен) ───────────────────────
    rows = [_parse_row(p) for p in pages]
    if not rows:
        print("[diagnose] Notion вернул 0 строк (токен невалиден / база пуста) — "
              "сверку Notion↔PG пропускаю, смотри PG-секцию выше.")
        return

    checklist = [r for r in rows if r["list_type"] == "чеклист"]
    print(f"[diagnose] Notion rows total={len(rows)}, of them 📋 Чеклист={len(checklist)}")

    by_bot: dict = {}
    no_name = []
    with_works = 0
    with_task = 0
    missing_in_pg = []
    for r in checklist:
        by_bot[r["bot"] or "(пусто)"] = by_bot.get(r["bot"] or "(пусто)", 0) + 1
        if not r["name"]:
            no_name.append(r["notion_id"])
        if r["works_id"]:
            with_works += 1
        if r["task_id"]:
            with_task += 1
        if r["notion_id"] not in pg_ids:
            missing_in_pg.append(r)

    print(f"[diagnose] checklist by Бот: {by_bot}")
    print(f"[diagnose] checklist with works_id={with_works}, with task_id={with_task}")
    print(f"[diagnose] checklist SKIP (no name)={len(no_name)}: {no_name[:10]}")
    print(f"[diagnose] checklist MISSING in both PG tables={len(missing_in_pg)} (= потерянные)")
    for r in missing_in_pg[:20]:
        print(
            f"    miss | bot={r['bot'] or '-':10} | name={(r['name'] or '<EMPTY>')[:30]:30} "
            f"| works={'Y' if r['works_id'] else '-'} task={'Y' if r['task_id'] else '-'} "
            f"| {r['notion_id']}"
        )
    if checklist and not missing_in_pg:
        print("[diagnose] все checklist-строки Notion есть в PG → корень в READ-path, не в миграции")



async def main(apply: bool, diagnose: bool = False) -> None:
    from core.notion_client import query_pages
    from core.config import config  # триггерит load_dotenv() + резолвит db_lists из .env
    from core.repos.lists_table import nexus_lists, arcana_inventory
    from sqlalchemy import select as sa_select

    db_id = config.db_lists or os.environ.get("NOTION_DB_LISTS")
    if not db_id:
        print("[backfill] ERROR: NOTION_DB_LISTS not set")
        sys.exit(1)

    pages = await query_pages(db_id, page_size=200)
    print(f"[backfill] Notion rows fetched: {len(pages)}")

    from arcana.repos.pg_sessions_repo import get_engine
    engine = get_engine()

    if diagnose:
        await _diagnose(pages, engine)
        return

    # Skip if already backfilled
    with engine.connect() as conn:
        existing_nexus = conn.execute(
            sa_select(nexus_lists.c.notion_id)
        ).fetchall()
        existing_ids = {r[0] for r in existing_nexus if r[0]}
    if existing_ids:
        print(f"[backfill] nexus_lists already has {len(existing_ids)} rows")
        if not apply:
            print("[backfill] dry-run — use --apply to re-run anyway")
            return

    nexus_rows = []
    arcana_rows = []
    skipped = []

    for page in pages:
        row = _parse_row(page)
        if not row["name"]:
            print(f"  SKIP (no name): {row['notion_id']}")
            skipped.append(row["notion_id"])
            continue
        if row["notion_id"] in existing_ids:
            skipped.append(row["notion_id"])
            continue

        bot = row.pop("bot")
        if bot == "🌒 Arcana":
            arcana_rows.append(row)
        else:
            # ☀️ Nexus or empty → nexus_lists
            nexus_rows.append(row)

    print(f"[backfill] To insert: nexus_lists={len(nexus_rows)}, arcana_inventory={len(arcana_rows)}, skip={len(skipped)}")

    if not apply:
        print("[backfill] DRY RUN — no changes. Use --apply to insert.")
        # Show first 5 for verification
        for r in nexus_rows[:5]:
            print(f"  nexus  | {r['list_type']:10} | {r['status']:12} | {r['name'][:40]}")
        for r in arcana_rows[:3]:
            print(f"  arcana | {r['list_type']:10} | {r['status']:12} | {r['name'][:40]}")
        return

    inserted_nexus = 0
    inserted_arcana = 0

    for row in nexus_rows:
        nid = row["notion_id"]
        with engine.begin() as conn:
            try:
                conn.execute(
                    nexus_lists.insert().values(
                        notion_id=nid,
                        name=row["name"],
                        list_type=row["list_type"],
                        status=row["status"],
                        category=row["category"] or "",
                        quantity=row["quantity"],
                        note=row["note"] or "",
                        price_actual=row["price_actual"],
                        price_plan=row["price_plan"],
                        store=row["store"] or "",
                        priority=row["priority"] or "",
                        group_name=row["group_name"] or "",
                        is_recurring=row["is_recurring"],
                        remind_days=row["remind_days"],
                        expires_at=row["expires_at"],
                        stage=row["stage"],
                        task_id=row["task_id"] or "",
                        works_id=row["works_id"] or "",
                        user_notion_id=row["user_notion_id"] or "",
                    )
                )
                inserted_nexus += 1
            except Exception as e:
                print(f"  ERROR inserting {nid}: {e}")

    for row in arcana_rows:
        nid = row["notion_id"]
        with engine.begin() as conn:
            try:
                conn.execute(
                    arcana_inventory.insert().values(
                        notion_id=nid,
                        name=row["name"],
                        list_type=row["list_type"],
                        status=row["status"],
                        category=row["category"] or "",
                        quantity=row["quantity"],
                        note=row["note"] or "",
                        group_name=row["group_name"] or "",
                        is_recurring=row["is_recurring"],
                        remind_days=row["remind_days"],
                        expires_at=row["expires_at"],
                        works_id=row["works_id"] or "",
                        user_notion_id=row["user_notion_id"] or "",
                    )
                )
                inserted_arcana += 1
            except Exception as e:
                print(f"  ERROR inserting {nid}: {e}")

    total_inserted = inserted_nexus + inserted_arcana
    total_expected = len(nexus_rows) + len(arcana_rows)

    print(f"[backfill] nexus_lists inserted: {inserted_nexus}")
    print(f"[backfill] arcana_inventory inserted: {inserted_arcana}")
    print(f"[backfill] total inserted: {total_inserted}")

    if total_inserted != total_expected:
        print(f"[backfill] ERROR: expected {total_expected}, got {total_inserted} — STOP")
        sys.exit(1)

    # Verify counts
    with engine.connect() as conn:
        n_nexus = conn.execute(
            sa_select(nexus_lists.c.id)
        ).fetchall()
        n_arcana = conn.execute(
            sa_select(arcana_inventory.c.id)
        ).fetchall()
    total_pg = len(n_nexus) + len(n_arcana)
    print(f"[backfill] PG verification: nexus_lists={len(n_nexus)}, arcana_inventory={len(n_arcana)}, total={total_pg}")

    if total_pg < (len(pages) - len(skipped)):
        print(f"[backfill] WARNING: PG total {total_pg} < expected {len(pages) - len(skipped)}")
    else:
        print("[backfill] OK — counts match")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    diagnose = "--diagnose" in sys.argv
    asyncio.run(main(apply, diagnose))
