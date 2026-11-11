"""core/cats.py — чистые функции домена «Коты» (без БД): ключ и тип факта кота.

Ключ факта кота — cat_<id>_<тип>_<hash8>, как person_<id>_<тип>_<hash8> у людей: одинаковый факт не
дублируется (upsert по ключу), тип читается из ключа. Старые факты с произвольным ключом читаются
по тексту (guess_fact_type) — см. fact_kind.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

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


def group_facts(facts, name: str = "", aliases=None, strip_name: bool = True, with_block: bool = False):
    """Факты → группы по типу ([{type, items}]), порядок FACT_TYPE_ORDER; внутри — по порядку входа.
    У факта кота имя в начале («Тучка не ест курицу») в карточке лишнее — срезаем, как у людей.
    with_block — добавить каждому факту `block` (core.cat_blocks). Только для владельца: контракт читающего токена (golden cats_read_v1) его не содержит."""
    from core.people import strip_name_prefix
    by = {}
    for f in facts:
        t = fact_kind(f.get("key"), f["text"])
        text_ = strip_name_prefix(f["text"], name, aliases or []) if (strip_name and name) else f["text"]
        item = {
            "id": f["id"], "text": text_, "type": t, "is_current": f.get("is_current", True),
            "source": f.get("source"),
            "created_at": f["created_at"].isoformat() if f.get("created_at") else None,
        }
        if with_block:
            item["block"] = f.get("block")
        by.setdefault(t, []).append(item)
    order = [t for t in FACT_TYPE_ORDER if t in by] + sorted(t for t in by if t not in FACT_TYPE_ORDER)
    return [{"type": t, "items": by[t]} for t in order]


_PHOTO_BAD = re.compile(r"""[\s"'<>\\`\x00-\x1f\x7f]""")
PHOTO_URL_MAX = 2048


def safe_photo_url(url: object) -> Optional[str]:
    """Адрес фото, который безопасно отдать потребителю, иначе None. Только абсолютный https с хостом и без учётных
    данных; пробелы, кавычки, скобки тегов, обратная косая, управляющие символы и слишком длинные адреса — отбрасываются
    (javascript:, data:, //host/x, http:// тоже). Адрес не «чинится»: либо как есть, либо None."""
    if not isinstance(url, str) or not url or len(url) > PHOTO_URL_MAX or _PHOTO_BAD.search(url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    return url
