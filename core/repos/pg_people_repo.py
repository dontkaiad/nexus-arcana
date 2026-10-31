"""core/repos/pg_people_repo.py — People domain storage (ADR-0027). Sync SQLAlchemy Core;
async callers wrap in asyncio.to_thread.

Аккаунты (TG/VK) лежат в `person_accounts` (PR-7): у человека их может быть несколько, у каждой
платформы один основной (`is_primary`). В словарях людей `accounts` — полный список, а `tg_id`,
`tg_username`, `vk_id` — производные от основных аккаунтов (для UI и инструментов)."""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import get_engine
from core.people import aliases_norm_for, mention_candidates
from core.repos.people_tables import people, person_accounts

PLATFORMS = ("tg", "vk")


def _row(r) -> Dict:
    return dict(r._mapping)


def account_url(platform: str, external_id: int, username: Optional[str]) -> Optional[str]:
    """Ссылка на аккаунт: VK — vk.com/id<id>; TG — t.me/<username>; TG только с id — None (копируется id)."""
    if platform == "vk":
        return "https://vk.com/id%d" % external_id
    return "https://t.me/%s" % username if username else None


def attach_accounts(rows: List[Dict]) -> List[Dict]:
    """Кладёт в каждую строку `accounts` и производные tg_id/tg_username/vk_id (основные аккаунты)."""
    ids = [r["id"] for r in rows]
    by: Dict[int, List[Dict]] = {i: [] for i in ids}
    if ids:
        with get_engine().connect() as c:
            for a in c.execute(select(person_accounts).where(person_accounts.c.person_id.in_(ids))
                               .order_by(person_accounts.c.platform, person_accounts.c.is_primary.desc(),
                                         person_accounts.c.id)):
                by[a.person_id].append({"id": a.id, "platform": a.platform, "external_id": a.external_id,
                                        "username": a.username, "display_name": a.display_name,
                                        "is_primary": a.is_primary})
    for r in rows:
        accs = by.get(r["id"], [])
        r["accounts"] = accs
        tg = next((a for a in accs if a["platform"] == "tg"), None)       # основной идёт первым
        vk = next((a for a in accs if a["platform"] == "vk"), None)
        r["tg_id"] = tg["external_id"] if tg else None
        r["tg_username"] = tg["username"] if tg else None
        r["vk_id"] = vk["external_id"] if vk else None
    return rows


def _accounts_from(tg_id=None, tg_username=None, vk_id=None, accounts=None) -> List[Dict]:
    out: List[Dict] = []
    if tg_id:
        out.append({"platform": "tg", "external_id": int(tg_id), "username": tg_username or None, "is_primary": True})
    if vk_id:
        out.append({"platform": "vk", "external_id": int(vk_id), "username": None, "is_primary": True})
    out += [dict(a) for a in (accounts or [])]
    return out


def _insert_accounts(conn, user_id: str, person_id: int, accs: List[Dict]) -> int:
    """Вставляет аккаунты, пропуская уже существующие (у любого человека этого владельца). Основным
    становится первый аккаунт платформы, если основного у человека ещё нет. Возвращает число вставленных."""
    primary_taken = {r[0] for r in conn.execute(
        select(person_accounts.c.platform).where(and_(person_accounts.c.person_id == person_id,
                                                      person_accounts.c.is_primary)))}
    n = 0
    for a in accs:
        want_primary = bool(a.get("is_primary")) and a["platform"] not in primary_taken
        res = conn.execute(pg_insert(person_accounts).values(
            user_id=user_id, person_id=person_id, platform=a["platform"], external_id=a["external_id"],
            username=a.get("username"), display_name=a.get("display_name") or None, is_primary=want_primary,
        ).on_conflict_do_nothing(index_elements=["user_id", "platform", "external_id"]))
        if res.rowcount:
            n += 1
            if want_primary:
                primary_taken.add(a["platform"])
    # первый аккаунт платформы без основного — делаем основным (даже если флаг не был передан)
    for plat in PLATFORMS:
        if plat not in primary_taken:
            first = conn.execute(select(person_accounts.c.id).where(and_(
                person_accounts.c.person_id == person_id, person_accounts.c.platform == plat))
                .order_by(person_accounts.c.id).limit(1)).scalar()
            if first:
                conn.execute(update(person_accounts).where(person_accounts.c.id == first).values(is_primary=True))
    return n


