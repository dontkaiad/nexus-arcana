"""core/repos/pg_client_links.py — связь клиент Арканы ↔ человек (ADR-0027, PR-8a). Sync SQLAlchemy Core.

`clients.person_id` (FK people ON DELETE SET NULL; не больше одного клиента на человека).
Связывает владелец; исключение — `self`-клиент ↔ карточка «Я» (детерминированно, `ensure_self_link`)."""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
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
    return person


def unlink(user_id: str, client_id: int) -> bool:
    cl = get_client(user_id, client_id)
    if not cl:
        raise LinkError("client_not_found")
    if cl["type_code"] == "self":
        raise LinkError("self_client")
    with get_engine().begin() as c:
        c.execute(text("UPDATE clients SET person_id = NULL WHERE id = :i AND user_id = :u"), {"i": client_id, "u": user_id})
    return True
