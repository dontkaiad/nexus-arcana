"""core/inv_line_parser.py — pure regex-парсер одной строки инвентаря.

Вынесено из ``nexus.handlers.lists`` чтобы скрипты могли импортировать
без подтягивания цепочки core.claude_client → ANTHROPIC_API_KEY.

Используется:
- regex-fallback в ``nexus.handlers.lists.handle_list_inv_add`` (когда Haiku
  не справился с batch'ем)
- одноразовый backfill ``scripts/fix_inv_quantities_2026_06.py``
"""
from __future__ import annotations

import calendar
import re
from typing import Optional, Tuple

# Quantity-маркер: «2 шт», «10 таблеток», «5 капсул», «3 пачки», «50 табл.»
_INV_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(?:шт(?:\.|ук[аи]?)?|таблет(?:ка|ки|ок|ой)?|табл\.?|"
    r"капсул[аыоу]?|пачк[аеиу]?|пакет(?:ов|а|ы)?|штук[аи]?)",
    re.IGNORECASE,
)

# Срок годности. Дата: «DD.MM.YYYY» / «DD.MM.YY» / «MM.YYYY» / «MM.YY».
_DATE_TOKEN = r"\d{1,2}(?:[.\/]\d{1,2})?[.\/]\d{2,4}"
# «срок годности [до] <дата>»
_EXPIRY_SROK_RE = re.compile(
    r"срок\w*\s*годн\w*\s*(?:до\s+)?(" + _DATE_TOKEN + r")",
    re.IGNORECASE,
)
# «[годен/годна/годно/годны] до <дата>»
_EXPIRY_DO_RE = re.compile(
    r"(?:год(?:ен|на|но|ны)\s+)?до\s+(" + _DATE_TOKEN + r")",
    re.IGNORECASE,
)


def _date_str_to_iso(s: str) -> Optional[str]:
    """«15.06.2026» → 2026-06-15; «03.2027» → 2027-03-31 (конец месяца)."""
    parts = [p for p in re.split(r"[.\/]", s or "") if p]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        day, month, year = nums
    elif len(nums) == 2:
        day, month, year = None, nums[0], nums[1]
    else:
        return None
    if year < 100:
        year += 2000
    if not (1 <= month <= 12) or not (1900 <= year <= 2100):
        return None
    if day is None:
        # Только месяц+год → последний день месяца (годен до конца месяца).
        day = calendar.monthrange(year, month)[1]
    if not (1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_expiry(text: str) -> Tuple[Optional[str], str]:
    """Вытащить срок годности из свободного текста.

    Возвращает (iso_date | None, cleaned_text). Маркер «годен до» /
    «срок годности» вместе с датой убирается из текста.
    """
    if not text:
        return None, text
    # Сначала «срок годности …» (более специфичный), затем «… до …».
    for rx in (_EXPIRY_SROK_RE, _EXPIRY_DO_RE):
        m = rx.search(text)
        if not m:
            continue
        iso = _date_str_to_iso(m.group(1))
        if not iso:
            continue
        cleaned = (text[:m.start()] + " " + text[m.end():])
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:-").strip()
        return iso, cleaned
    return None, text


def parse_inv_line(line: str) -> Optional[dict]:
    """Парсит одну строку инвентаря: name + quantity + note + expiry.

    Примеры:
        «меновазин 2 шт» → name=«меновазин», qty=2, note=«»
        «глюкофаж 1000мг 10 шт» → name=«глюкофаж», qty=10, note=«1000мг»
        «активированный уголь 250мг 1 пачка 30шт» → qty=30, note=«250мг»
        «гексаспрей 30гр годен до 03.2027» → note=«30гр», expiry=«2027-03-31»
        «бинт обычный» (без цифр) → qty=1
    """
    line = (line or "").strip().lstrip("•·-–— ").strip()
    if not line:
        return None
    # 0) срок годности — извлекаем ПЕРВЫМ, чтобы дата не путалась с qty/note.
    expiry, line = extract_expiry(line)
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
    return {"name": name, "quantity": qty, "note": note, "expiry": expiry or ""}
