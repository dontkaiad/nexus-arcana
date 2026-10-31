"""core/memory_search.py — ЕДИНЫЙ информационный поиск по 🧠 Память.

Один путь для `search_memory` (бот), `get_memories_for_context` (контекст в
промпты) и Mini App `GET /memory?q=`:

    FTS top-20 (Postgres, 'russian', ts_rank)  ┐
                                               ├─ RRF ─ [Haiku-реранк] ─ top-k
    vector top-20 (Voyage + pgvector, cosine)  ┘

Реранк Haiku — опциональный (`rerank=True` для search_memory/Mini App; контекстный
путь идёт без него: дальше всё равно LLM, она сама отфильтрует).

Фильтры живут ровно в одном месте (`_FILTERS_SQL`) и общие для обеих ног:
is_current, не is_archived, review_status='confirmed', НЕ rejected-человек,
НЕ tombstone (fact_text = '' — «Стереть» из карточки человека).

Деструктивные пути (забудь / удали / деактивация / алиасы) этот модуль НЕ
используют — там остаётся точный ILIKE (`PgMemoryRepo.search(match_all=True)`).

Этап 2 — `answer()`: сначала `core.query_parse` разбирает вопрос (субъект + тип), и если
структура ясна — прямой запрос по person_id + type (без гибрида); гибрид только когда
структурно не разобрать, либо для фактов «Я» (у них тип не хранится) — внутри моих фактов
со строгим реранком. `person_ids` / `subject_self` сужают гибрид; `trace` — колбэк шагов.

Graceful: нет ключа Voyage → только FTS; сбой Haiku → порядок RRF; сбой БД → [].
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import sqlalchemy as sa

from core.claude_client import ask_claude
from core.db import get_engine
from core.people import owner_fact
from core.rag import _embed, _vec_literal
from core.repos.memories_table import memories
from core.repos.pg_memory_repo import Memory, _row_to_memory

logger = logging.getLogger("core.memory_search")

FTS_TOP = 20
VEC_TOP = 20
RRF_K = 60
RERANK_MAX_CANDIDATES = 25
DEFAULT_K = 5

# Служебные и вопросительные слова: в FTS-запросе только шум. «не» нарочно тут же —
# русская конфигурация и так считает его стоп-словом; негацию («что я не ем»)
# несёт вектор, поэтому поиск всегда гибридный.
_STOP = {
    "про", "о", "об", "и", "не", "это", "что", "как", "из", "по", "для", "на", "в",
    "с", "к", "у", "за", "от", "я", "мне", "мой", "моя", "мои", "ли", "а", "но",
    "или", "когда", "где", "кто", "какой", "какая", "какие", "есть", "был", "была",
    "было", "были", "меня", "мы", "ты", "он", "она", "они",
}
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Единые фильтры чтения. `fact_text <> ''` — tombstone («Стереть»: текст очищен,
# ключ + review_status='rejected' остаются). review_status='confirmed' уже режет
# tombstone, второе условие — страховка на случай ручной правки статуса.
_FILTERS_SQL = """
    is_current = true
    AND is_archived = false
    AND review_status = 'confirmed'
    AND fact_text <> ''
    AND NOT EXISTS (SELECT 1 FROM people p
                    WHERE p.id = memories.person_id AND p.status = 'rejected')