def find_self(user_id: str) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.user_id == user_id, people.c.is_self))).first()
    return attach_accounts([_row(r)])[0] if r else None


def get_or_create_self(user_id: str, name: str, aliases: List[str], *, tg_id: Optional[int] = None,
                       tg_username: Optional[str] = None, vk_id: Optional[int] = None,
                       accounts: Optional[List[Dict]] = None) -> int:
    """Единственная карточка владельца: status=confirmed, is_self. Есть — дополняем алиасы/аккаунты."""
    me = find_self(user_id)
    if me:
        fill_missing(me, aliases=aliases, birthday=None, tg_id=tg_id, tg_username=tg_username, vk_id=vk_id,
                     accounts=accounts)
        return me["id"]
    pid = create(user_id, name, aliases, tg_id=tg_id, tg_username=tg_username, vk_id=vk_id, accounts=accounts,
                 status="confirmed", is_self=True)
    with get_engine().begin() as c:
        refresh_related(c, pid)
    return pid


def find_by_account(user_id: str, platform: str, external_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).join(person_accounts, person_accounts.c.person_id == people.c.id).where(and_(
            people.c.user_id == user_id, person_accounts.c.user_id == user_id,
            person_accounts.c.platform == platform, person_accounts.c.external_id == external_id))).first()
    return attach_accounts([_row(r)])[0] if r else None


def find_by_tg(user_id: str, tg_id: int) -> Optional[Dict]:
    return find_by_account(user_id, "tg", tg_id)


def find_by_vk(user_id: str, vk_id: int) -> Optional[Dict]:
    return find_by_account(user_id, "vk", vk_id)


def find_named(user_id: str) -> List[Dict]:
    """Все не отклонённые люди."""
    with get_engine().connect() as c:
        rows = c.execute(select(people).where(and_(
            people.c.user_id == user_id, people.c.status != "rejected"))).all()
    return attach_accounts([_row(r) for r in rows])


def find_idless(user_id: str) -> List[Dict]:
    """Люди без единого аккаунта."""
    with get_engine().connect() as c:
        rows = c.execute(select(people).where(and_(
            people.c.user_id == user_id,
            ~select(person_accounts.c.id).where(person_accounts.c.person_id == people.c.id).exists(),
        ))).all()
    return attach_accounts([_row(r) for r in rows])


def sync_norm(conn, person_id: int) -> None:
    """Пересчитывает aliases_norm из текущих name+aliases (вызывать в той же транзакции после их изменения)."""
    r = conn.execute(text("SELECT name, aliases FROM people WHERE id = :i"), {"i": person_id}).first()
    if r:
        conn.execute(text("UPDATE people SET aliases_norm = :n WHERE id = :i"),
                     {"n": aliases_norm_for(r.name, r.aliases or []), "i": person_id})


# ── резолв упоминаний (PR-3) ────────────────────────────────────────────────

FACT_TYPE_RANK = {"important": 0, "likes": 1, "dislikes": 2, "other": 3}


def resolve(user_id: str, mentions: List[str]) -> List[Dict]:
    """Люди владельца, упомянутые в тексте: пересечение нормализованных форм (GIN &&). Отклонённые и «Я»
    не резолвятся. Порядок стабилен (по id). Несколько совпадений = тёзки/общая основа — решает вызывающий."""
    forms = mention_candidates(mentions)
    if not forms:
        return []
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, name, birthday, birthday_year_known FROM people "
            "WHERE user_id = :u AND status <> 'rejected' AND NOT is_self AND aliases_norm && CAST(:f AS text[]) "
            "ORDER BY id"), {"u": user_id, "f": forms}).all()
    return [{"id": r.id, "name": r.name, "birthday": r.birthday, "birthday_year_known": r.birthday_year_known}
            for r in rows]


