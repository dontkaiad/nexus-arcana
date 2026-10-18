"""core/repos/pg_people_repo.py — People domain storage (ADR-0027). Sync SQLAlchemy Core;
async callers wrap in asyncio.to_thread. API/merge land in later PRs."""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import and_, func, insert, select, text, update

from core.db import get_engine
from core.repos.people_tables import people


def _row(r) -> Dict:
    return dict(r._mapping)


def find_by_tg(user_id: str, tg_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.user_id == user_id, people.c.tg_id == tg_id))).first()
    return _row(r) if r else None


def find_by_vk(user_id: str, vk_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.user_id == user_id, people.c.vk_id == vk_id))).first()
    return _row(r) if r else None


def find_named(user_id: str) -> List[Dict]:
    """Все не отклонённые люди — для матча id-less строк по имени/алиасу (после merge
    имя склеенного источника живёт алиасом цели)."""
    with get_engine().connect() as c:
        rows = c.execute(select(people).where(and_(
            people.c.user_id == user_id, people.c.status != "rejected"))).all()
    return [_row(r) for r in rows]


def find_idless(user_id: str) -> List[Dict]:
    """Люди без tg_id и vk_id — кандидаты для повторного матча по имени."""
    with get_engine().connect() as c:
        rows = c.execute(select(people).where(and_(
            people.c.user_id == user_id, people.c.tg_id.is_(None), people.c.vk_id.is_(None),
        ))).all()
    return [_row(r) for r in rows]


def create(user_id: str, name: str, aliases: List[str], *, birthday: Optional[date] = None,
           tg_id: Optional[int] = None, tg_username: Optional[str] = None,
           vk_id: Optional[int] = None, status: str = "pending",
           birthday_year_known: bool = True) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(people).values(
            user_id=user_id, name=name, aliases=aliases, birthday=birthday,
            birthday_year_known=birthday_year_known, tg_id=tg_id,
            tg_username=tg_username, vk_id=vk_id, status=status,
        ).returning(people.c.id)).scalar_one()


def fill_missing(person: Dict, *, aliases: List[str], birthday: Optional[date],
                 tg_id: Optional[int], tg_username: Optional[str], vk_id: Optional[int],
                 birthday_year_known: bool = True) -> bool:
    """Дополняет ТОЛЬКО пустые поля и объединяет алиасы; имя/статус не трогает."""
    vals: Dict = {}
    if birthday and not person.get("birthday"):
        vals["birthday"] = birthday
        vals["birthday_year_known"] = birthday_year_known
    for f, v in (("tg_id", tg_id), ("tg_username", tg_username), ("vk_id", vk_id)):
        if v and not person.get(f):
            vals[f] = v
    merged = list(person.get("aliases") or [])
    seen = {a.lower() for a in merged} | {person["name"].lower()}
    for a in aliases:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            merged.append(a)
    if merged != list(person.get("aliases") or []):
        vals["aliases"] = merged
    if not vals:
        return False
    vals["updated_at"] = func.now()
    with get_engine().begin() as c:
        c.execute(update(people).where(people.c.id == person["id"]).values(**vals))
    return True


# ── чтение/правка для API Mini App (PR-4) ───────────────────────────────────

REVIEW_STATUSES = ("pending", "confirmed", "rejected")
FACT_TYPE_ORDER = ("important", "likes", "dislikes", "other")
_FACT_KEY = re.compile(r"^person_\d+_([a-z]+)_[0-9a-f]{8}$")
_TYPE_SQL = "COALESCE(substring(key_name from '^person_[0-9]+_([a-z]+)_[0-9a-f]{8}$'), 'other')"


def fact_type(key_name: str) -> str:
    m = _FACT_KEY.match(key_name or "")
    return m.group(1) if m else "other"


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def counts_for(person_ids: List[int]) -> Dict[int, Dict]:
    """{person_id: {"counts": {status: n}, "by_type": {type: {status: n}}}} (без архивных)."""
    out: Dict[int, Dict] = {pid: {"counts": {s: 0 for s in REVIEW_STATUSES}, "by_type": {}} for pid in person_ids}
    if not person_ids:
        return out
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT person_id, review_status, %s AS t, count(*) FROM memories "
            "WHERE person_id = ANY(:ids) AND NOT is_archived GROUP BY 1, 2, 3" % _TYPE_SQL
        ), {"ids": list(person_ids)}).all()
    for pid, st, t, n in rows:
        out[pid]["counts"][st] = out[pid]["counts"].get(st, 0) + n
        out[pid]["by_type"].setdefault(t, {s: 0 for s in REVIEW_STATUSES})[st] = n
    return out


