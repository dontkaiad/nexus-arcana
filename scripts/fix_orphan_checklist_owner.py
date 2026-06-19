"""scripts/fix_orphan_checklist_owner.py — проставить владельца осиротевшим
подзадачам в nexus_lists.

Контекст: при Notion→PG бэкфилле checklist-строки приехали с пустым
user_notion_id (в Notion у них не было relation на Пользователей). Read-path
Mini App фильтрует списки по user_notion_id → подзадачи невидимы в шите задачи
и во вкладке Чеклист, хотя в PG лежат.

Фикс: nexus_lists, list_type='чеклист', пустой user_notion_id → проставить
владельца (по умолчанию — доминирующий user_notion_id среди непустых строк
nexus_lists; можно переопределить через NEXUS_FIX_OWNER).

Usage:
    python3 scripts/fix_orphan_checklist_owner.py          # dry-run
    python3 scripts/fix_orphan_checklist_owner.py --apply   # UPDATE
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")


def _short(s: str) -> str:
    return (s or "")[:8] + "…" if s else "(пусто)"


def main(apply: bool) -> None:
    from core.config import config  # триггерит load_dotenv()  # noqa: F401
    from core.repos.lists_table import nexus_lists
    from arcana.repos.pg_sessions_repo import get_engine
    from sqlalchemy import select as sa_select, update as sa_update, or_

    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(sa_select(
            nexus_lists.c.list_type, nexus_lists.c.user_notion_id,
        )).fetchall()

    # доминирующий непустой владелец
    counts: dict = {}
    for lt, uid in rows:
        if uid:
            counts[uid] = counts.get(uid, 0) + 1
    target = os.environ.get("NEXUS_FIX_OWNER") or (
        max(counts, key=counts.get) if counts else ""
    )
    if not target:
        print("[fix] ERROR: не нашёл ни одного непустого user_notion_id — нечем заполнять. STOP")
        sys.exit(1)

    orphans = [r for r in rows if r[0] == "чеклист" and not r[1]]
    print(f"[fix] target owner (авто): {_short(target)}  (строк у него: {counts.get(target, 0)})")
    print(f"[fix] осиротевших checklist-строк (пустой user): {len(orphans)}")

    if not orphans:
        print("[fix] нечего чинить — все checklist-строки уже с владельцем.")
        return

    if not apply:
        print("[fix] DRY RUN — изменений нет. Запусти с --apply чтобы проставить владельца.")
        return

    stmt = (
        sa_update(nexus_lists)
        .where(nexus_lists.c.list_type == "чеклист")
        .where(or_(nexus_lists.c.user_notion_id == "", nexus_lists.c.user_notion_id.is_(None)))
        .values(user_notion_id=target)
    )
    with engine.begin() as conn:
        result = conn.execute(stmt)
    print(f"[fix] обновлено строк: {result.rowcount}")

    # verify
    with engine.connect() as conn:
        still = conn.execute(sa_select(nexus_lists.c.id)
            .where(nexus_lists.c.list_type == "чеклист")
            .where(or_(nexus_lists.c.user_notion_id == "", nexus_lists.c.user_notion_id.is_(None)))
        ).fetchall()
    if still:
        print(f"[fix] WARNING: осталось {len(still)} строк без владельца")
    else:
        print("[fix] OK — осиротевших checklist-строк больше нет.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