def confirmed_facts(person_id: int, limit: int = 20) -> List[Dict]:
    """Подтверждённые, актуальные, не стёртые (tombstone = пустой текст) факты; важные первыми."""
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, fact_text, key_name FROM memories WHERE person_id = :p AND review_status = 'confirmed' "
            "AND NOT is_archived AND is_current AND fact_text <> '' ORDER BY id"), {"p": person_id}).all()
    facts = [{"id": r.id, "text": r.fact_text, "type": fact_type(r.key_name)} for r in rows]
    facts.sort(key=lambda f: (FACT_TYPE_RANK.get(f["type"], 9), f["id"]))
    return facts[:limit]


def create(user_id: str, name: str, aliases: List[str], *, birthday: Optional[date] = None,
           tg_id: Optional[int] = None, tg_username: Optional[str] = None,
           vk_id: Optional[int] = None, status: str = "pending",
           birthday_year_known: bool = True, is_self: bool = False,
           accounts: Optional[List[Dict]] = None) -> int:
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(
            user_id=user_id, name=name, aliases=aliases, birthday=birthday,
            birthday_year_known=birthday_year_known, is_self=is_self, status=status,
        ).returning(people.c.id)).scalar_one()
        _insert_accounts(c, user_id, pid, _accounts_from(tg_id, tg_username, vk_id, accounts))
        sync_norm(c, pid)
    return pid


def fill_missing(person: Dict, *, aliases: List[str], birthday: Optional[date],
                 tg_id: Optional[int], tg_username: Optional[str], vk_id: Optional[int],
                 birthday_year_known: bool = True, accounts: Optional[List[Dict]] = None) -> bool:
    """Дополняет ТОЛЬКО пустое: ДР, недостающие аккаунты, алиасы. Имя/статус/основные аккаунты не трогает."""
    vals: Dict = {}
    changed = False
    if birthday and not person.get("birthday"):
        vals["birthday"] = birthday
        vals["birthday_year_known"] = birthday_year_known
    merged = list(person.get("aliases") or [])
    seen = {a.lower() for a in merged} | {person["name"].lower()}
    for a in aliases:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            merged.append(a)
    if merged != list(person.get("aliases") or []):
        vals["aliases"] = merged
    with get_engine().begin() as c:
        if _insert_accounts(c, person["user_id"], person["id"], _accounts_from(tg_id, tg_username, vk_id, accounts)):
            changed = True
        if tg_username:                              # username основного TG, если его ещё нет
            r = c.execute(update(person_accounts).where(and_(
                person_accounts.c.person_id == person["id"], person_accounts.c.platform == "tg",
                person_accounts.c.is_primary, person_accounts.c.external_id == tg_id,
                person_accounts.c.username.is_(None))).values(username=tg_username))
            changed = changed or bool(r.rowcount)
        if vals:
            vals["updated_at"] = func.now()
            c.execute(update(people).where(people.c.id == person["id"]).values(**vals))
            if "aliases" in vals:
                sync_norm(c, person["id"])
            changed = True
    return changed


# ── аккаунты человека (PR-7) ────────────────────────────────────────────────

def add_account(user_id: str, person_id: int, platform: str, external_id: int,
                username: Optional[str] = None, make_primary: bool = False) -> Optional[Dict]:
    """Добавляет аккаунт. Дубль (владелец, платформа, id) → IntegrityError (роут → 409). None, если человека нет."""
    with get_engine().begin() as c:
        if c.execute(select(people.c.id).where(and_(people.c.id == person_id, people.c.user_id == user_id))
                     .with_for_update()).first() is None:
            return None
        has_primary = c.execute(select(person_accounts.c.id).where(and_(
            person_accounts.c.person_id == person_id, person_accounts.c.platform == platform,
            person_accounts.c.is_primary))).first()
        if make_primary and has_primary:
            c.execute(update(person_accounts).where(person_accounts.c.id == has_primary[0]).values(is_primary=False))
        c.execute(insert(person_accounts).values(
            user_id=user_id, person_id=person_id, platform=platform, external_id=external_id,
            username=(username or None), is_primary=bool(make_primary or not has_primary)))
        c.execute(update(people).where(people.c.id == person_id).values(updated_at=func.now()))
    return get_person(user_id, person_id)


