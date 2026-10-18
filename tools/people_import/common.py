"""Общее для people_import: пути, .env, staging (people_staging.jsonl), slug/ключи."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]  # AI_AGENTS/
DATA_DIR = Path(__file__).resolve().parent / "data"
STAGING = DATA_DIR / "people_staging.jsonl"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TYPES = ("likes", "dislikes", "birthday", "important", "other")

_TR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


def env(*names: str, required: bool = True) -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    if required:
        sys.exit("Нет в .env: " + " / ".join(names))
    return ""


def slugify(name: str) -> str:
    s = "".join(_TR.get(ch, ch) for ch in name.lower())
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "unknown"


def norm_fact(fact: str) -> str:
    return re.sub(r"\s+", " ", fact.lower().strip())


from core.people import fact_hash  # noqa: E402  (одна реализация для импорта и ручного ввода)


def memory_key(name: str, type_: str, fact: str) -> str:
    """birthday — одна запись на человека (без хэша, реимпорт обновляет)."""
    slug = slugify(name)
    if type_ == "birthday":
        return "person_%s_birthday" % slug
    return "person_%s_%s_%s" % (slug, type_, fact_hash(fact))


def clean_aliases(name: str, aliases: Iterable[str]) -> List[str]:
    seen, out = {name.lower()}, []
    for a in aliases:
        a = (a or "").strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def load_staging() -> List[Dict]:
    if not STAGING.exists():
        return []
    with STAGING.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _dedup_id(rec: Dict) -> str:
    return "%s|%s|%s" % (rec["name"].lower(), rec["type"], norm_fact(rec["fact"]))


# Идентичность человека в staging: жёсткие ключи tg_id / vk_id (+ username'ы
# для автосклейки VK↔TG). origin: api — ДР из TG/VK API, chat — извлечено из переписки.
IDENT_FIELDS = ("tg_id", "tg_username", "vk_id", "vk_screen")


def _enrich(old: Dict, new: Dict) -> bool:
    """Дополняет существующую запись недостающими id/алиасами. True если изменили."""
    changed = False
    for f in IDENT_FIELDS:
        if not old.get(f) and new.get(f):
            old[f] = new[f]
            changed = True
    if new.get("origin") == "api" and old.get("origin") != "api":
        old["origin"] = "api"
        changed = True
    merged = clean_aliases(old["name"], list(old.get("aliases") or []) + list(new.get("aliases") or []))
    if merged != list(old.get("aliases") or []):
        old["aliases"] = merged
        changed = True
    return changed


def append_staging(records: Iterable[Dict]) -> int:
    """Дописывает новые записи; дубль (имя+тип+факт) не плодит, а ДОПОЛНЯЕТ
    существующую недостающими id/алиасами. Возвращает число новых записей."""
    existing = load_staging()
    index = {_dedup_id(r): r for r in existing}
    added, dirty = [], False
    for r in records:
        rec = {
            "name": r["name"],
            "aliases": clean_aliases(r["name"], r.get("aliases") or []),
            "fact": r["fact"],
            "type": r["type"] if r["type"] in TYPES else "other",
            "quote": r.get("quote", ""),
            "source": r.get("source", ""),
            "origin": r.get("origin") or ("api" if not r.get("quote") else "chat"),
            "approved": None,
        }
        for f in IDENT_FIELDS:
            rec[f] = r.get(f)
        k = _dedup_id(rec)
        if k in index:
            dirty = _enrich(index[k], rec) or dirty
            continue
        index[k] = rec
        added.append(rec)
    if dirty:
        write_staging(existing + added)
    elif added:
        with STAGING.open("a", encoding="utf-8") as f:
            for rec in added:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(added)


def write_staging(rows: List[Dict]) -> None:
    tmp = STAGING.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(STAGING)


def birthday_fact(day: int, month: int, year: Optional[int] = None) -> str:
    return "День рождения: %02d.%02d%s" % (day, month, ".%d" % year if year else "")


# ── даты рождения ───────────────────────────────────────────────────────────

from core.people import UNKNOWN_YEAR  # noqa: E402  (1904 — год ДР неизвестен, ADR-0027)

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_NUM_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?!\d)")
_WORD_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", re.IGNORECASE)


def parse_birthday(fact: str) -> Optional[Tuple[date, bool]]:
    """Дата из текста факта → (date, year_known). None если дату не достать
    (нет числа+месяца). Год неизвестен → UNKNOWN_YEAR."""
    text_ = fact.lower()
    cands = []  # type: List[Tuple[int, int, Optional[int]]]
    for m in _NUM_DATE.finditer(text_):
        cands.append((int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
    for m in _WORD_DATE.finditer(text_):
        word = m.group(2)
        mon = next((n for stem, n in _MONTHS.items() if word.startswith(stem) and (stem != "ма" or word in ("мая", "май"))), None)
        if mon:
            cands.append((int(m.group(1)), mon, int(m.group(3)) if m.group(3) else None))
    if len(cands) != 1:  # ноль или несколько дат — неоднозначно
        return None
    d, mth, y = cands[0]
    try:
        return date(y or UNKNOWN_YEAR, mth, d), bool(y)
    except ValueError:
        return None


def name_key(name: str) -> str:
    """Нормализованное имя для сравнения: lower, ё→е, схлопнутые пробелы,
    токены без падежных окончаний не режем (имя+фамилия сравниваются как есть)."""
    return re.sub(r"\s+", " ", name.lower().replace("ё", "е")).strip()
