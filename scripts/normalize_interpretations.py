#!/usr/bin/env python3
"""scripts/normalize_interpretations.py — одноразовая нормализация Трактовок.

Прогон sanitize_interpretation() по полю «Трактовка» всех записей 🃏 Расклады.
Существующие записи в смешанном формате (markdown + HTML) → чистый HTML
с allowlist'ом h3/b/i/p/br.

Запуск (из корня репо):
  python scripts/normalize_interpretations.py             # dry-run
  python scripts/normalize_interpretations.py --apply     # пишет в Notion
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import config  # noqa: E402
from core.html_sanitize import sanitize_interpretation  # noqa: E402
from core.notion_client import _text, query_pages, update_page  # noqa: E402


def _rich_text(page: dict, name: str) -> str:
    parts = page.get("properties", {}).get(name, {}).get("rich_text", [])
    return "".join(p.get("plain_text", "") for p in parts).strip()


async def normalize(apply: bool) -> None:
    db_id = config.arcana.db_sessions
    pages = await query_pages(db_id, page_size=500)
    print(f"Found {len(pages)} sessions")

    changed = 0
    unchanged = 0
    errors: list[str] = []

    for p in pages:
        page_id = p.get("id", "")
        title_parts = p.get("properties", {}).get("Тема", {}).get("title", [])
        theme = title_parts[0]["plain_text"] if title_parts else "—"
        interp = _rich_text(p, "Трактовка")
        if not interp:
            continue
        normalized = sanitize_interpretation(interp)
        if normalized == interp:
            unchanged += 1
            continue
        if apply:
            try:
                await update_page(page_id, {"Трактовка": _text(normalized[:2000])})
                changed += 1
                print(f"✓ {theme[:50]}: {len(interp)} → {len(normalized)} chars")
            except Exception as e:
                errors.append(f"{page_id}: {e}")
        else:
            changed += 1
            print(f"DRY {theme[:50]}: {len(interp)} → {len(normalized)} chars")

    err_path = ROOT / "scripts" / "normalize_errors.txt"
    err_path.write_text("\n".join(errors), encoding="utf-8")
    print()
    print(f"{'APPLIED' if apply else 'DRY-RUN'}: {changed} changed, "
          f"{unchanged} unchanged, {len(errors)} errors")
    if errors:
        print(f"Errors → {err_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(normalize(apply=args.apply))


if __name__ == "__main__":
    main()