def remove_account(user_id: str, person_id: int, account_id: int) -> Optional[Dict]:
    """Удаляет аккаунт; если он был основным — основным становится следующий той же платформы."""
    with get_engine().begin() as c:
        gone = c.execute(person_accounts.delete().where(and_(
            person_accounts.c.id == account_id, person_accounts.c.person_id == person_id,
            person_accounts.c.user_id == user_id)).returning(person_accounts.c.platform,
                                                             person_accounts.c.is_primary)).first()
        if gone is None:
            return None
        if gone.is_primary:
            nxt = c.execute(select(person_accounts.c.id).where(and_(
                person_accounts.c.person_id == person_id, person_accounts.c.platform == gone.platform))
                .order_by(person_accounts.c.id).limit(1)).scalar()
            if nxt:
                c.execute(update(person_accounts).where(person_accounts.c.id == nxt).values(is_primary=True))
    return get_person(user_id, person_id)


def set_primary_account(user_id: str, person_id: int, account_id: int) -> Optional[Dict]:
    with get_engine().begin() as c:
        acc = c.execute(select(person_accounts.c.platform).where(and_(
            person_accounts.c.id == account_id, person_accounts.c.person_id == person_id,
            person_accounts.c.user_id == user_id))).first()
        if acc is None:
            return None
        c.execute(update(person_accounts).where(and_(
            person_accounts.c.person_id == person_id, person_accounts.c.platform == acc.platform,
            person_accounts.c.is_primary)).values(is_primary=False))
        c.execute(update(person_accounts).where(person_accounts.c.id == account_id).values(is_primary=True))
    return get_person(user_id, person_id)


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
            "WHERE person_id = ANY(:ids) AND NOT is_archived AND fact_text <> '' GROUP BY 1, 2, 3" % _TYPE_SQL
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
                     "(SELECT 1 FROM unnest(aliases) a WHERE a ILIKE :q ESCAPE '\\') OR EXISTS "
                     "(SELECT 1 FROM person_accounts pa WHERE pa.person_id = people.id AND pa.username ILIKE :q ESCAPE '\\'))")
        params["q"] = _like(q.strip())
    with get_engine().connect() as c:
        rows = [_row(r) for r in c.execute(text(
            "SELECT * FROM people WHERE " + " AND ".join(where)), params)]
    if not (q and q.strip()) and not any(r["is_self"] for r in rows):
        me = find_self(user_id)                      # карточка «Я» закреплена сверху при любом фильтре
        if me:
            rows.append(me)
    attach_accounts(rows)
    cnt = counts_for([r["id"] for r in rows])
    for r in rows:
        r.update(cnt[r["id"]])
    rows.sort(key=lambda r: (not r["is_self"], -r["counts"]["pending"], (r["name"] or "").lower(), r["id"]))
    return rows


def alias_rows(user_id: str) -> List[Dict]:
    """Минимум полей для аудита «Других имён» (без счётчиков и аккаунтов)."""
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT id, name, aliases, is_self, status FROM people WHERE user_id = :u ORDER BY id"),
                         {"u": user_id}).all()
    return [{"id": r.id, "name": r.name, "aliases": list(r.aliases or []), "is_self": bool(r.is_self), "status": r.status}
            for r in rows]


def alias_ok_pairs(user_id: str) -> set:
    """Пары «Тёзки, ок» владельца: {(меньший id, больший id, alias_key)}."""
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT o.person_a, o.person_b, o.alias_key FROM people_alias_ok o "
                              "JOIN people p ON p.id = o.person_a WHERE p.user_id = :u"), {"u": user_id}).all()
    return {(r.person_a, r.person_b, r.alias_key) for r in rows}


def mark_alias_ok(user_id: str, person_id: int, alias: str) -> Optional[int]:
    """«Тёзки, ок»: все ТЕКУЩИЕ совпадения этого алиаса помечаются проверенными. None — у человека нет такого
    спорного алиаса; иначе число помеченных пар."""
    from core.people import alias_disputes, alias_key
    rows = alias_rows(user_id)
    hit = next((d for d in alias_disputes(rows, alias_ok_pairs(user_id)).get(person_id, []) if d["alias"] == alias), None)
    if not hit:
        return None
    k = alias_key(alias)
    with get_engine().begin() as c:
        for o in hit["also"]:
            a, b = sorted((person_id, o["id"]))
            c.execute(text("INSERT INTO people_alias_ok (person_a, person_b, alias_key) VALUES (:a, :b, :k) "
                           "ON CONFLICT DO NOTHING"), {"a": a, "b": b, "k": k})
    return len(hit["also"])