"""


@dataclass
class Hit:
    memory_id: int
    score: float                      # RRF-сумма (после реранка — всё равно RRF, позиция = порядок)
    memory: Optional[Memory] = None   # заполняется после загрузки строк
    fts_rank: Optional[int] = None    # 1-based позиция в FTS-выдаче
    vec_rank: Optional[int] = None    # 1-based позиция в векторной выдаче
    vec_score: Optional[float] = None  # cosine similarity
    reranked: bool = False


Trace = Callable[[str, dict], None]


def fts_query(query: str) -> str:
    """Запрос → строка для to_tsquery('russian', …): слова через ' | '.
    Сырые слова (без нашего падежного стеммера — иначе двойной стемминг), без
    стоп-слов. Только \\w+, так что инъекции в синтаксис tsquery невозможны."""
    words: List[str] = []
    seen = set()
    for w in _WORD_RE.findall((query or "").lower()):
        if len(w) < 2 or w in _STOP or w in seen:
            continue
        seen.add(w)
        words.append(w)
    return " | ".join(words)


def _scope_sql(scope: str, params: dict, user_id: str) -> str:
    sql = ""
    if scope and scope != "global":
        sql += " AND (scope = :scope OR scope = 'global')"
        params["scope"] = scope
    if user_id:
        sql += " AND user_id = :uid"
        params["uid"] = user_id
    return sql


def _person_sql(person_ids: Optional[Sequence[int]], params: dict, subject_self: bool = False) -> str:
    """person_ids — только факты этих людей; subject_self — только факты владельца: без person_id
    ЛИБО привязанные к карточке «Я»."""
    if subject_self:
        return (" AND (person_id IS NULL OR EXISTS (SELECT 1 FROM people s "
                "WHERE s.id = memories.person_id AND s.is_self))")
    if not person_ids:
        return ""
    params["pids"] = [int(p) for p in person_ids]
    return " AND person_id = ANY(:pids)"


def _fts_leg(query: str, scope: str, user_id: str, person_ids, n: int, subject_self: bool = False) -> List[int]:
    tsq = fts_query(query)
    if not tsq:
        return []
    params: dict = {"tsq": tsq, "n": n}
    extra = _scope_sql(scope, params, user_id) + _person_sql(person_ids, params, subject_self)
    sql = sa.text(f"""
        SELECT id FROM memories, to_tsquery('russian', :tsq) q
        WHERE fts @@ q AND {_FILTERS_SQL} {extra}
        ORDER BY ts_rank(fts, q) DESC, id DESC
        LIMIT :n
    """)
    try:
        with get_engine().connect() as conn:
            return [r[0] for r in conn.execute(sql, params).all()]
    except Exception as e:
        logger.warning("memory_search FTS failed: %s", e)
        return []


def _vector_leg(query: str, scope: str, user_id: str, person_ids, n: int, subject_self: bool = False) -> List[tuple]:
    """[(id, cosine_similarity)] по убыванию близости."""
    vecs = _embed(query, input_type="query")
    if not vecs:
        return []
    params: dict = {"q": _vec_literal(vecs[0]), "n": n}
    extra = _scope_sql(scope, params, user_id) + _person_sql(person_ids, params, subject_self)
    sql = sa.text(f"""
        SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score
        FROM memories
        WHERE embedding IS NOT NULL AND {_FILTERS_SQL} {extra}
        ORDER BY embedding <=> CAST(:q AS vector)
        LIMIT :n
    """)
    try:
        with get_engine().connect() as conn:
            return [(r[0], float(r[1])) for r in conn.execute(sql, params).all()]
    except Exception as e:
        logger.warning("memory_search vector failed: %s", e)
        return []


def _load(ids: List[int]) -> Dict[int, Memory]:
    if not ids:
        return {}
    q = sa.select(memories).where(memories.c.id.in_(ids))
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return {int(r.id): _row_to_memory(r) for r in rows}


def rrf_fuse(fts_ids: Sequence[int], vec: Sequence[tuple], k: int = RRF_K) -> List[Hit]:
    """Reciprocal Rank Fusion. Возвращает Hit без Memory (грузится позже)."""
    by_id: Dict[int, Hit] = {}

    def _hit(i: int) -> Hit:
        if i not in by_id:
            by_id[i] = Hit(memory_id=i, score=0.0)
        return by_id[i]

    for rank, i in enumerate(fts_ids, 1):
        h = _hit(i)
        h.fts_rank = rank
        h.score += 1.0 / (k + rank)
    for rank, (i, sc) in enumerate(vec, 1):
        h = _hit(i)
        h.vec_rank = rank
        h.vec_score = sc
        h.score += 1.0 / (k + rank)
    return sorted(by_id.values(), key=lambda h: (-h.score, -h.memory_id))


# ── Haiku-реранк ──────────────────────────────────────────────────────────────

_RERANK_SYSTEM = (
    "Ты ранжируешь кандидатов поиска по личным заметкам пользователя (🧠 Память). "
    "Дан запрос и кандидаты с полями id/факт/категория/связь. Факты о людях "
    "начинаются с имени человека. Категория и связь — контекст, которого не хватает "
    "самому тексту: например категория «🐾 Коты» рядом со словом «луна» — кличка "
    "питомца.\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Отрицание в запросе и утверждение в факте (или наоборот) = НЕрелевантно: "
    "на «что я не ем» факт «люблю рыбу» не подходит; на «что любит X» факт «X не любит …» не подходит.\n"
    "2. Факт о другом человеке, чем в запросе, = нерелевантно. Если в запросе нет "
    "человека, а факт про конкретного другого человека — тоже нерелевантно.\n"
    "3. Совпадение слова недостаточно: нужен тот же смысл. Лучше вернуть меньше "
    "или [] — пустой ответ лучше неверного.\n"
    "Верни ТОЛЬКО JSON-массив id релевантных кандидатов, от самого релевантного "
    "к менее релевантному, не больше N штук. Если релевантных нет — []. Никакого "
    "текста до или после JSON, никакого markdown."
)


def _candidate_line(m: Memory) -> str:
    parts = [f"id={m.id}", f'факт="{owner_fact(m.fact, m.related_to, m.person_id)}"']
    if m.category:
        parts.append(f'категория="{m.category}"')
    if m.related_to:
        parts.append(f'связь="{m.related_to}"')
    return " ".join(parts)


async def rerank(query: str, hits: List[Hit], top_n: int, hint: str = "") -> List[Hit]:
    """Haiku упорядочивает/отсекает кандидатов. Валидный `[]` = «ничего
    релевантного» → []. Сбой Haiku/битый JSON → порядок RRF (top_n)."""
    if not hits:
        return []
    cands = hits[:RERANK_MAX_CANDIDATES]
    prompt = (
        f"Запрос пользователя: {query!r}\nN = {top_n}\n"
        + (f"Разбор запроса: {hint}\n" if hint else "")
        + "\nКандидаты:\n"
        + "\n".join(_candidate_line(h.memory) for h in cands)
    )
    try:
        raw = await ask_claude(
            prompt=prompt, system=_RERANK_SYSTEM,
            model="claude-haiku-4-5-20251001", max_tokens=120, temperature=0,
        )
        cleaned = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        ids = json.loads(cleaned)
        if not isinstance(ids, list):
            raise ValueError("rerank JSON is not a list")
    except Exception as e:
        logger.warning("memory_search rerank failed (%s) → RRF order", e)
        return hits[:top_n]
    by_id = {str(h.memory.id): h for h in cands}
    out: List[Hit] = []
    for i in ids:
        h = by_id.pop(str(i), None)
        if h is not None:
            h.reranked = True
            out.append(h)
        if len(out) >= top_n:
            break
    return out


# ── Публичный API ─────────────────────────────────────────────────────────────

async def hybrid_search(
    query: str,
    user_id: str = "",
    scope: str = "",
    k: int = DEFAULT_K,
    do_rerank: bool = True,
    person_ids: Optional[Sequence[int]] = None,
    trace: Optional[Trace] = None,
    subject_self: bool = False,
    hint: str = "",
) -> List[Hit]:
    """FTS top-20 + vector top-20 → RRF → [Haiku-реранк] → top-k."""
    query = (query or "").strip()
    if not query:
        return []

    def _t(step: str, data: dict) -> None:
        if trace:
            try:
                trace(step, data)
            except Exception:  # трейс не должен ронять поиск
                pass

    fts_ids, vec = await asyncio.gather(
        asyncio.to_thread(_fts_leg, query, scope, user_id, person_ids, FTS_TOP, subject_self),
        asyncio.to_thread(_vector_leg, query, scope, user_id, person_ids, VEC_TOP, subject_self),
    )
    hits = rrf_fuse(fts_ids, vec)
    _t("legs", {"fts": list(fts_ids), "vec": [i for i, _ in vec]})
    if not hits:
        return []
    loaded = await asyncio.to_thread(_load, [h.memory_id for h in hits])
    final: List[Hit] = []
    for h in hits:
        m = loaded.get(h.memory_id)
        if m is None:   # строка исчезла/сменилась между запросами
            continue
        h.memory = m
        final.append(h)
    _t("rrf", {"ids": [h.memory.id for h in final]})
    if do_rerank:
        final = await rerank(query, final, k, hint)
        _t("rerank", {"ids": [h.memory.id for h in final]})
    else:
        final = final[:k]
    logger.info(
        "hybrid_search: q=%r rerank=%s ids=%s", query[:60], do_rerank,
        [(h.memory.id, round(h.score, 4), h.fts_rank, h.vec_rank) for h in final],
    )
    return final


async def search_memories(query: str, **kw) -> List[Memory]:
    """Удобная обёртка: только Memory, без метаданных."""
    return [h.memory for h in await hybrid_search(query, **kw)]


# ── Этап 2: понимание вопроса до поиска ───────────────────────────────────────

from core import query_parse  # noqa: E402  (после определения Hit/гибрида — нет циклов)

DIRECT_LIMIT = 20
_TYPE_SQL = "COALESCE(substring(key_name from '^person_[0-9]+_([a-z]+)_[0-9a-f]{8}$'), 'other')"


@dataclass
class Group:
    """Блок результата: один человек (тёзки → несколько блоков) либо общий набор (person_id=None)."""
    label: str = ""
    person_id: Optional[int] = None
    hits: List[Hit] = field(default_factory=list)
    birthday: str = ""        # «ДР: 05.03» (intent=birthday)


@dataclass
class Result:
    route: str                # direct | birthday | hybrid | hybrid_self | hybrid_person | unknown_person | none
    kind: str                 # facts | birthday | none | unknown_person
    subject: str              # self | person | ambiguous | unknown_person | unclear
    types: Tuple[str, ...] = ()
    groups: List[Group] = field(default_factory=list)
    unknown_name: str = ""

    @property
    def hits(self) -> List[Hit]:
        return [h for g in self.groups for h in g.hits]

    @property
    def memories(self) -> List[Memory]:
        return [h.memory for h in self.hits]


def understanding_enabled() -> bool:
    return os.environ.get("MEMORY_QUERY_UNDERSTANDING", "1").strip().lower() not in ("0", "false", "off", "no")


def _resolve_people(user_id: str, mentions: List[str]) -> List[Dict]:
    from core.repos import pg_people_repo as prepo
    return prepo.resolve(user_id, mentions)


def _word_in_facts(user_id: str, word: str) -> bool:
    """Слово встречается в подтверждённых фактах владельца → это скорее предмет, а не неизвестный человек."""
    tsq = fts_query(word)
    if not tsq:
        return False
    params: dict = {"tsq": tsq}
    extra = _scope_sql("", params, user_id)
    sql = sa.text(f"SELECT 1 FROM memories, to_tsquery('russian', :tsq) q "
                  f"WHERE fts @@ q AND {_FILTERS_SQL} {extra} LIMIT 1")
    try:
        with get_engine().connect() as conn:
            return conn.execute(sql, params).first() is not None
    except Exception:
        return True       # при сомнении не объявляем «такого человека нет»


def _people_labels(person_ids: Sequence[int]) -> Dict[int, str]:
    if not person_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text("SELECT id, name, notes FROM people WHERE id = ANY(:i)"),
                            {"i": [int(i) for i in person_ids]}).all()
    out = {}
    for r in rows:
        note = " ".join((r.notes or "").split())[:40]
        out[r.id] = f"{r.name} ({note})" if note else r.name
    return out


def effective_type(fact_text: str, stored: str) -> str:
    """Тип факта для выдачи: ТЕКСТ перекрывает тип, поставленный Haiku при импорте, где лексикон сработал
    («не любит X» не бывает likes); где не сработал — остаётся сохранённый тип."""
    return query_parse.fact_type_from_text(fact_text) or stored


def _direct_facts(person_id: int, types: Sequence[str], user_id: str) -> List[Memory]:
    """Подтверждённые факты человека нужных типов, важные первыми. Только общие фильтры чтения
    (confirmed, не tombstone, не rejected-человек, is_current, не archived). Тип — по тексту (лексикон),
    а где лексикон молчит — по ключу person_<id>_<type>_<hash>."""
    params: dict = {"pid": int(person_id)}
    extra = _scope_sql("", params, user_id)
    sql = sa.text(f"""
        SELECT id, {_TYPE_SQL} AS t FROM memories
        WHERE person_id = :pid AND {_FILTERS_SQL} {extra}
        ORDER BY id
    """)
    with get_engine().connect() as conn:
        rows = [(r[0], r[1]) for r in conn.execute(sql, params).all()]
    loaded = _load([i for i, _ in rows])
    rank = {"important": 0, "likes": 1, "dislikes": 2}
    picked = []
    for i, stored in rows:
        m = loaded.get(i)
        if m is None:
            continue
        t = effective_type(m.fact, stored)
        if t in types:
            picked.append((rank.get(t, 3), i, m))
    picked.sort(key=lambda x: (x[0], x[1]))
    return [m for _, _, m in picked[:DIRECT_LIMIT]]


def _hint_for(plan) -> str:
    bits = [f"субъект={plan.subject}"]
    if plan.types:
        bits.append("тип=" + "/".join(plan.types))
    if plan.negated:
        bits.append("в запросе есть отрицание")
    return ", ".join(bits)


async def answer(query: str, user_id: str = "", scope: str = "", k: int = DEFAULT_K,
                 do_rerank: bool = True, trace: Optional[Trace] = None) -> Result:
    """Единая точка входа информационного поиска: понять вопрос → прямой запрос ИЛИ гибрид.

    Прямой путь (субъект-человек + тип, либо ДР): без Voyage и Haiku; пусто → kind='none'
    («не знаю»), а НЕ похожие факты. «Я» → гибрид внутри моих фактов со строгим реранком.
    Неясно → обычный гибрид этапа 1."""
    query = (query or "").strip()
    if not query:
        return Result(route="none", kind="none", subject="unclear")

    def _t(step: str, data: dict) -> None:
        if trace:
            try:
                trace(step, data)
            except Exception:
                pass

    async def _hybrid(route: str, subject: str, types=(), **kw) -> Result:
        hits = await hybrid_search(query, user_id=user_id, scope=scope, k=k, do_rerank=do_rerank,
                                   trace=trace, **kw)
        kind = "facts" if hits else "none"
        return Result(route=route, kind=kind, subject=subject, types=tuple(types),
                      groups=[Group(hits=hits)] if hits else [])

    if not understanding_enabled():
        return await _hybrid("hybrid", "unclear")

    plan = await asyncio.to_thread(
        query_parse.parse, query,
        lambda cand: _resolve_people(user_id, cand),
        lambda w: _word_in_facts(user_id, w),
    )
    _t("parse", {"subject": plan.subject, "types": list(plan.types), "intent": plan.intent,
                 "people": [p["id"] for p in plan.people]})
    hint = _hint_for(plan)

    if plan.subject == "unknown_person":
        return Result(route="unknown_person", kind="unknown_person", subject=plan.subject,
                      types=plan.types, unknown_name=plan.unknown_name)

    if plan.subject in ("person", "ambiguous"):
        labels = await asyncio.to_thread(_people_labels, [p["id"] for p in plan.people])
        if plan.intent == "birthday":
            from core.people import birthday_context
            groups = [Group(label=labels.get(p["id"], p["name"]), person_id=p["id"],
                            birthday=birthday_context(p["birthday"], p["birthday_year_known"]))
                      for p in plan.people]
            has = any(g.birthday for g in groups)
            return Result(route="birthday", kind="birthday" if has else "none", subject=plan.subject,
                          groups=groups)
        if plan.types:
            groups = []
            for p in plan.people:
                mems = await asyncio.to_thread(_direct_facts, p["id"], plan.types, user_id)
                groups.append(Group(label=labels.get(p["id"], p["name"]), person_id=p["id"],
                                    hits=[Hit(memory_id=int(m.id), score=0.0, memory=m) for m in mems]))
            _t("direct", {"ids": [h.memory.id for g in groups for h in g.hits]})
            any_hits = any(g.hits for g in groups)
            return Result(route="direct", kind="facts" if any_hits else "none", subject=plan.subject,
                          types=plan.types, groups=groups)
        # человек ясен, тип нет → гибрид только внутри его фактов
        return await _hybrid("hybrid_person", plan.subject, plan.types,
                             person_ids=[p["id"] for p in plan.people], hint=hint)

    if plan.subject == "self":
        return await _hybrid("hybrid_self", "self", plan.types, subject_self=True, hint=hint)

    return await _hybrid("hybrid", "unclear", plan.types, hint=hint)
