"""core/inv_line_parser.py — pure regex-парсер одной строки инвентаря.

Вынесено из ``nexus.handlers.lists`` чтобы скрипты могли импортировать
без подтягивания цепочки core.claude_client → ANTHROPIC_API_KEY.

Используется:
- regex-fallback в ``nexus.handlers.lists.handle_list_inv_add`` (когда Haiku
  не справился с batch'ем)
- одноразовый backfill ``scripts/fix_inv_quantities_2026_06.py``
"""
from __future__ import annotations

import re
from typing import Optional

# Quantity-маркер: «2 шт», «10 таблеток», «5 капсул», «3 пачки», «50 табл.»
_INV_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(?:шт(?:\.|ук[аи]?)?|таблет(?:ка|ки|ок|ой)?|табл\.?|"
    r"капсул[аыоу]?|пачк[аеиу]?|пакет(?:ов|а|ы)?|штук[аи]?)",
    re.IGNORECASE,
)


def parse_inv_line(line: str) -> Optional[dict]:
    """Парсит одну строку инвентаря: name + quantity + note (дозировка).

    Примеры:
        «меновазин 2 шт» → name=«меновазин», qty=2, note=«»
        «глюкофаж 1000мг 10 шт» → name=«глюкофаж», qty=10, note=«1000мг»
        «активированный уголь 250мг 1 пачка 30шт» → qty=30, note=«250мг»
        «бинт обычный» (без цифр) → qty=1
    """
    line = (line or "").strip().lstrip("•·-–— ").strip()
    if not line:
        return None
    # 1) quantity — последнее вхождение «\d+ шт/таблеток/капсул/пачк»
    qty = 1
    qty_matches = list(_INV_QTY_RE.finditer(line))
    if qty_matches:
        last = qty_matches[-1]
        try:
            qty = max(1, int(float(last.group(1).replace(",", "."))))
        except (ValueError, TypeError):
            qty = 1
    # 2) name = текст до первой цифры (если цифра не в начале)
    first_digit = re.search(r"\d", line)
    if first_digit and first_digit.start() > 0:
        name = line[:first_digit.start()].rstrip(",;:- ").strip()
        rest = line[first_digit.start():].strip()
    else:
        name = line
        rest = ""
    if not name:
        name = line
        rest = ""
    # 3) note = rest минус извлечённые qty-токены, очищенный
    note = rest
    if qty_matches and note:
        note = _INV_QTY_RE.sub("", note)
        note = re.sub(r"\s{2,}", " ", note).strip(" ,;:-").strip()
    return {"name": name, "quantity": qty, "note": note}
