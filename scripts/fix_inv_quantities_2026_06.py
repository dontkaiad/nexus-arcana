"""scripts/fix_inv_quantities_2026_06.py — backfill после волны #78.

Что чинит:
Записи в 📦 Инвентарь (☀️ Nexus) созданные ДО фикса #78 — у всех
qty=1 и заметка пустая, а реальная дозировка/количество вшиты в название
(«глюкофаж 1000мг 10 шт», «активированный уголь 250мг 1 пачка 30шт»).

Что делает:
1. Запрашивает все 📦 Инвентарь · ☀️ Nexus · Not started.
2. Фильтрует «нетронутые» — qty=1 (или None) И заметка пустая.
3. Парсит название через ``nexus.handlers.lists._parse_inv_line``.
4. Если парсер выделил qty>1 или note — обновляет:
   - Название = parsed.name (без «1000мг 10 шт» хвоста)
   - Количество = parsed.quantity
   - Заметка = parsed.note

Идемпотентность: пропускает уже исправленные (qty>1 или note не пустой).

Использование::

    # Безопасный dry-run (по умолчанию)
    python3 scripts/fix_inv_quantities_2026_06.py
    python3 scripts/fix_inv_quantities_2026_06.py --limit 200

    # Реальная запись (только с явного go от Кай)
    python3 scripts/fix_inv_quantities_2026_06.py --apply

CLI:
    --dry-run  (default): показывает sample 10 + summary, ничего не пишет
    --apply               (опасно): пишет в Notion
    --limit N             (default 500): максимум записей для скана
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Добавляем корень репо в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Загружаем .env ДО любых импортов из core/* — там цепочка тянет claude_client
# который требует ANTHROPIC_API_KEY на импорте.
try:
    from dotenv import load_dotenv as _load_dotenv
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    # override=True: в shell могут быть пустые значения (например, ANTHROPIC_API_KEY=""),
    # без override load_dotenv не перезапишет их и core.config упадёт.
    _load_dotenv(_ENV_PATH, override=True)
except ImportError:
    pass

from core.props import _title, _number, _text, _date
from core.notion_client import query_pages, update_page  # noqa: E402
from core.inv_line_parser import parse_inv_line as _parse_inv_line  # noqa: E402

logger = logging.getLogger("fix_inv_quantities_2026_06")

_RATE_LIMIT_SEC = 0.3


def _get_name(page: dict) -> str:
    title = page.get("properties", {}).get("Название", {}).get("title", [])
    if not title:
        return ""
    return "".join(t.get("plain_text", "") for t in title).strip()


def _get_qty(page: dict) -> float:
    return page.get("properties", {}).get("Количество", {}).get("number") or 0


def _get_note(page: dict) -> str:
    rt = page.get("properties", {}).get("Заметка", {}).get("rich_text", [])
    if not rt:
        return ""
    return "".join(t.get("plain_text", "") for t in rt).strip()


def _get_type(page: dict) -> str:
    sel = page.get("properties", {}).get("Тип", {}).get("select")
    return (sel or {}).get("name", "")


def _get_bot(page: dict) -> str:
    sel = page.get("properties", {}).get("Бот", {}).get("select")
    return (sel or {}).get("name", "")


def _get_status(page: dict) -> str:
    st = page.get("properties", {}).get("Статус", {}).get("status")
    return (st or {}).get("name", "")


def _get_expiry(page: dict) -> str:
    d = page.get("properties", {}).get("Срок годности", {}).get("date")
    return (d or {}).get("start", "") or ""


def _planned_changes(page: dict) -> dict | None:
    """Возвращает props для update_page или None если менять нечего."""
    name = _get_name(page)
    if not name:
        return None
    qty = _get_qty(page)
    note = _get_note(page)
    # Уже исправлено или ручная правка — пропускаем
    if qty and qty > 1:
        return None
    if note:
        return None
    parsed = _parse_inv_line(name)
    if not parsed:
        return None
    new_name = parsed["name"]
    new_qty = parsed["quantity"]
    new_note = parsed["note"]
    new_expiry = parsed.get("expiry", "")
    cur_expiry = _get_expiry(page)
    # Если парсер ничего полезного не извлёк — skip
    if new_qty == 1 and not new_note and new_name == name and not new_expiry:
        return None
    props: dict = {}
    if new_name and new_name != name:
        props["Название"] = _title(new_name)
    if new_qty and new_qty != 1:
        props["Количество"] = _number(float(new_qty))
    if new_note:
        props["Заметка"] = _text(new_note)
    if new_expiry and not cur_expiry:
        props["Срок годности"] = _date(new_expiry)
    return props if props else None


def _diff_preview(page: dict, props: dict) -> str:
    pid = page.get("id", "")[:8]
    old_name = _get_name(page)
    old_qty = _get_qty(page)
    old_note = _get_note(page)
    old_expiry = _get_expiry(page)
    new_name = props.get("Название", {}).get("title", [{}])[0].get("text", {}).get("content", old_name)
    new_qty = props.get("Количество", {}).get("number", old_qty)
    new_note = props.get("Заметка", {}).get("rich_text", [{}])[0].get("text", {}).get("content", old_note)
    new_expiry = props.get("Срок годности", {}).get("date", {}).get("start", old_expiry)
    lines = [f"  id={pid}…"]
    if new_name != old_name:
        lines.append(f"    Название: {old_name!r}\n            → {new_name!r}")
    if new_qty != old_qty:
        lines.append(f"    Количество: {old_qty} → {new_qty}")
    if new_note != old_note:
        lines.append(f"    Заметка: {old_note!r} → {new_note!r}")
    if new_expiry != old_expiry:
        lines.append(f"    Срок годности: {old_expiry!r} → {new_expiry!r}")
    return "\n".join(lines)


async def _scan_and_fix(limit: int, apply: bool) -> dict:
    db_id = os.environ.get("NOTION_DB_LISTS", "")
    if not db_id:
        raise RuntimeError("NOTION_DB_LISTS не задан в .env")

    # Фильтр на стороне Notion: 📦 Инвентарь · ☀️ Nexus · Not started
    notion_filter = {
        "and": [
            {"property": "Тип", "select": {"equals": "📦 Инвентарь"}},
            {"property": "Бот", "select": {"equals": "☀️ Nexus"}},
            {"property": "Статус", "status": {"equals": "Not started"}},
        ]
    }
    pages = await query_pages(db_id, filters=notion_filter, page_size=min(limit, 500))
    pages = pages[:limit]
    summary = {
        "scanned": len(pages),
        "needs_fix": 0,
        "applied": 0,
        "skipped_modified": 0,
        "skipped_no_change": 0,
        "sample": [],
        "errors": [],
    }
    for i, page in enumerate(pages, start=1):
        qty = _get_qty(page)
        note = _get_note(page)
        if (qty and qty > 1) or note:
            summary["skipped_modified"] += 1
            continue
        props = _planned_changes(page)
        if not props:
            summary["skipped_no_change"] += 1
            continue
        summary["needs_fix"] += 1
        if len(summary["sample"]) < 10:
            summary["sample"].append(_diff_preview(page, props))
        action = "APPLY" if apply else "DRY"
        pid = page.get("id", "")[:8]
        print(f"[{i}/{len(pages)}] id={pid}… action={action} "
              f"fields={list(props.keys())}")
        if apply:
            try:
                await update_page(page["id"], props)
                summary["applied"] += 1
                await asyncio.sleep(_RATE_LIMIT_SEC)
            except Exception as e:
                logger.error("update_page failed for %s: %s", pid, e)
                summary["errors"].append((pid, str(e)))
    return summary


def _print_summary(summary: dict, apply: bool) -> None:
    print()
    print("=" * 60)
    print(f"  SCANNED:             {summary['scanned']}")
    print(f"  NEEDS FIX:           {summary['needs_fix']}")
    print(f"  SKIPPED (modified):  {summary['skipped_modified']}")
    print(f"  SKIPPED (no change): {summary['skipped_no_change']}")
    if apply:
        print(f"  APPLIED:             {summary['applied']}")
        if summary["errors"]:
            print(f"  ERRORS:              {len(summary['errors'])}")
            for pid, err in summary["errors"][:5]:
                print(f"    - {pid}: {err}")
    else:
        print("  APPLIED:             0 (dry-run, --apply для записи)")
    print("=" * 60)
    if summary["sample"]:
        print("\nSAMPLE (first 10):")
        for s in summary["sample"]:
            print(s)
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill quantity/note для 📦 Инвентарь (☀️ Nexus) после #78",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Реальная запись в Notion (по умолчанию dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет сделано (default)")
    parser.add_argument("--limit", type=int, default=500,
                        help="Сколько записей просканировать (default 500)")
    args = parser.parse_args()

    apply = bool(args.apply)
    if apply and args.dry_run:
        print("⚠️ --apply и --dry-run одновременно — apply отменён, делаю dry-run")
        apply = False

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if apply:
        print("⚠️ APPLY MODE — пишу в продовый Notion. Ctrl+C в первые 3с чтоб отменить.")
        import time
        time.sleep(3)

    summary = asyncio.run(_scan_and_fix(args.limit, apply))
    _print_summary(summary, apply)


if __name__ == "__main__":
    main()