def get_person(user_id: str, person_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.id == person_id, people.c.user_id == user_id))).first()
    if not r:
        return None
    d = attach_accounts([_row(r)])[0]
    d.update(counts_for([person_id])[person_id])
    return d


def list_facts(person_id: int) -> List[Dict]:
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, fact_text, key_name, review_status, is_current, source, created_at, source_quote, "
            "source_chat_id, source_message_date, quote_author, source_platform, "
            "mention_count, first_mention_date, last_mention_date "
            "FROM memories WHERE person_id = :p AND NOT is_archived AND fact_text <> '' ORDER BY created_at, id"
        ), {"p": person_id}).all()
    return [{"id": r.id, "text": r.fact_text, "type": fact_type(r.key_name),
             "review_status": r.review_status, "is_current": r.is_current, "source": r.source,
             "created_at": r.created_at, "quote": r.source_quote,
             "chat_id": r.source_chat_id, "message_date": r.source_message_date,
             "quote_author": r.quote_author, "platform": r.source_platform,
             "mention_count": r.mention_count, "first_mention": r.first_mention_date,
             "last_mention": r.last_mention_date} for r in rows]


EDITABLE = ("name", "aliases", "birthday", "birthday_year_known", "notes", "status")


def update_person(user_id: str, person_id: int, values: Dict) -> Optional[Dict]:
    """Частичное обновление полей карточки (аккаунты — отдельные функции add/remove/set_primary_account)."""
    vals = {k: v for k, v in values.items() if k in EDITABLE}
    if vals.get("status") in ("rejected", "pending"):
        me = find_self(user_id)
        if me and me["id"] == person_id:
            raise ValueError("self_status")          # карточку «Я» нельзя отклонить/вернуть в pending
    with get_engine().begin() as c:
        if vals:
            vals["updated_at"] = func.now()
            res = c.execute(update(people).where(and_(people.c.id == person_id, people.c.user_id == user_id))
                            .values(**vals))
            if res.rowcount == 0:
                return None
            if "name" in vals or "aliases" in vals:
                sync_norm(c, person_id)
                refresh_related(c, person_id)
        elif c.execute(select(people.c.id).where(and_(people.c.id == person_id,
                                                      people.c.user_id == user_id))).first() is None:
            return None
    return get_person(user_id, person_id)


def set_fact_status(user_id: str, person_id: int, fact_id: int, status: str) -> bool:
    """Меняет review_status ОДНОГО факта этого человека этого владельца."""
    with get_engine().begin() as c:
        res = c.execute(text(
            "UPDATE memories SET review_status = :st, updated_at = now() "
            "WHERE id = :f AND person_id = :p AND user_id = :u AND NOT is_archived AND fact_text <> ''"
        ), {"st": status, "f": fact_id, "p": person_id, "u": user_id})
    return res.rowcount > 0


def erase_fact(user_id: str, person_id: int, fact_id: int) -> str:
    """«Стереть» отклонённый факт: удаляет текст, цитату, эмбеддинг, related_to и происхождение, оставляя
    ТОЛЬКО ключ и review_status='rejected' (tombstone): импорт пропускает существующие ключи, поэтому
    факт не воскреснет при повторном импорте. Не архивируем (архивные ключи импорт не видит и создал бы заново).
    Возвращает 'ok' | 'not_found' | 'not_rejected'."""
    with get_engine().begin() as c:
        row = c.execute(text(
            "SELECT review_status, fact_text FROM memories WHERE id = :f AND person_id = :p AND user_id = :u "
            "AND NOT is_archived FOR UPDATE"), {"f": fact_id, "p": person_id, "u": user_id}).first()
        if row is None or row.fact_text == "":
            return "not_found"
        if row.review_status != "rejected":
            return "not_rejected"
        c.execute(text(
            "UPDATE memories SET fact_text = '', source_quote = NULL, related_to = '', embedding = NULL, "
            "source_chat_id = NULL, source_message_date = NULL, quote_author = NULL, source_platform = NULL, "
            "mention_count = 1, first_mention_date = NULL, last_mention_date = NULL, updated_at = now() "
            "WHERE id = :f"), {"f": fact_id})
    return "ok"


