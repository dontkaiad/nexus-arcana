"""core/repos/pg_cats_repo.py — Cats subject storage (карточка кота; факты живут в memories.cat_id).

Sync SQLAlchemy Core, как pg_people_repo; async-вызывающие оборачивают в asyncio.to_thread.
Нормализатор имён и склонений — тот же, что у людей (core.people.aliases_norm_for / mention_candidates),
поэтому «Тучке» находит «Тучка» ровно так же, как «Оле» находит «Оля».
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import insert, select, text, update

from core import cat_blocks
from core.db import get_engine
from core.people import aliases_norm_for, mention_candidates
from core.repos.cats_tables import CAT_STATUSES, cats

EDITABLE = ("name", "aliases", "photo_url", "status")


def _row(r) -> Dict:
    return dict(r._mapping)


def sync_norm(conn, cat_id: int) -> None:
    """Пересчитывает aliases_norm из текущих name+aliases (в той же транзакции после их изменения)."""
    r = conn.execute(text("SELECT name, aliases FROM cats WHERE id = :i"), {"i": cat_id}).first()
    if r:
        conn.execute(text("UPDATE cats SET aliases_norm = :n WHERE id = :i"),
                     {"n": aliases_norm_for(r.name, r.aliases or []), "i": cat_id})


def create(user_id: str, name: str, aliases: Optional[List[str]] = None, *,
           photo_url: Optional[str] = None, status: str = "confirmed") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("cat name is empty")
    if status not in CAT_STATUSES:
        raise ValueError("bad status")
    with get_engine().begin() as c:
        cid = c.execute(insert(cats).values(
            user_id=user_id, name=name, aliases=[a.strip() for a in (aliases or []) if a and a.strip()],
            photo_url=photo_url, status=status,
        ).returning(cats.c.id)).scalar_one()
        sync_norm(c, cid)
    return cid


def get_cat(user_id: str, cat_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(cats).where(cats.c.id == cat_id, cats.c.user_id == user_id)).first()
    return _row(r) if r else None


def list_cats(user_id: str, status: Optional[str] = None) -> List[Dict]:
    """Коты владельца (по умолчанию все, кроме отклонённых), стабильно по id."""
    q = select(cats).where(cats.c.user_id == user_id).order_by(cats.c.id)
    q = q.where(cats.c.status == status) if status else q.where(cats.c.status != "rejected")
    with get_engine().connect() as c:
        return [_row(r) for r in c.execute(q)]


def update_cat(user_id: str, cat_id: int, values: Dict) -> Optional[Dict]:
    vals = {k: v for k, v in values.items() if k in EDITABLE}
    if "status" in vals and vals["status"] not in CAT_STATUSES:
        raise ValueError("bad status")
    if "name" in vals:
        vals["name"] = (vals["name"] or "").strip()
        if not vals["name"]:
            raise ValueError("cat name is empty")
    if "aliases" in vals:
        vals["aliases"] = [a.strip() for a in (vals["aliases"] or []) if a and a.strip()]
    with get_engine().begin() as c:
        if vals:
            res = c.execute(update(cats).where(cats.c.id == cat_id, cats.c.user_id == user_id)
                            .values(**vals, updated_at=text("now()")))
            if not res.rowcount:
                return None
        if "name" in vals or "aliases" in vals:
            sync_norm(c, cat_id)
        if "name" in vals:                     # related_to у фактов кота = его имя (для старого поиска и контекста)
            c.execute(text("UPDATE memories SET related_to = :n WHERE cat_id = :c AND user_id = :u"),
                      {"n": vals["name"], "c": cat_id, "u": user_id})
    return get_cat(user_id, cat_id)


def resolve_cat(user_id: str, mentions: List[str]) -> List[Dict]:
    """Коты владельца, упомянутые в тексте: пересечение нормализованных форм (GIN &&). Отклонённые не
    резолвятся. Порядок стабилен (по id). Не ровно один результат — решает вызывающий (без догадок)."""
    forms = mention_candidates(mentions)
    if not forms:
        return []
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, name, aliases, photo_url FROM cats "
            "WHERE user_id = :u AND status <> 'rejected' AND aliases_norm && CAST(:f AS text[]) "
            "ORDER BY id"), {"u": user_id, "f": forms}).all()
    return [{"id": r.id, "name": r.name, "aliases": list(r.aliases or []), "photo_url": r.photo_url} for r in rows]


def cat_names(user_id: str) -> List[str]:
    """Имя + клички всех не отклонённых котов владельца — для подсказки промпту parse (без дублей, по порядку)."""
    seen: List[str] = []
    for c in list_cats(user_id):
        for n in [c["name"], *(c.get("aliases") or [])]:
            if n and n not in seen:
                seen.append(n)
    return seen


# ── факты кота ───────────────────────────────────────────────────────────────

CATS_CATEGORY = "🐾 Коты"
_FACT_COLS = ("id, fact_text, key_name, is_current, source, created_at, updated_at, related_to, "
              "scope, review_status, cat_block")


def _fact(r) -> Dict:
    return {"id": r.id, "text": r.fact_text, "key": r.key_name, "is_current": r.is_current, "source": r.source,
            "created_at": r.created_at, "updated_at": r.updated_at, "related_to": r.related_to, "block": r.cat_block}


def _only_sitter(for_sitter: bool) -> str:
    """Добавка к WHERE: для ситтера (читающие токены) только факты, открытые блоком (core.cat_blocks.sitter_sql)."""
    return " AND " + cat_blocks.sitter_sql() if for_sitter else ""


def list_facts(user_id: str, cat_id: int, for_sitter: bool = False) -> List[Dict]:
    """Факты кота: не архивные, не стёртые (пустой текст = tombstone), не rejected; по времени создания. for_sitter — только открытые блоком."""
    with get_engine().connect() as c:
        rows = c.execute(text(
            f"SELECT {_FACT_COLS} FROM memories WHERE cat_id = :c AND user_id = :u AND NOT is_archived "
            "AND fact_text <> '' AND review_status <> 'rejected'" + _only_sitter(for_sitter) + " ORDER BY created_at, id"),
            {"c": cat_id, "u": user_id}).all()
    return [_fact(r) for r in rows]


def list_facts_many(user_id: str, cat_ids: List[int], for_sitter: bool = False) -> Dict[int, List[Dict]]:
    """Факты сразу нескольких котов одним запросом ({cat_id: [факты по времени создания]}); те же условия, что у list_facts."""
    out: Dict[int, List[Dict]] = {int(i): [] for i in cat_ids}
    if not out:
        return out
    with get_engine().connect() as c:
        rows = c.execute(text(
            f"SELECT cat_id, {_FACT_COLS} FROM memories WHERE cat_id = ANY(:ids) AND user_id = :u AND NOT is_archived "
            "AND fact_text <> '' AND review_status <> 'rejected'" + _only_sitter(for_sitter) + " ORDER BY created_at, id"),
            {"ids": list(out), "u": user_id}).all()
    for r in rows:
        out[int(r.cat_id)].append(_fact(r))
    return out


def list_general_facts(user_id: str) -> List[Dict]:
    """«Общее про котов»: категория «🐾 Коты» без cat_id (про всех сразу или про кота без карточки).
    Факты карточки «Я» владельца показываются (их можно привязать к коту, person_id при этом снимается); факты другого человека
    принадлежат его карточке и сюда не попадают: привязать их к коту нельзя (attach_fact → taken)."""
    with get_engine().connect() as c:
        rows = c.execute(text(
            f"SELECT {_FACT_COLS} FROM memories WHERE category = :cat AND cat_id IS NULL AND user_id = :u "
            "AND NOT is_archived AND fact_text <> '' AND review_status <> 'rejected' "
            "AND (person_id IS NULL OR person_id IN (SELECT id FROM people WHERE user_id = :u AND is_self)) ORDER BY created_at, id"),
            {"cat": CATS_CATEGORY, "u": user_id}).all()
    return [_fact(r) for r in rows]


def list_general_facts_for_sitter(user_id: str) -> List[Dict]:
    """«Общее про котов» для ситтера: только актуальные, без привязки к кому-либо (ни к коту, ни к карточке человека, в том числе «Я»), по времени создания."""
    with get_engine().connect() as c:
        rows = c.execute(text(
            f"SELECT {_FACT_COLS} FROM memories WHERE category = :cat AND cat_id IS NULL AND person_id IS NULL AND user_id = :u "
            "AND is_current AND NOT is_archived AND fact_text <> '' AND review_status <> 'rejected' AND " + cat_blocks.sitter_sql() + " ORDER BY created_at, id"),
            {"cat": CATS_CATEGORY, "u": user_id}).all()
    return [_fact(r) for r in rows]


def set_fact_block(user_id: str, memory_id: int, block: str) -> bool:
    """Блок факта про котов (core.cat_blocks) руками владельца. Только факт владельца в категории «🐾 Коты», который привязан к коту или общий
    (факт карточки человека сюда не относится: у него блок не ставится, он остаётся закрытым); архивные, стёртые и отклонённые не трогаем.
    Возвращает False, если такого факта нет."""
    with get_engine().begin() as c:
        res = c.execute(text(
            "UPDATE memories SET cat_block = :b, updated_at = now() WHERE id = :id AND user_id = :u AND category = :cat AND NOT is_archived "
            "AND fact_text <> '' AND review_status <> 'rejected' AND (cat_id IS NOT NULL OR person_id IS NULL)"),
            {"b": block, "id": memory_id, "u": user_id, "cat": CATS_CATEGORY})
    return res.rowcount == 1


def facts_count(user_id: str, cat_ids: List[int], for_sitter: bool = False) -> Dict[int, int]:
    """{cat_id: число видимых фактов} (те же условия, что у list_facts); коты без фактов — 0."""
    out = {int(i): 0 for i in cat_ids}
    if not out:
        return out
    with get_engine().connect() as c:
        for r in c.execute(text(
                "SELECT cat_id, count(*) AS n FROM memories WHERE cat_id = ANY(:ids) AND user_id = :u AND NOT is_archived "
                "AND fact_text <> '' AND review_status <> 'rejected'" + _only_sitter(for_sitter) + " GROUP BY cat_id"),
                {"ids": list(out), "u": user_id}):
            out[int(r.cat_id)] = int(r.n)
    return out


def name_taken(user_id: str, name: str, exclude_id: Optional[int] = None) -> bool:
    """Есть ли у владельца другой не отклонённый кот с таким именем (без учёта регистра)."""
    with get_engine().connect() as c:
        return c.execute(text(
            "SELECT 1 FROM cats WHERE user_id = :u AND status <> 'rejected' AND lower(name) = lower(:n) "
            "AND (CAST(:x AS bigint) IS NULL OR id <> :x) LIMIT 1"),
            {"u": user_id, "n": name.strip(), "x": exclude_id}).first() is not None


def attach_fact(user_id: str, cat: Dict, memory_id: int) -> str:
    """Привязывает «общий» факт категории «🐾 Коты» к коту: cat_id, related_to = имя, ключ cat_<id>_<тип>_<hash>.
    Факт карточки «Я» владельца (person_id = людей.is_self) тоже привязывается: он попал в «Коты» сменой категории, а person_id остался;
    person_id снимается, факт подтверждается (владелец указал сам), эмбеддинг сбрасывается (в нём имя). Факт другого человека — taken.
    Статусы: ok | same (уже привязан к этому коту) | not_found | not_cat_fact | taken (другой кот/человек) |
    conflict (у кота уже есть такой же факт)."""
    import re
    from core.cats import FACT_TYPES, cat_fact_key, fact_kind
    with get_engine().begin() as c:
        r = c.execute(text(
            "SELECT m.id, m.fact_text, m.key_name, m.category, m.cat_id, m.person_id, COALESCE(p.is_self, false) AS self_card "
            "FROM memories m LEFT JOIN people p ON p.id = m.person_id AND p.user_id = m.user_id "
            "WHERE m.id = :i AND m.user_id = :u AND NOT m.is_archived AND m.fact_text <> '' FOR UPDATE OF m"),
            {"i": memory_id, "u": user_id}).first()
        if not r:
            return "not_found"
        if r.cat_id == cat["id"]:
            return "same"
        if r.cat_id is not None or (r.person_id is not None and not r.self_card):
            return "taken"
        if r.category != CATS_CATEGORY:
            return "not_cat_fact"
        kind = fact_kind(r.key_name, r.fact_text)
        m = re.match(r"^person_\d+_([a-z]+)_[0-9a-f]{8}$", r.key_name or "")
        if r.person_id is not None and m and m.group(1) in FACT_TYPES:
            kind = m.group(1)                                                  # тип из ключа карточки «Я», не угадываем заново
        key = cat_fact_key(cat["id"], kind, r.fact_text)
        if c.execute(text("SELECT 1 FROM memories WHERE key_name = :k AND user_id = :u AND NOT is_archived AND id <> :i"),
                     {"k": key, "u": user_id, "i": memory_id}).first():
            return "conflict"
        if r.person_id is None:
            c.execute(text("UPDATE memories SET cat_id = :c, related_to = :n, key_name = :k, updated_at = now() "
                           "WHERE id = :i"), {"c": cat["id"], "n": cat["name"], "k": key, "i": memory_id})
        else:
            c.execute(text("UPDATE memories SET cat_id = :c, person_id = NULL, related_to = :n, key_name = :k, "
                           "review_status = 'confirmed', embedding = NULL, updated_at = now() WHERE id = :i"),
                      {"c": cat["id"], "n": cat["name"], "k": key, "i": memory_id})
    return "ok"
