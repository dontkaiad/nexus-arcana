"""scripts/migrate_arcana_legacy.py — миграция legacy 🃏 Раскладов.

Что делает:
1. Сырой markdown/HTML в поле «Трактовка» → нормализует через
   ``core.html_sanitize.sanitize_interpretation``.
2. Пустое поле «Дно колоды», но в «Трактовка» есть «🂠 Дно: КартаX»
   или «🂠 КартаX · фон» — извлекает имя карты в поле.

Идемпотентность: повторный прогон no-op для уже мигрированных записей
(sanitize детерминирован, bottom-extract проверяет «уже заполнено»).

Использование::

    # Безопасный dry-run (по умолчанию)
    python3 scripts/migrate_arcana_legacy.py
    python3 scripts/migrate_arcana_legacy.py --limit 200

    # Реальная запись (только с явного go от Кай)
    python3 scripts/migrate_arcana_legacy.py --apply --limit 50

CLI:
    --dry-run  (default): показать sample 3 + summary, ничего не пишет
    --apply               (опасно): пишет в Notion
    --limit N             (default 50): сколько записей просканировать
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Optional

# Добавляем корень репо в path, чтобы импортировать core/*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.html_sanitize import sanitize_interpretation  # noqa: E402

logger = logging.getLogger("migrate_arcana_legacy")

_RATE_LIMIT_SEC = 0.3


# ── Pure helpers (тестируемы без Notion) ────────────────────────────────────

def _detect_html_in_interp(text: str) -> bool:
    """True если текст содержит сырой markdown/HTML, требующий sanitize.

    Сигналы:
    - markdown headings ``## ...``, ``### ...``
    - markdown bold/italic ``**...**``, ``__...__``
    - HTML-теги (любые, sanitize фильтрует не-allowlist)
    """
    if not text:
        return False
    s = str(text)
    if re.search(r"^\s*#{2,}\s+\w", s, flags=re.MULTILINE):
        return True
    if re.search(r"\*\*\S.+?\S\*\*", s):
        return True
    if re.search(r"__\S.+?\S__", s):
        return True
    if re.search(r"<[a-zA-Z]+[^>]*>", s):
        return True
    return False


def _extract_bottom_from_legacy_interp(text: str) -> Optional[str]:
    """«🂠 Дно: Король Кубков» / «🂠 Король Кубков · фон» / «🂠 King of Cups»
    → 'Король Кубков' / 'King of Cups'. None если не нашли.
    """
    if not text:
        return None
    s = str(text)
    # Вариант 1: «🂠 Дно: КартаX»
    m = re.search(r"🂠\s*Дно[:\s]+([^\n<·]+)", s)
    if m:
        name = m.group(1).strip(" .,;<>")
        if name:
            return name
    # Вариант 2: «🂠 КартаX · фон» (новый формат)
    m = re.search(r"🂠\s+([^\n<·]+?)\s*·\s*фон", s)
    if m:
        return m.group(1).strip(" .,;<>")
    # Вариант 3: «🂠 КартаX» (просто символ + имя на той же строке)
    m = re.search(r"🂠\s+([^\n<·]+?)(?:\s*</?[a-zA-Z]|$)", s, flags=re.MULTILINE)
    if m:
        cand = m.group(1).strip(" .,;<>")
        # Отбрасываем явный мусор / структурные подписи
        if cand and cand.lower() not in {"дно колоды", "дно"}:
            return cand
    return None


def _migrate_record(page: dict) -> dict:
    """Возвращает dict с предложенными изменениями (только полями для update_page).

    Пустой dict ⇒ запись уже в актуальном состоянии (no-op).
    """
    changes: dict = {}
    props = page.get("properties", {}) or {}

    interp_items = (props.get("Трактовка", {}) or {}).get("rich_text") or []
    interp = "".join(it.get("plain_text", "") for it in interp_items)

    bottom_items = (props.get("Дно колоды", {}) or {}).get("rich_text") or []
    bottom = "".join(it.get("plain_text", "") for it in bottom_items).strip()

    # 1. Sanitize interpretation если требуется
    if interp and _detect_html_in_interp(interp):
        new_interp = sanitize_interpretation(interp)
        if new_interp and new_interp != interp:
            changes["Трактовка"] = {
                "rich_text": [
                    {"type": "text", "text": {"content": new_interp[:2000]}}
                ]
            }

    # 2. Заполняем «Дно колоды» из legacy формата (только если поле пустое)
    if not bottom and interp:
        guessed = _extract_bottom_from_legacy_interp(interp)
        if guessed:
            changes["Дно колоды"] = {
                "rich_text": [
                    {"type": "text", "text": {"content": guessed[:200]}}
                ]
            }

    return changes


# ── CLI / Notion driver ─────────────────────────────────────────────────────

def _diff_preview(page: dict, changes: dict, max_len: int = 160) -> str:
    """Human-readable превью изменений для dry-run."""
    pid = page.get("id", "")[:8]
    lines = [f"  id={pid}…"]
    for field, val in changes.items():
        rt = val.get("rich_text") or []
        new_text = "".join(it.get("text", {}).get("content", "") for it in rt)
        # Показываем before
        before_items = (page.get("properties", {}).get(field, {})
                        or {}).get("rich_text") or []
        before = "".join(it.get("plain_text", "") for it in before_items)
        b = (before[:max_len] + "…") if len(before) > max_len else before
        a = (new_text[:max_len] + "…") if len(new_text) > max_len else new_text
        lines.append(f"    {field}:")
        lines.append(f"      before: {b!r}")
        lines.append(f"      after:  {a!r}")
    return "\n".join(lines)


async def _scan_and_migrate(limit: int, apply: bool) -> dict:
    """Возвращает summary {scanned, needs_migration, applied, sample}."""
    from core.config import config
    from core.notion_client import query_pages, update_page

    db_id = config.arcana.db_sessions if hasattr(
        config.arcana, "db_sessions"
    ) else None
    if not db_id:
        # fallback: используем то что выставлено в env
        import os
        db_id = os.environ.get("NOTION_DB_SESSIONS", "")
    if not db_id:
        raise RuntimeError(
            "config.arcana.db_sessions не задан. Проверь NOTION_DB_SESSIONS."
        )

    pages = await query_pages(db_id, page_size=min(limit, 500))
    pages = pages[:limit]
    summary = {
        "scanned": len(pages),
        "needs_migration": 0,
        "applied": 0,
        "sample": [],
    }
    for i, page in enumerate(pages, start=1):
        changes = _migrate_record(page)
        if not changes:
            continue
        summary["needs_migration"] += 1
        if len(summary["sample"]) < 3:
            summary["sample"].append(_diff_preview(page, changes))
        action = "APPLY" if apply else "DRY"
        pid = page.get("id", "")[:8]
        print(f"[{i}/{len(pages)}] id={pid}… action={action} "
              f"fields={list(changes.keys())}")
        if apply:
            try:
                await update_page(page["id"], changes)
                summary["applied"] += 1
                await asyncio.sleep(_RATE_LIMIT_SEC)
            except Exception as e:
                logger.error("update_page failed for %s: %s", pid, e)
    return summary


def _print_summary(summary: dict, apply: bool) -> None:
    print()
    print("=" * 60)
    print(f"  SCANNED:         {summary['scanned']}")
    print(f"  NEEDS MIGRATION: {summary['needs_migration']}")
    if apply:
        print(f"  APPLIED:         {summary['applied']}")
    else:
        print(f"  APPLIED:         0 (dry-run, --apply для записи)")
    print("=" * 60)
    if summary["sample"]:
        print("\nSAMPLE (first 3):")
        for s in summary["sample"]:
            print(s)
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция legacy 🃏 Раскладов")
    parser.add_argument("--apply", action="store_true",
                        help="Реальная запись в Notion (по умолчанию dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет сделано (default)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Сколько записей просканировать (default 50)")
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

    summary = asyncio.run(_scan_and_migrate(args.limit, apply))
    _print_summary(summary, apply)


if __name__ == "__main__":
    main()