def review_all(user_id: str, person_id: int, to_status: str) -> Optional[int]:
    """approve-all / reject-all: все pending-факты человека → to_status и
    status человека (confirmed|rejected) — одной транзакцией. None если человека нет."""
    person_status = {"confirmed": "confirmed", "rejected": "rejected"}[to_status]
    me = find_self(user_id)
    if to_status == "rejected" and me and me["id"] == person_id:
        raise ValueError("self_status")
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


def account_display_names(conn, person_id: int) -> List[str]:
    """Display name всех аккаунтов человека (под ними подписывались факты при импорте)."""
    rows = conn.execute(select(person_accounts.c.display_name).where(and_(
        person_accounts.c.person_id == person_id, person_accounts.c.display_name.isnot(None)))
        .order_by(person_accounts.c.id)).all()
    return [r[0].strip() for r in rows if r[0] and r[0].strip()]


def refresh_related(conn, person_id: int) -> None:
    """related_to фактов человека = «имя | алиасы | display name аккаунтов» (ILIKE/FTS-поиск ботов, эмбеддинг-текст
    и срезание старых префиксов «Имя: » в core.people.owner_fact живут от него). Зовётся после правки
    имени/алиасов/аккаунтов и после merge."""
    from core.people import related_to_str
    row = conn.execute(select(people.c.name, people.c.aliases).where(people.c.id == person_id)).first()
    if row:
        seen = {row.name.lower()} | {a.lower() for a in (row.aliases or [])}
        extra = [d for d in account_display_names(conn, person_id) if d.lower() not in seen]
        conn.execute(text("UPDATE memories SET related_to = :r WHERE person_id = :p"),
                     {"r": related_to_str(row.name, list(row.aliases or []) + extra), "p": person_id})


def set_account_display_name(user_id: str, person_id: int, account_id: int, display_name: Optional[str]) -> bool:
    """Задаёт display name аккаунта человека и обновляет related_to его фактов. False — аккаунта нет."""
    name = " ".join((display_name or "").split()) or None
    with get_engine().begin() as c:
        res = c.execute(update(person_accounts).where(and_(
            person_accounts.c.id == account_id, person_accounts.c.person_id == person_id,
            person_accounts.c.user_id == user_id)).values(display_name=name))
        if not res.rowcount:
            return False
        refresh_related(c, person_id)
    return True


def set_fact_type(user_id: str, person_id: int, fact_id: int, new_type: str) -> str:
    """Вручную меняет тип факта: переписывает ключ person_<id>_<type>_<hash>. 'ok' | 'not_found' | 'bad_type' |
    'conflict' (у человека уже есть живой факт с таким ключом). Стёртые (tombstone) не трогаем."""
    from core.people import FACT_TYPES, retype_key
    if new_type not in FACT_TYPES:
        return "bad_type"
    with get_engine().begin() as c:
        row = c.execute(text(
            "SELECT key_name, fact_text FROM memories WHERE id = :f AND person_id = :p AND user_id = :u "
            "AND NOT is_archived FOR UPDATE"), {"f": fact_id, "p": person_id, "u": user_id}).first()
        if row is None or row.fact_text == "":
            return "not_found"
        new_key = retype_key(row.key_name, new_type)
        if new_key is None:
            return "not_found"
        if new_key == row.key_name:
            return "ok"
        if c.execute(text("SELECT 1 FROM memories WHERE user_id = :u AND key_name = :k AND NOT is_archived "
                          "AND id <> :f"), {"u": user_id, "k": new_key, "f": fact_id}).first():
            return "conflict"
        c.execute(text("UPDATE memories SET key_name = :k, updated_at = now() WHERE id = :f"),
                  {"k": new_key, "f": fact_id})
    return "ok"


