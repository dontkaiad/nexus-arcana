"""core/cats.py — чистые функции домена «Коты» (без БД): ключ и тип факта кота.

Ключ факта кота — cat_<id>_<тип>_<hash8>, как person_<id>_<тип>_<hash8> у людей: одинаковый факт не
дублируется (upsert по ключу), тип читается из ключа. Старые факты с произвольным ключом читаются
по тексту (guess_fact_type) — см. fact_kind.
"""
from __future__ import annotations

import re
from typing import Optional

from core.people import FACT_TYPES, fact_hash, guess_fact_type

FACT_TYPE_ORDER = ("important", "likes", "dislikes", "other")
_KEY_RE = re.compile(r"^(cat_\d+_)([a-z]+)(_[0-9a-f]{8})$")


def cat_fact_key(cat_id: int, fact_type: str, fact: str) -> str:
    if fact_type not in FACT_TYPES:
        fact_type = "other"
    return "cat_%d_%s_%s" % (int(cat_id), fact_type, fact_hash(fact))


def cat_key_type(key_name: str) -> Optional[str]:
    """Тип из ключа cat_<id>_<тип>_<hash8>; None — ключ не такого вида (старый факт)."""
    m = _KEY_RE.match(key_name or "")
    return m.group(2) if m else None


def retype_cat_key(key_name: str, new_type: str) -> Optional[str]:
    m = _KEY_RE.match(key_name or "")
    return "%s%s%s" % (m.group(1), new_type, m.group(3)) if m and new_type in FACT_TYPES else None


def fact_kind(key_name: str, fact: str) -> str:
    """Тип для показа: из ключа, иначе по тексту (старые факты без ключа cat_…)."""
    t = cat_key_type(key_name)
    return t if t in FACT_TYPES else guess_fact_type(fact)