def list_people(user_id: str, status: Optional[str] = None, q: Optional[str] = None) -> List[Dict]:
    """Люди владельца + счётчики фактов; сортировка: pending-фактов больше → выше.
    Без `status` отклонённые (rejected) скрыты."""
    where, params = ["user_id = :u"], {"u": user_id}
    if status:
        where.append("status = :st")
        params["st"] = status
    else:
        where.append("status <> 'rejected'")
    if q and q.strip():
        where.append("(name ILIKE :q ESCAPE '\\' OR EXISTS "
                     "(SELECT 1 FROM unnest(aliases) a WHERE a ILIKE :q ESCAPE '\\'))")
        params["q"] = _like(q.strip())
    with get_engine().connect() as c:
        rows = [_row(r) for r in c.execute(text(
            "SELECT * FROM people WHERE " + " AND ".join(where)), params)]
    cnt = counts_for([r["id"] for r in rows])
    for r in rows:
        r.update(cnt[r["id"]])
    rows.sort(key=lambda r: (-r["counts"]["pending"], (r["name"] or "").lower(), r["id"]))
    return rows


def get_person(user_id: str, person_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.id == person_id, people.c.user_id == user_id))).first()
    if not r:
        return None
    d = _row(r)
    d.update(counts_for([person_id])[person_id])
    return d


def list_facts(person_id: int) -> List[Dict]:
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, fact_text, key_name, review_status, is_current, source, created_at "
            "FROM memories WHERE person_id = :p AND NOT is_archived ORDER BY created_at, id"
        ), {"p": person_id}).all()
    return [{"id": r.id, "text": r.fact_text, "type": fact_type(r.key_name),
             "review_status": r.review_status, "is_current": r.is_current, "source": r.source,
             "created_at": r.created_at} for r in rows]


EDITABLE = ("name", "aliases", "birthday", "birthday_year_known", "tg_id", "tg_username",
            "vk_id", "notes", "status")


def update_person(user_id: str, person_id: int, values: Dict) -> Optional[Dict]:
    """Частичное обновление. IntegrityError (дубль tg_id/vk_id, CHECK) пробрасывается."""
    vals = {k: v for k, v in values.items() if k in EDITABLE}
    with get_engine().begin() as c:
        if vals:
            vals["updated_at"] = func.now()
            res = c.execute(update(people).where(and_(people.c.id == person_id, people.c.user_id == user_id))
                            .values(**vals))
            if res.rowcount == 0:
                return None
        elif c.execute(select(people.c.id).where(and_(people.c.id == person_id,
                                                      people.c.user_id == user_id))).first() is None:
            return None
    return get_person(user_id, person_id)


def set_fact_status(user_id: str, person_id: int, fact_id: int, status: str) -> bool:
    """Меняет review_status ОДНОГО факта этого человека этого владельца."""
    with get_engine().begin() as c:
        res = c.execute(text(
            "UPDATE memories SET review_status = :st, updated_at = now() "
            "WHERE id = :f AND person_id = :p AND user_id = :u AND NOT is_archived"
        ), {"st": status, "f": fact_id, "p": person_id, "u": user_id})
    return res.rowcount > 0


def review_all(user_id: str, person_id: int, to_status: str) -> Optional[int]:
    """approve-all / reject-all: все pending-факты человека → to_status и
    status человека (confirmed|rejected) — одной транзакцией. None если человека нет."""
    person_status = {"confirmed": "confirmed", "rejected": "rejected"}[to_status]
    with get_engine().begin() as c:
        res = c.execute(update(people).where(and_(people.c.id == person_id, people.c.user_id == user_id))
                        .values(status=person_status, updated_at=func.now()))
        if res.rowcount == 0:
            return None
        r = c.execute(text(
            "UPDATE memories SET review_status = :st, updated_at = now() "
            "WHERE person_id = :p AND user_id = :u AND review_status = 'pending' AND NOT is_archived"
        ), {"st": to_status, "p": person_id, "u": user_id})
    return r.rowcount