def review_batch(user_id: str, person_id: int, approve: List[int], reject: List[int],
                 reset: List[int], person_status: str) -> Optional[Dict]:
    """Ревью человека ОДНОЙ транзакцией: approve → confirmed, reject → rejected,
    reset → pending (для undo), статус человека. Любой id, не принадлежащий этому
    человеку/владельцу, → ValueError и полный откат. None если человека нет."""
    ids = list(approve) + list(reject) + list(reset)
    me = find_self(user_id)
    if me and me["id"] == person_id:
        person_status = "confirmed"                  # ревью «Я» статус карточки не меняет
    with get_engine().begin() as c:
        if c.execute(select(people.c.id).where(and_(people.c.id == person_id, people.c.user_id == user_id))
                     .with_for_update()).first() is None:
            return None
        if ids:
            have = {r[0] for r in c.execute(text(
                "SELECT id FROM memories WHERE id = ANY(:ids) AND person_id = :p AND user_id = :u AND NOT is_archived"
            ), {"ids": ids, "p": person_id, "u": user_id})}
            if have != set(ids):
                raise ValueError("fact does not belong to person")
        for fact_ids, status in ((approve, "confirmed"), (reject, "rejected"), (reset, "pending")):
            if fact_ids:
                c.execute(text("UPDATE memories SET review_status = :st, updated_at = now() WHERE id = ANY(:ids)"),
                          {"st": status, "ids": list(fact_ids)})
        c.execute(update(people).where(people.c.id == person_id).values(status=person_status, updated_at=func.now()))
    return get_person(user_id, person_id)


# ── merge двух людей (PR-5) ──────────────────────────────────────────────────

class MergeError(Exception):
    """code: not_found | same | self | both_clients"""

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


def _move_accounts(conn, src: int, dst: int) -> int:
    """Аккаунты src → dst (ДО удаления src: person_accounts удаляются каскадом). Основной аккаунт
    цели на платформе сохраняется; основной аккаунт источника становится основным только если у цели
    на этой платформе основного нет."""
    primary_taken = {r[0] for r in conn.execute(select(person_accounts.c.platform).where(and_(
        person_accounts.c.person_id == dst, person_accounts.c.is_primary)))}
    moved = 0
    for a in conn.execute(select(person_accounts.c.id, person_accounts.c.platform, person_accounts.c.is_primary)
                          .where(person_accounts.c.person_id == src).order_by(person_accounts.c.id)).all():
        becomes_primary = bool(a.is_primary) and a.platform not in primary_taken
        conn.execute(update(person_accounts).where(person_accounts.c.id == a.id)
                     .values(person_id=dst, is_primary=becomes_primary))
        if becomes_primary:
            primary_taken.add(a.platform)
        moved += 1
    return moved


def _delete_person(conn, pid: int) -> None:
    conn.execute(people.delete().where(people.c.id == pid))


