#!/usr/bin/env python3
"""scripts/migrate_cards_to_en.py — одноразовая миграция Notion 🃏 Расклады.

Читает все записи, для каждой:
  1. Парсит поле «Карты» (split by comma).
  2. Резолвит каждую через deck_cards.json (deck = «Колоды» multi-select,
     дефолт rider-waite).
  3. Если все карты успешно — переписывает поле «Карты» в EN-форму.
  4. Если хотя бы одна не резолвится — лог в errors.txt, страница не трогается.

Запуск (из корня репо):
  python scripts/migrate_cards_to_en.py            # dry-run
  python scripts/migrate_cards_to_en.py --apply    # реально пишет в Notion
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import config  # noqa: E402
from core.notion_client import _text, query_pages, update_page  # noqa: E402
from miniapp.backend.tarot import find_card, resolve_deck_id  # noqa: E402


def _rich_text(page: dict, name: str) -> str:
    parts = page.get("properties", {}).get(name, {}).get("rich_text", [])
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _multi_names(page: dict, name: str) -> list[str]:
    items = page.get("properties", {}).get(name, {}).get("multi_select", [])
    return [it.get("name", "") for it in items if it.get("name")]


async def migrate(apply: bool) -> None:
    db_id = config.arcana.db_sessions
    pages = await query_pages(db_id, page_size=500)
    print(f"Found {len(pages)} sessions")

    errors_path = ROOT / "scripts" / "migrate_errors.txt"
    errors: list[str] = []
    updated = 0
    skipped_unchanged = 0

    for p in pages:
        cards_raw = _rich_text(p, "Карты")
        if not cards_raw:
            continue
        deck_name = ", ".join(_multi_names(p, "Колоды")) or "rider-waite"
        deck_id = resolve_deck_id(deck_name)
        page_id = p.get("id", "")
        title_parts = p.get("properties", {}).get("Тема", {}).get("title", [])
        theme = title_parts[0]["plain_text"] if title_parts else "—"

        en_parts: list[str] = []
        ok = True
        for raw in cards_raw.split(","):
            raw = raw.strip()
            if not raw:
                continue
            c = find_card(deck_id, raw)
            if not c or not c.get("en"):
                ok = False
                errors.append(f"{page_id} · «{theme}» · deck={deck_id} · unmatched: {raw}")
                break
            en_parts.append(c["en"])

        if not ok:
            continue
        new_str = ", ".join(en_parts)
        if new_str == cards_raw:
            skipped_unchanged += 1
            continue

        if apply:
            try:
                await update_page(page_id, {"Карты": _text(new_str)})
                updated += 1
                print(f"✓ {theme}: {cards_raw[:60]} → {new_str[:60]}")
            except Exception as e:
                errors.append(f"{page_id} · update failed: {e}")
        else:
            updated += 1
            print(f"DRY {theme}: {cards_raw[:60]} → {new_str[:60]}")

    errors_path.write_text("\n".join(errors), encoding="utf-8")
    print()
    print(f"{'APPLIED' if apply else 'DRY-RUN'}: {updated} updated, "
          f"{skipped_unchanged} unchanged, {len(errors)} errors")
    print(f"Errors → {errors_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Реально записать изменения в Notion (по умолчанию dry-run).")
    args = ap.parse_args()
    asyncio.run(migrate(apply=args.apply))


if __name__ == "__main__":
    main()
