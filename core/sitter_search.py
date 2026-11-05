"""core/sitter_search.py — поиск ситтера «где таблетки» БЕЗ LLM по ограниченному набору источников.

Источники (и только они; SQL написан на фиксированные таблицы, а не фильтр общего поиска по Памяти):
  1. факты про котов (memories.cat_id IS NOT NULL);
  2. «Общее про котов» (категория «🐾 Коты», cat_id пуст);
  3. пункты nexus_lists, которые видит ситтер — то же условие `sitter_visible_clause`, что у GET /api/sitter/lists
     (инвентарь по умолчанию, кроме скрытых; покупки и чеклисты по for_sitter).
Люди, финансы, остальная Память, прочие списки, чужие владельцы недоступны по построению.

Релевантность: FTS ('russian', ts_rank) и вектор (Voyage, cosine). Пункт проходит, если cosine >= VEC_MIN ИЛИ ts_rank >= FTS_MIN.
Нет ключа Voyage / сбой эмбеддинга — работает только FTS. Ответ — до MAX_HITS фрагментов (≤ MAX_SNIPPET символов) с ярлыком источника,
без id, цен и служебных полей; ничего не прошло порог — found=false (бот отвечает «Не нашёл информации, спроси у Кай»).
Запрос ≤ MAX_Q символов, в логи попадает только длина.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import unicodedata
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from sqlalchemy import func, literal_column, select, text

from core.db import get_engine
from core.memory_search import fts_query
from core.rag import _embed, _vec_literal
from core.repos.lists_table import nexus_lists as nl
from core.repos.pg_sitter_lists_repo import INVENTORY, sitter_visible_clause

logger = logging.getLogger("core.sitter_search")

MAX_Q = 200
MAX_HITS = 3
MAX_SNIPPET = 300
MAX_ITEMS_EMBEDDED = 400
CATS_CATEGORY = "🐾 Коты"
# Пороги релевантности (подобраны на вымышленном наборе, см. docs/CASES/0034-sitter-ask.md).
VEC_MIN = 0.58      # cosine (voyage-4-lite): 0 ложных на 10 вопросах без ответа, запас 0.011 до ближайшего «соседа»
FTS_MIN = 0.10      # после sitter_fts_query остаются предметные слова: любое совпадение уже даёт ts_rank ≥ 0.3


# --- sitter_fts_query begin ---
# Общие слова вопросов ситтера: лексически совпадают с чужими пунктами («лежит корм» ≈ «лежит паспорт») и дают ложные срабатывания.
_ASK_STOP = {
    "лежит", "лежат", "лежать", "лёг", "стоит", "стоят", "находится", "находятся", "храню", "хранится", "любит", "любят",
    "можно", "нужно", "надо", "должен", "должна", "дать", "давать", "делать", "сделать", "принять", "взять", "найти", "искать",
    "куда", "когда", "сколько", "почему", "зачем", "чего", "кого", "чем", "если", "часто", "сейчас", "сегодня", "завтра",
    "ли", "бы", "же", "то", "вот", "тут", "там", "здесь", "мне", "нам", "вам", "ему", "ей", "им", "это", "этот", "эта",
}


def sitter_fts_query(q: str) -> str:
    """fts_query без общих слов вопроса (останутся предметные слова: «таблетки», «бинт», «Тучка»)."""
    words = [w for w in fts_query(q).split(" | ") if w and w not in _ASK_STOP]
    return " | ".join(words)
# --- sitter_fts_query end ---


def clean_query(raw: Any) -> str:
    """Пробелы схлопнуты, управляющие и невидимые форматирующие символы убраны, длина ≤ MAX_Q."""
    if not isinstance(raw, str):
        return ""
    t = "".join(" " if unicodedata.category(ch) == "Cc" else "" if unicodedata.category(ch) == "Cf" else ch for ch in raw)
    return " ".join(t.split())[:MAX_Q].strip()


class QueryBudget:
    """Скользящее окно: не больше `limit` запросов за `window` секунд (защита расходов на эмбеддинги)."""

    def __init__(self, limit: int = 30, window: float = 60.0, clock=time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: Deque[float] = deque()

    def take(self) -> int:
        """0 — можно (запрос засчитан); иначе сколько секунд ждать."""
        now = self._clock()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return max(1, int(math.ceil(self.window - (now - self._hits[0]))))
        self._hits.append(now)
        return 0


def _cos(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _clip(v: Any, n: int) -> str:
    return " ".join("".join(ch for ch in str(v or "") if ch >= " " and ch != "\x7f").split())[:n]


_ITEM_VEC: Dict[Tuple[int, str], List[float]] = {}          # (id, updated_at) -> вектор документа; в памяти процесса


def _item_vectors(items: List[Dict[str, Any]]) -> Dict[int, List[float]]:
    """Векторы документов пунктов списков: из кэша, недостающие одним батчем. Сбой эмбеддинга — пусто (останется FTS)."""
    out: Dict[int, List[float]] = {}
    missing = []
    for it in items:
        v = _ITEM_VEC.get((it["id"], it["stamp"]))
        if v is not None:
            out[it["id"]] = v
        else:
            missing.append(it)
    if missing and len(missing) <= MAX_ITEMS_EMBEDDED:
        try:
            vecs = _embed([m["doc"] for m in missing], "document")
        except Exception:
            logger.warning("sitter_search: item embedding failed", exc_info=True)
            vecs = []
        if len(vecs) == len(missing):
            if len(_ITEM_VEC) > 5000:
                _ITEM_VEC.clear()
            for m, v in zip(missing, vecs):
                _ITEM_VEC[(m["id"], m["stamp"])] = v
                out[m["id"]] = v
    return out


_FACTS_SQL = text("""
    SELECT m.id, m.fact_text, c.name AS cat_name, (m.cat_id IS NULL) AS general,
           CASE WHEN :tsq <> '' THEN ts_rank(m.fts, to_tsquery('russian', :tsq)) ELSE 0 END AS fts,
           CASE WHEN CAST(:q AS text) IS NOT NULL AND m.embedding IS NOT NULL
                THEN 1 - (m.embedding <=> CAST(:q AS vector)) END AS cos
    FROM memories m LEFT JOIN cats c ON c.id = m.cat_id
    WHERE m.user_id = :u
      AND (m.cat_id IS NOT NULL OR m.category = :cat)
      AND m.is_current = true AND m.is_archived = false AND m.review_status = 'confirmed' AND m.fact_text <> ''
      AND (c.id IS NULL OR c.status <> 'rejected')
