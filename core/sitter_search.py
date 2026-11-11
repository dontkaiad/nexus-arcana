"""core/sitter_search.py — поиск ситтера «где таблетки» БЕЗ LLM по ограниченному набору источников.

Источники (и только они; SQL написан на фиксированные таблицы, а не фильтр общего поиска по Памяти):
  1. факты про котов (memories.cat_id IS NOT NULL);
  2. «Общее про котов» (категория «🐾 Коты», cat_id пуст, не факт карточки человека);
  оба только открытые блоком (`cat_blocks.sitter_sql`: profile / care / relations, health только «критично»; owner, unsorted, NULL закрыты);
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

from core import cat_blocks
from core.db import get_engine
from core.memory_search import fts_query
from core import sitter_expand
from core.rag import _embed, _vec_literal
from core.repos.lists_table import nexus_lists as nl
from core.repos.pg_sitter_lists_repo import INVENTORY, MAX_LOC, sitter_visible_clause

logger = logging.getLogger("core.sitter_search")

MAX_Q = 200
MAX_HITS = 3
MAX_OFFSET = 30
MAX_EXTRA = 8
MAX_SNIPPET = 300
MAX_ITEMS_EMBEDDED = 400
CATS_CATEGORY = "🐾 Коты"
# Пороги релевантности (подобраны на вымышленном наборе, см. docs/CASES/0034-sitter-ask.md).
VEC_MIN = 0.58      # cosine (voyage-4-lite): 0 ложных на 10 вопросах без ответа, запас 0.011 до ближайшего «соседа»
FTS_MIN = 0.10      # после sitter_fts_query остаются предметные слова: любое совпадение уже даёт ts_rank ≥ 0.3
REL_MIN = 0.8       # фрагмент показывается, только если его балл ≥ 0.8 от лучшего (хвост слабых совпадений обрезается)


# --- sitter_fts_query begin ---
# Общие слова вопросов ситтера: лексически совпадают с чужими пунктами («лежит корм» ≈ «лежит паспорт») и дают ложные срабатывания.
_ASK_STOP = {
    "лежит", "лежат", "лежать", "лёг", "стоит", "стоят", "находится", "находятся", "храню", "хранится", "любит", "любят",
    "можно", "нужно", "надо", "должен", "должна", "дать", "давать", "делать", "сделать", "принять", "взять", "найти", "искать",
    "куда", "когда", "сколько", "почему", "зачем", "чего", "кого", "чем", "если", "часто", "сейчас", "сегодня", "завтра",
    "ли", "бы", "же", "то", "вот", "тут", "там", "здесь", "мне", "нам", "вам", "ему", "ей", "им", "это", "этот", "эта",
}
# Поиск ситтера идёт по базе, где ВСЁ про котов: слово «кот» ничего не различает, зато «корм для котов» вытесняет «наполнитель»
# на вопрос «какой наполнитель у котов». Только для sitter_fts_query (поиск по памяти владельца использует core.memory_search.fts_query).
_CAT_WORDS = {
    "кот", "кота", "коту", "котом", "коте", "коты", "котов", "котам", "котами", "котах", "котик", "котика", "котиков",
    "кошка", "кошки", "кошке", "кошку", "кошкой", "кошек", "кошкам", "кошками", "кошках",
    "котенок", "котёнок", "котята", "котят", "котятам", "кошачий", "кошачья", "кошачье", "кошачьи", "кошачьего", "кошачьей",
}


def sitter_fts_query(q: str) -> str:
    """fts_query без общих слов вопроса (останутся предметные слова: «таблетки», «бинт», «Тучка»)."""
    words = [w for w in fts_query(q).split(" | ") if w and w not in _ASK_STOP and w not in _CAT_WORDS]
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


def _facts_sql(n_extra: int):
    # ts_rank по OR-запросу из N слов разбавляется, поэтому слова расширения ранжируются каждое отдельно, берётся лучший ранг
    ranks = ["CASE WHEN :tsq <> '' THEN ts_rank(m.fts, to_tsquery('russian', :tsq)) ELSE 0 END"]
    ranks += ["ts_rank(m.fts, to_tsquery('russian', :x%d))" % i for i in range(n_extra)]
    return text("""
    SELECT m.id, m.fact_text, c.name AS cat_name, (m.cat_id IS NULL) AS general,
           GREATEST(%s) AS fts,
           CASE WHEN CAST(:q AS text) IS NOT NULL AND m.embedding IS NOT NULL
                THEN 1 - (m.embedding <=> CAST(:q AS vector)) END AS cos
    FROM memories m LEFT JOIN cats c ON c.id = m.cat_id
    WHERE m.user_id = :u
      AND (m.cat_id IS NOT NULL OR m.category = :cat)
      AND m.is_current = true AND m.is_archived = false AND m.review_status = 'confirmed' AND m.fact_text <> ''
      AND (c.id IS NULL OR c.status <> 'rejected')
      AND m.person_id IS NULL AND %s