def merge_people(user_id: str, source_id: int, target_id: int) -> Dict:
    """Склеивает source в target ОДНОЙ транзакцией; любое исключение → полный откат.

    Факты переезжают (см. _move_facts); алиасы — union (имя источника тоже становится
    алиасом); аккаунты объединяются (основные аккаунты цели сохраняются); пустые поля цели
    заполняются из источника, заметки при различии склеиваются; имя/статус цели не меняются.
    Аккаунты не теряются: потерянный id при повторном импорте воскресил бы дубль."""
    if source_id == target_id:
        raise MergeError("same")
    me = find_self(user_id)
    if me and me["id"] == source_id:
        raise MergeError("self")                      # «Я» нельзя влить в кого-то; в «Я» вливать можно
    with get_engine().begin() as conn:
        first, second = sorted((source_id, target_id))
        locked = {pid: _lock_person(conn, user_id, pid) for pid in (first, second)}
        src, dst = locked[source_id], locked[target_id]
        if not src or not dst:
            raise MergeError("not_found")

        # связь с клиентом Арканы переезжает к цели; у обоих по клиенту — склеить нельзя (не больше одного
        # клиента на человека): сначала нужно отвязать один из клиентов
        src_cl = conn.execute(text("SELECT id FROM clients WHERE person_id = :p AND user_id = :u"),
                              {"p": source_id, "u": user_id}).scalar()
        if src_cl:
            if conn.execute(text("SELECT 1 FROM clients WHERE person_id = :p AND user_id = :u"),
                            {"p": target_id, "u": user_id}).first():
                raise MergeError("both_clients")
            conn.execute(text("UPDATE clients SET person_id = :t WHERE id = :c"), {"t": target_id, "c": src_cl})
            stats_client = 1
        else:
            stats_client = 0
        stats = _move_facts(conn, user_id, source_id, target_id)
        stats["accounts_moved"] = _move_accounts(conn, source_id, target_id)
        stats["client_link_moved"] = stats_client
        _delete_person(conn, source_id)          # аккаунты уже переехали — каскад ничего не заберёт

        aliases = list(dst.get("aliases") or [])
        seen = {a.lower() for a in aliases} | {dst["name"].lower()}
        # Многословный алиас индексируется только фразой (core.people.alias_forms), поэтому имя
        # присоединяемого человека («Вера Пять») добавляем и целиком, и первым словом («Вера») —
        # иначе после слияния «Вере» перестала бы находить целевую карточку.
        src_name_words = (src["name"] or "").split()
        dst_words = {w.lower() for w in (dst["name"] or "").split()}   # имя цели и так ищется по словам
        first = src_name_words[:1] if len(src_name_words) > 1 and src_name_words[0].lower() not in dst_words else []
        extra = [src["name"]] + first
        for a in list(src.get("aliases") or []) + extra:
            if a and a.lower() not in seen:
                seen.add(a.lower())
                aliases.append(a)
        vals: Dict = {"aliases": aliases, "updated_at": func.now()}
        if src.get("birthday") and not dst.get("birthday"):
            vals["birthday"] = src["birthday"]
            vals["birthday_year_known"] = src.get("birthday_year_known", True)
        sn, dn = (src.get("notes") or "").strip(), (dst.get("notes") or "").strip()
        if sn and not dn:
            vals["notes"] = sn
        elif sn and dn and sn != dn:
            vals["notes"] = dn + "\n" + sn
        conn.execute(update(people).where(people.c.id == target_id).values(**vals))
        sync_norm(conn, target_id)
        refresh_related(conn, target_id)
    return stats


def absorb_into_self(user_id: str, source_id: int) -> Optional[Dict]:
    """Человек оказался самой владелицей (чат с её другим аккаунтом): его pending-факты → карточка «Я»,
    карточка человека удаляется; его аккаунты становятся аккаунтами «Я». В отличие от merge_people
    НЕ копирует ДР/алиасы. Подтверждённые/ручные факты не трогает:
    при их наличии → ValueError (человека нужно разобрать руками). None, если «Я»/человека нет."""
    me = find_self(user_id)
    if not me or me["id"] == source_id:
        return None
    with get_engine().begin() as conn:
        src = _lock_person(conn, user_id, source_id)
        if not src:
            return None
        protected = conn.execute(text(
            "SELECT count(*) FROM memories WHERE person_id = :p AND (review_status <> 'pending' OR source <> 'auto')"
        ), {"p": source_id}).scalar_one()
        if protected:
            raise ValueError("protected_facts")
        src_cl = conn.execute(text("SELECT id FROM clients WHERE person_id = :p AND user_id = :u"),
                              {"p": source_id, "u": user_id}).scalar()
        if src_cl:
            if conn.execute(text("SELECT 1 FROM clients WHERE person_id = :p AND user_id = :u"),
                            {"p": me["id"], "u": user_id}).first():
                raise ValueError("client_link")        # у «Я» уже есть свой клиент — разобрать руками
            conn.execute(text("UPDATE clients SET person_id = :t WHERE id = :c"), {"t": me["id"], "c": src_cl})
        stats = _move_facts(conn, user_id, source_id, me["id"])
        stats["accounts_moved"] = _move_accounts(conn, source_id, me["id"])
        _delete_person(conn, source_id)
        refresh_related(conn, me["id"])
    return stats