""")


def _candidates(user_id: str, tsq: str, qvec: Optional[List[float]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with get_engine().connect() as c:
        for r in c.execute(_FACTS_SQL, {"tsq": tsq, "q": _vec_literal(qvec) if qvec else None, "u": user_id, "cat": CATS_CATEGORY}):
            out.append({"source": "general" if r.general else "fact",
                        "label": "общее про котов" if r.general else "про кота " + _clip(r.cat_name, 40),
                        "text": _clip(r.fact_text, MAX_SNIPPET), "fts": float(r.fts or 0), "cos": float(r.cos) if r.cos is not None else None})
        # вес 'A', как у fact_text в memories.fts: иначе ts_rank пунктов списков в ~10 раз ниже и пороги несравнимы
        doc = func.setweight(func.to_tsvector("russian", func.concat_ws(" ", nl.c.name, nl.c.note, nl.c.category, nl.c.group_name)), "A")
        rank = func.ts_rank(doc, func.to_tsquery("russian", tsq)) if tsq else literal_column("0.0")
        rows = c.execute(select(nl.c.id, nl.c.name, nl.c.note, nl.c.category, nl.c.group_name, nl.c.list_type, nl.c.quantity,
                                nl.c.updated_at, rank.label("fts")).where(sitter_visible_clause(user_id))).all()
    items = [{"id": r.id, "stamp": str(r.updated_at), "row": r,
              "doc": _clip("%s: %s. %s" % (r.category or r.group_name or "", r.name, r.note or ""), 400)} for r in rows]
    vecs = _item_vectors(items) if (qvec and items) else {}
    for it in items:
        r = it["row"]
        qty = (" × %g" % float(r.quantity)) if r.quantity is not None else ""
        txt = _clip(r.name, 120) + qty + ((" — " + _clip(r.note, 200)) if r.note else "")
        inv = r.list_type == INVENTORY
        out.append({"source": "inventory" if inv else "list",
                    "label": ("инвентарь: " + _clip(r.category, 40)) if inv else ("список: " + _clip(r.group_name, 40)),
                    "text": _clip(txt, MAX_SNIPPET), "fts": float(r.fts or 0),
                    "cos": _cos(qvec, vecs[r.id]) if qvec and r.id in vecs else None})
    return out


def search_sync(user_id: str, q: str, qvec: Optional[List[float]]) -> Dict[str, Any]:
    tsq = sitter_fts_query(q)
    scored = []
    for cnd in _candidates(user_id, tsq, qvec) if user_id else []:
        cos, fts = cnd["cos"], cnd["fts"]
        if (cos is not None and cos >= VEC_MIN) or fts >= FTS_MIN:
            scored.append(((cos or 0.0) + 0.5 * min(fts, 1.0), cnd))
    scored.sort(key=lambda x: -x[0])
    hits, seen = [], set()
    for _, cnd in scored:
        key = cnd["text"].casefold()
        if key in seen:
            continue
        seen.add(key)
        hits.append({"source": cnd["source"], "label": cnd["label"], "text": cnd["text"]})
        if len(hits) >= MAX_HITS:
            break
    return {"found": bool(hits), "hits": hits}


async def ask(user_id: str, q: str) -> Dict[str, Any]:
    q = clean_query(q)
    if not q:
        return {"found": False, "hits": []}
    try:
        vecs = await asyncio.to_thread(_embed, q, "query")
    except Exception:
        logger.warning("sitter_search: query embedding failed", exc_info=True)
        vecs = []
    qvec = vecs[0] if vecs else None
    res = await asyncio.to_thread(search_sync, user_id, q, qvec)
    logger.info("sitter_search: qlen=%d vector=%s hits=%d", len(q), "yes" if qvec else "no", len(res["hits"]))
    return res
