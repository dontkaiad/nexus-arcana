"""core/repos/pg_client_links.py — связь клиент Арканы ↔ человек (ADR-0027, PR-8a). Sync SQLAlchemy Core.

`clients.person_id` (FK people ON DELETE SET NULL; не больше одного клиента на человека).
Связывает владелец; исключение — `self`-клиент ↔ карточка «Я» (детерминированно, `ensure_self_link`)."""
from __future__ import annotations

from typing import Dict, List, Optional

from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from core.people import _same_bday, birthday_iso
from core.people_links import rank_candidates
from core.repos import pg_people_repo as prepo


class LinkError(Exception):
    """code: client_not_found | person_not_found | person_rejected | person_taken | already_linked | self_client"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def get_client(user_id: str, client_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(text(
            "SELECT cl.id, cl.name, cl.contact, cl.birthday, cl.person_id, ct.code AS type_code "
            "FROM clients cl LEFT JOIN client_type ct ON ct.id = cl.type_id "
            "WHERE cl.id = :i AND cl.user_id = :u"), {"i": client_id, "u": user_id}).first()
    return dict(r._mapping) if r else None


def clients_by_person(user_id: str, person_ids: List[int]) -> Dict[int, int]:
    """{person_id: client_id} — для бейджа «Клиент Арканы» (наружу отдаётся только id клиента)."""
    if not person_ids:
        return {}
    with get_engine().connect() as c:
        return {r[0]: r[1] for r in c.execute(text(
            "SELECT person_id, id FROM clients WHERE user_id = :u AND person_id = ANY(:ids)"),
            {"u": user_id, "ids": list(person_ids)})}


_TYPE_ORDER = ("important", "likes", "dislikes", "other")


def person_ids_by_client(user_id: str) -> Dict[str, int]:
    with get_engine().connect() as c:
        return {str(r[0]): r[1] for r in c.execute(text(
            "SELECT id, person_id FROM clients WHERE user_id = :u AND person_id IS NOT NULL"), {"u": user_id})}


def person_brief(user_id: str, client_id) -> Optional[Dict]:
    """{id, name} связанного человека или None."""
    try:
        cl = get_client(user_id, int(client_id))
    except (TypeError, ValueError):
        return None
    if not cl or not cl["person_id"]:
        return None
    p = prepo.get_person(user_id, cl["person_id"])
    return {"id": p["id"], "name": p["name"]} if p else None


def person_block(user_id: str, client_id) -> Optional[Dict]:
    """Блок «Из Людей» для досье Арканы (read-only): человек, ДР, аккаунты со ссылками и подтверждённые
    факты (важные первыми). Pending/rejected не попадают."""
    try:
        cl = get_client(user_id, int(client_id))
    except (TypeError, ValueError):
        return None
    if not cl or not cl["person_id"]:
        return None
    p = prepo.get_person(user_id, cl["person_id"])
    if not p:
        return None
    facts = [f for f in prepo.list_facts(p["id"]) if f["review_status"] == "confirmed" and f["is_current"] is not False]
    facts.sort(key=lambda f: (_TYPE_ORDER.index(f["type"]) if f["type"] in _TYPE_ORDER else len(_TYPE_ORDER), f["id"]))
    return {
        "id": p["id"], "name": p["name"], "is_self": bool(p.get("is_self")),
        "birthday": birthday_iso(p.get("birthday"), p.get("birthday_year_known", True)),
        "accounts": [{"platform": a["platform"], "username": a.get("username"), "external_id": a["external_id"],
                      "url": prepo.account_url(a["platform"], a["external_id"], a.get("username"))}
                     for a in p.get("accounts", [])],
        "facts": [{"id": f["id"], "type": f["type"], "text": f["text"]} for f in facts],
    }


def ensure_self_link(user_id: str) -> Optional[Dict]:
    """Клиент типа `self` ↔ карточка «Я»: детерминированно, без вопроса. Идемпотентно.
    Возвращает {client_id, person_id}, если связь создана сейчас."""
    me = prepo.find_self(user_id)
    if not me:
        return None
    with get_engine().begin() as c:
        row = c.execute(text(
            "SELECT cl.id FROM clients cl JOIN client_type ct ON ct.id = cl.type_id "
            "WHERE cl.user_id = :u AND ct.code = 'self' AND cl.person_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM clients x WHERE x.user_id = :u AND x.person_id = :p) "
            "ORDER BY cl.id LIMIT 1 FOR UPDATE"), {"u": user_id, "p": me["id"]}).first()
        if not row:
            return None
        c.execute(text("UPDATE clients SET person_id = :p WHERE id = :i"), {"p": me["id"], "i": row[0]})
    return {"client_id": row[0], "person_id": me["id"]}


def candidates(user_id: str, client_id: int) -> Optional[Dict]:
    """{'linked': person|None, 'candidates': [{person, score, reasons}]} или None, если клиента нет.
    Без автосвязки: кандидаты — подсказка. Уже занятые другим клиентом и отклонённые люди не предлагаются."""
    cl = get_client(user_id, client_id)
    if not cl:
        return None
    if cl["person_id"]:
        return {"linked": prepo.get_person(user_id, cl["person_id"]), "candidates": [], "client": cl}
    with get_engine().connect() as c:
        taken = {r[0] for r in c.execute(text(
            "SELECT person_id FROM clients WHERE user_id = :u AND person_id IS NOT NULL"), {"u": user_id})}
    people = [p for p in prepo.find_named(user_id) if p["id"] not in taken]
    if cl["type_code"] == "self":                      # свой клиент предлагается только к «Я» (и связывается сам)
        me = prepo.find_self(user_id)
        cands = [{"person": me, "score": 1.0, "reasons": ["self"]}] if me and me["id"] not in taken else []
    else:
        cands = rank_candidates({"name": cl["name"], "contact": cl["contact"], "birthday": cl["birthday"]}, people)
    return {"linked": None, "candidates": cands, "client": cl}


def link(user_id: str, client_id: int, person_id: int) -> Dict:
    cl = get_client(user_id, client_id)
    if not cl:
        raise LinkError("client_not_found")
    if cl["type_code"] == "self":
        raise LinkError("self_client")                 # связывается сам, вручную не трогаем
    person = prepo.get_person(user_id, person_id)
    if not person:
        raise LinkError("person_not_found")
    if person["status"] == "rejected":
        raise LinkError("person_rejected")
    if cl["person_id"]:
        raise LinkError("already_linked")
    try:
        with get_engine().begin() as c:
            c.execute(text("UPDATE clients SET person_id = :p WHERE id = :i AND user_id = :u"),
                      {"p": person_id, "i": client_id, "u": user_id})
    except IntegrityError:
        raise LinkError("person_taken")
    person["birthday_sync"] = _sync_birthday_on_link(user_id, cl, person)
    return person


# ── ДР: единственный источник — people.birthday (после связи) ────────────────

def _sync_birthday_on_link(user_id: str, cl: Dict, person: Dict) -> Dict:
    """При связи: у человека ДР пуст, у клиента есть → копируем клиентский человеку.
    Оба заданы и различаются → ничего не меняем, отдаём конфликт: выбирает владелец (resolve_birthday)."""
    cb, pb = cl.get("birthday"), person.get("birthday")
    pk = person.get("birthday_year_known", True)
    out = {"state": "none", "client": cb.isoformat() if cb else None, "person": birthday_iso(pb, pk)}
    if cb and not pb:
        with get_engine().begin() as c:
            c.execute(text("UPDATE people SET birthday = :b, birthday_year_known = TRUE WHERE id = :p AND user_id = :u"),
                      {"b": cb, "p": person["id"], "u": user_id})
        out.update(state="copied", person=cb.isoformat())
    elif cb and pb:
        same = _same_bday({"birthday": cb, "birthday_year_known": True}, {"birthday": pb, "birthday_year_known": pk})
        out["state"] = "same" if same else "conflict"
    return out


def resolve_birthday(user_id: str, client_id: int, use: str) -> Dict:
    """Конфликт ДР решает владелец: use='client' — берём дату клиента, use='person' — дату человека.
    Итог: people.birthday = выбранная; clients.birthday приводится к ней (чтобы после отвязки не было расхождения)."""
    cl = get_client(user_id, client_id)
    if not cl:
        raise LinkError("client_not_found")
    if not cl["person_id"]:
        raise LinkError("not_linked")
    person = prepo.get_person(user_id, cl["person_id"])
    if use == "client" and cl["birthday"]:
        with get_engine().begin() as c:
            c.execute(text("UPDATE people SET birthday = :b, birthday_year_known = TRUE WHERE id = :p AND user_id = :u"),
                      {"b": cl["birthday"], "p": person["id"], "u": user_id})
        return {"birthday": cl["birthday"].isoformat()}
    if use == "person" and person.get("birthday"):
        with get_engine().begin() as c:
            c.execute(text("UPDATE clients SET birthday = :b WHERE id = :i AND user_id = :u"),
                      {"b": person["birthday"] if person.get("birthday_year_known", True) else None,
                       "i": client_id, "u": user_id})
        return {"birthday": birthday_iso(person["birthday"], person.get("birthday_year_known", True))}
    raise LinkError("nothing_to_resolve")


def set_linked_birthday(user_id: str, client_id, iso: Optional[str]) -> bool:
    """Правка ДР клиента, связанного с человеком, пишется в people.birthday. False — клиент не связан
    (вызывающий пишет в clients.birthday как раньше)."""
    try:
        cl = get_client(user_id, int(client_id))
    except (TypeError, ValueError):
        return False
    if not cl or not cl["person_id"]:
        return False
    try:
        b = date.fromisoformat(iso) if iso else None
    except ValueError:
        return True                                   # мусорная дата: не пишем никуда
    with get_engine().begin() as c:
        c.execute(text("UPDATE people SET birthday = :b, birthday_year_known = TRUE WHERE id = :p AND user_id = :u"),
                  {"b": b, "p": cl["person_id"], "u": user_id})
    return True


def effective_birthday(user_id: str, client_id, own_iso: Optional[str]) -> Optional[str]:
    """Для показа: у связанного клиента ДР берётся из карточки человека, иначе — собственный."""
    try:
        cl = get_client(user_id, int(client_id))
    except (TypeError, ValueError):
        return own_iso
    if not cl or not cl["person_id"]:
        return own_iso
    p = prepo.get_person(user_id, cl["person_id"])
    if not p or not p.get("birthday"):
        return own_iso
    return birthday_iso(p["birthday"], p.get("birthday_year_known", True))


# ── контекст для трактовок: подтверждённые факты связанного человека ─────────

def person_context(user_id: str, client_id) -> str:
    """Текст для промпта таро: «ДР» и подтверждённые (confirmed) факты связанного человека, важные первыми.
    Pending/rejected не попадают. Без связи — пустая строка (контекст не придумываем по имени)."""
    try:
        cl = get_client(user_id, int(client_id))
    except (TypeError, ValueError):
        return ""
    if not cl or not cl["person_id"]:
        return ""
    p = prepo.get_person(user_id, cl["person_id"])
    if not p:
        return ""
    lines = []
    if p.get("birthday"):
        b, known = p["birthday"], p.get("birthday_year_known", True) and p["birthday"].year != 1904
        lines.append("ДР: %02d.%02d%s" % (b.day, b.month, (".%d" % b.year) if known else ""))
    facts = [f for f in prepo.list_facts(p["id"]) if f["review_status"] == "confirmed" and f["is_current"] is not False]
    facts.sort(key=lambda f: (_TYPE_ORDER.index(f["type"]) if f["type"] in _TYPE_ORDER else len(_TYPE_ORDER), f["id"]))
    lines += ["- " + f["text"] for f in facts]
    return "\n".join(lines)


def unlink(user_id: str, client_id: int) -> bool:
    cl = get_client(user_id, client_id)
    if not cl:
        raise LinkError("client_not_found")
    if cl["type_code"] == "self":
        raise LinkError("self_client")
    with get_engine().begin() as c:
        c.execute(text("UPDATE clients SET person_id = NULL WHERE id = :i AND user_id = :u"), {"i": client_id, "u": user_id})
    return True
