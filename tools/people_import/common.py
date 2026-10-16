"""Общее для people_import: пути, .env, staging (people_staging.jsonl), slug/ключи."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]  # AI_AGENTS/
DATA_DIR = Path(__file__).resolve().parent / "data"
STAGING = DATA_DIR / "people_staging.jsonl"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TYPES = ("likes", "dislikes", "birthday", "other")

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


def fact_hash(fact: str) -> str:
    return hashlib.sha1(norm_fact(fact).encode("utf-8")).hexdigest()[:8]


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


def append_staging(records: Iterable[Dict]) -> int:
    """Дописывает новые записи; дубли (имя+тип+факт) пропускает. approved=None."""
    have = {_dedup_id(r) for r in load_staging()}
    added = 0
    with STAGING.open("a", encoding="utf-8") as f:
        for r in records:
            rec = {
                "name": r["name"],
                "aliases": clean_aliases(r["name"], r.get("aliases") or []),
                "fact": r["fact"],
                "type": r["type"] if r["type"] in TYPES else "other",
                "quote": r.get("quote", ""),
                "source": r.get("source", ""),
                "approved": None,
            }
            k = _dedup_id(rec)
            if k in have:
                continue
            have.add(k)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            added += 1
    return added


def birthday_fact(day: int, month: int, year: Optional[int] = None) -> str:
    return "День рождения: %02d.%02d%s" % (day, month, ".%d" % year if year else "")