# ── merge двух людей (PR-5) ──────────────────────────────────────────────────

class MergeError(Exception):
    """code: not_found | same | conflicting_ids"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_KEY_TAIL = re.compile(r"^person_\d+_([a-z]+_[0-9a-f]{8})$")


def _lock_person(conn, user_id: str, pid: int) -> Optional[Dict]:
    r = conn.execute(select(people).where(and_(people.c.id == pid, people.c.user_id == user_id)).with_for_update()).first()
    return _row(r) if r else None


def _move_facts(conn, user_id: str, src: int, dst: int) -> Dict[str, int]:
    """Факты src → dst. Ключ person_{id}_{type}_{hash8} переписывается на dst; если у dst
    уже есть факт с таким ключом (то же type+hash8 — тот же факт) — остаётся ЦЕЛЕВОЙ,
    факт источника удаляется. Не-импортные ключи переезжают как есть (конфликт — те же правила)."""
    moved = dropped = 0
    rows = conn.execute(text(
        "SELECT id, key_name FROM memories WHERE person_id = :s AND user_id = :u ORDER BY id"
    ), {"s": src, "u": user_id}).all()
    for fid, key in rows:
        m = _KEY_TAIL.match(key or "")
        new_key = "person_%d_%s" % (dst, m.group(1)) if m else key
        clash = new_key and conn.execute(text(
            "SELECT 1 FROM memories WHERE key_name = :k AND user_id = :u AND id <> :i AND NOT is_archived LIMIT 1"
        ), {"k": new_key, "u": user_id, "i": fid}).first()
        if clash:
            conn.execute(text("DELETE FROM memories WHERE id = :i"), {"i": fid})
            dropped += 1
        else:
            conn.execute(text(
                "UPDATE memories SET person_id = :d, key_name = :k, updated_at = now() WHERE id = :i"
            ), {"d": dst, "k": new_key, "i": fid})
            moved += 1
    return {"moved": moved, "dropped_duplicates": dropped}


def _delete_person(conn, pid: int) -> None:
    conn.execute(people.delete().where(people.c.id == pid))


def merge_people(user_id: str, source_id: int, target_id: int) -> Dict:
    """Склеивает source в target ОДНОЙ транзакцией; любое исключение → полный откат.

    Факты переезжают (см. _move_facts); алиасы — union (имя источника тоже становится
    алиасом); пустые поля цели заполняются из источника, заметки при различии склеиваются;
    имя/статус цели не меняются. У обоих разные tg_id (или vk_id) → MergeError('conflicting_ids'):
    это разные аккаунты, и потерянный id при повторном импорте воскресил бы дубль."""
    if source_id == target_id:
        raise MergeError("same")
    with get_engine().begin() as conn:
        first, second = sorted((source_id, target_id))
        locked = {pid: _lock_person(conn, user_id, pid) for pid in (first, second)}
        src, dst = locked[source_id], locked[target_id]
        if not src or not dst:
            raise MergeError("not_found")
        for f in ("tg_id", "vk_id"):
            if src.get(f) and dst.get(f) and src[f] != dst[f]:
                raise MergeError("conflicting_ids")

        stats = _move_facts(conn, user_id, source_id, target_id)
        _delete_person(conn, source_id)          # раньше UPDATE цели: освобождает уникальные tg_id/vk_id

        aliases = list(dst.get("aliases") or [])
        seen = {a.lower() for a in aliases} | {dst["name"].lower()}
        for a in list(src.get("aliases") or []) + [src["name"]]:
            if a and a.lower() not in seen:
                seen.add(a.lower())
                aliases.append(a)
        vals: Dict = {"aliases": aliases, "updated_at": func.now()}
        if src.get("birthday") and not dst.get("birthday"):
            vals["birthday"] = src["birthday"]
            vals["birthday_year_known"] = src.get("birthday_year_known", True)
        for f in ("tg_id", "tg_username", "vk_id"):
            if src.get(f) and not dst.get(f):
                vals[f] = src[f]
        sn, dn = (src.get("notes") or "").strip(), (dst.get("notes") or "").strip()
        if sn and not dn:
            vals["notes"] = sn
        elif sn and dn and sn != dn:
            vals["notes"] = dn + "\n" + sn
        conn.execute(update(people).where(people.c.id == target_id).values(**vals))
    return stats