""" % (", ".join(ranks), cat_blocks.sitter_sql("m")))


def extra_terms(words: Optional[List[str]]) -> List[str]:
    """Слова расширения → предметные слова для FTS (без вопросительных и «котовых» слов), ≤ MAX_EXTRA."""
    out: List[str] = []
    for w in sitter_fts_query(" ".join(words or [])).split(" | "):
        if w and w not in out:
            out.append(w)
    return out[:MAX_EXTRA]


def _candidates(user_id: str, tsq: str, qvec: Optional[List[float]], extra: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with get_engine().connect() as c:
        extra = extra or []
        params = {"tsq": tsq, "q": _vec_literal(qvec) if qvec else None, "u": user_id, "cat": CATS_CATEGORY}
        params.update({"x%d" % i: w for i, w in enumerate(extra)})
        for r in c.execute(_facts_sql(len(extra)), params):
            out.append({"source": "general" if r.general else "fact",
                        "label": "общее про котов" if r.general else "про кота " + _clip(r.cat_name, 40),
                        "text": _clip(r.fact_text, MAX_SNIPPET), "fts": float(r.fts or 0), "cos": float(r.cos) if r.cos is not None else None})
        # вес 'A', как у fact_text в memories.fts: иначе ts_rank пунктов списков в ~10 раз ниже и пороги несравнимы
        doc = func.setweight(func.to_tsvector("russian", func.concat_ws(" ", nl.c.name, nl.c.note, nl.c.category, nl.c.group_name,
                                                                         nl.c.location)), "A")
        ranks = ([func.ts_rank(doc, func.to_tsquery("russian", tsq))] if tsq else []) + \
                [func.ts_rank(doc, func.to_tsquery("russian", w)) for w in extra]
        rank = (func.greatest(*ranks) if len(ranks) > 1 else ranks[0]) if ranks else literal_column("0.0")
        rows = c.execute(select(nl.c.id, nl.c.name, nl.c.note, nl.c.category, nl.c.group_name, nl.c.list_type, nl.c.quantity,
                                nl.c.location, nl.c.updated_at, rank.label("fts")).where(sitter_visible_clause(user_id))).all()
    items = [{"id": r.id, "stamp": str(r.updated_at), "row": r,
              "doc": _clip("%s: %s. %s %s" % (r.category or r.group_name or "", r.name, r.note or "", r.location or ""), 400)}
             for r in rows]
    vecs = _item_vectors(items) if (qvec and items) else {}
    for it in items:
        r = it["row"]
        qty = (" × %g" % float(r.quantity)) if r.quantity is not None else ""
        txt = _clip(r.name, 120) + qty + ((" — " + _clip(r.note, 200)) if r.note else "")
        inv = r.list_type == INVENTORY
        out.append({"source": "inventory" if inv else "list",
                    "label": ("инвентарь: " + _clip(r.category, 40)) if inv else ("список: " + _clip(r.group_name, 40)),
                    "text": _clip(txt, MAX_SNIPPET), "fts": float(r.fts or 0),
                    "cos": _cos(qvec, vecs[r.id]) if qvec and r.id in vecs else None,
                    **({"location": _clip(r.location, MAX_LOC)} if inv else {})})
    return out


def search_sync(user_id: str, q: str, qvec: Optional[List[float]], offset: int = 0, words: Optional[List[str]] = None) -> Dict[str, Any]:
    """{found, hits (≤ MAX_HITS начиная с offset), more}: more — сколько ещё прошло пороги сверх показанных."""
    tsq = sitter_fts_query(q)
    scored = []
    for cnd in _candidates(user_id, tsq, qvec, extra_terms(words)) if user_id else []:
        cos, fts = cnd["cos"], cnd["fts"]
        if (cos is not None and cos >= VEC_MIN) or fts >= FTS_MIN:
            scored.append(((cos or 0.0) + 0.5 * min(fts, 1.0), cnd))
    scored.sort(key=lambda x: -x[0])
    uniq, seen = [], set()
    for score, cnd in scored:
        key = cnd["text"].casefold()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((score, cnd))
    if uniq:
        best = uniq[0][0]
        uniq = [x for x in uniq if x[0] >= REL_MIN * best]          # относительный порог: хвост слабых совпадений не показываем
    offset = max(0, min(int(offset or 0), MAX_OFFSET))
    page = uniq[offset:offset + MAX_HITS]
    hits = [{k: v for k, v in cnd.items() if k in ("source", "label", "text", "location")} for _, cnd in page]
    return {"found": bool(hits), "hits": hits, "more": max(0, len(uniq) - offset - len(page))}


async def ask(user_id: str, q: str, offset: int = 0) -> Dict[str, Any]:
    q = clean_query(q)
    if not q:
        return {"found": False, "hits": [], "more": 0}
    async def _qvec():
        try:
            return await asyncio.to_thread(_embed, q, "query")
        except Exception:
            logger.warning("sitter_search: query embedding failed", exc_info=True)
            return []
    vecs, words = await asyncio.gather(_qvec(), sitter_expand.expand(q))      # расширение не мешает вектору: параллельно
    qvec = vecs[0] if vecs else None
    res = await asyncio.to_thread(search_sync, user_id, q, qvec, offset, words)
    logger.info("sitter_search: qlen=%d vector=%s hits=%d", len(q), "yes" if qvec else "no", len(res["hits"]))
    return res
