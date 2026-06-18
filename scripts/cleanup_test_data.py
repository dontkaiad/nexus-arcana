#!/usr/bin/env python3
"""scripts/cleanup_test_data.py — Dry-run / apply cleanup of test data in PG.

Usage:
    python3 scripts/cleanup_test_data.py           # dry-run (default, ничего не удаляет)
    python3 scripts/cleanup_test_data.py --apply   # удаление после подтверждения

ВАЖНО: сделай бэкап перед --apply:  ~/backups/pg_backup.sh
"""
from __future__ import annotations

import argparse
import os
import sys

# ── Паттерны (редактируй здесь) ───────────────────────────────────────────────
ILIKE_PATTERNS = [
    "%тест%",
    "%test%",
    "%asdf%",
]

# ── Таблицы (table_name, text_column)
# Порядок: потомки перед родителями по FK
_TABLES = [
    ("nexus_budget",    "description"),
    ("arcana_pnl",      "description"),
    ("debts",           "name"),
    ("nexus_lists",     "name"),
    ("arcana_inventory", "name"),
    ("memories",        "fact_text"),
    ("tasks",           "title"),
    ("sessions",        "title"),
    ("clients",         "name"),
    ("notes",           "title"),
]


def _get_engine():
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL не задан", file=sys.stderr)
        sys.exit(1)
    try:
        from sqlalchemy import create_engine
        return create_engine(db_url)
    except ImportError:
        print("ERROR: sqlalchemy не установлен", file=sys.stderr)
        sys.exit(1)


def _scan_table(engine, table_name: str, text_col: str) -> list:
    """Возвращает [{id, text}] для строк, совпадающих с любым ILIKE-паттерном."""
    from sqlalchemy import text

    parts = " OR ".join(
        f'"{text_col}" ILIKE :p{i}' for i, _ in enumerate(ILIKE_PATTERNS)
    )
    sql = f'SELECT id, "{text_col}" FROM "{table_name}" WHERE {parts}'
    params = {f"p{i}": p for i, p in enumerate(ILIKE_PATTERNS)}

    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
            return [{"id": row[0], "text": row[1]} for row in rows]
    except Exception as exc:
        print(f"  WARN: {table_name}: {exc}")
        return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Поиск и (опционально) удаление тестовых данных в PG."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Выполнить удаление (по умолчанию — dry-run, ничего не удаляется)."
    )
    args = parser.parse_args()

    engine = _get_engine()

    # ── Scan ──────────────────────────────────────────────────────────────────
    print("\n=== Поиск тестовых данных ===\n")
    print(f"Паттерны: {ILIKE_PATTERNS}\n")

    found: dict = {}
    total = 0

    for table_name, text_col in _TABLES:
        rows = _scan_table(engine, table_name, text_col)
        if rows:
            found[table_name] = rows
            total += len(rows)
            print(f"  {table_name} ({text_col}): {len(rows)} строк")
            for r in rows:
                print(f"    id={r['id']!r}  text={r['text']!r}")

    if not total:
        print("Тестовых данных не найдено.")
        return

    print(f"\nИтого: {total} строк в {len(found)} таблицах.")

    if not args.apply:
        print("\n[DRY-RUN] Ничего не удалено. Добавь --apply для реального удаления.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    print("\n⚠️  ВАЖНО: сделай бэкап ПЕРЕД удалением:  ~/backups/pg_backup.sh")
    print(f"\nБудет удалено {total} строк из таблиц: {', '.join(found)}")
    answer = input("\nНапиши DELETE для подтверждения: ").strip()
    if answer != "DELETE":
        print("Отменено.")
        return

    from sqlalchemy import text

    deleted: dict = {}
    with engine.begin() as conn:
        for table_name, _ in _TABLES:
            if table_name not in found:
                continue
            ids = [r["id"] for r in found[table_name]]
            if not ids:
                continue
            placeholders = ", ".join(f":id{i}" for i, _ in enumerate(ids))
            params = {f"id{i}": v for i, v in enumerate(ids)}
            sql = f'DELETE FROM "{table_name}" WHERE id IN ({placeholders})'
            result = conn.execute(text(sql), params)
            deleted[table_name] = result.rowcount

    print("\n=== Удалено ===")
    grand_total = 0
    for t, count in deleted.items():
        print(f"  {t}: {count} строк")
        grand_total += count
    print(f"\nИтого удалено: {grand_total} строк.")


if __name__ == "__main__":
    main()
