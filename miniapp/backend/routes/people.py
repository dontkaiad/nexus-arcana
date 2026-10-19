"""miniapp/backend/routes/people.py — /api/people: домен «Люди» (ADR-0027, PR-4).

Список, карточка, правка, ревью фактов (approve/reject, approve-all/reject-all).
Владелец: current_user_id → user_id; чужая/несуществующая запись → 404.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from core.people import (
    UNKNOWN_YEAR, age, birthday_iso, fact_hash, fact_text, find_duplicates, related_to_str,
)
from core.repos.pg_memory_repo import PgMemoryRepo, bot_to_scope
from core.repos import pg_people_repo as repo
from core.user_manager import get_user_id

from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.people")

router = APIRouter()

PERSON_STATUSES = ("pending", "confirmed", "rejected")


async def _owner(tg_id: int) -> str:
    user_id = await get_user_id(tg_id)
    if not user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return user_id


def _person_out(p: Dict[str, Any]) -> Dict[str, Any]:
    known = bool(p.get("birthday_year_known", True))
    return {
        "id": p["id"],
        "name": p["name"],
        "aliases": list(p.get("aliases") or []),
        "birthday": birthday_iso(p.get("birthday"), known),
        "birthday_year_known": known,
        "age": age(p.get("birthday"), known),
        "tg_id": p.get("tg_id"),
        "tg_username": p.get("tg_username"),
        "vk_id": p.get("vk_id"),
        "notes": p.get("notes"),
        "status": p["status"],
        "is_self": bool(p.get("is_self")),
        "counts": p.get("counts", {}),
        "by_type": p.get("by_type", {}),
    }


@router.get("/people")
async def list_people(
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    tg_id: int = Depends(current_user_id),
) -> Dict[str, Any]:
    if status and status not in PERSON_STATUSES:
        raise HTTPException(status_code=422, detail="bad status")
    user_id = await _owner(tg_id)
    rows = await asyncio.to_thread(repo.list_people, user_id, status, q)
    return {"total": len(rows), "people": [_person_out(p) for p in rows]}


def _iso(d) -> Any:
    return d.isoformat() if d else None


def _group_facts(facts: List[Dict[str, Any]], is_self: bool = False) -> List[Dict[str, Any]]:
    """Группы по type; important первым, затем likes/dislikes/other, прочие по алфавиту.
    Для карточки «Я» внутри группы — по последнему упоминанию (свежие сверху)."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        by.setdefault(f["type"], []).append({
            "id": f["id"], "text": f["text"], "quote": f.get("quote"), "review_status": f["review_status"],
            "is_current": f["is_current"], "source": f["source"],
            "created_at": f["created_at"].isoformat() if f.get("created_at") else None,
            # происхождение («VK · 12.03.2021») и упоминания («×7 · последнее 2023»)
            "platform": f.get("platform"), "chat_id": f.get("chat_id"),
            "message_date": _iso(f.get("message_date")), "quote_author": f.get("quote_author"),
            "mention_count": f.get("mention_count") or 1,
            "first_mention": _iso(f.get("first_mention")), "last_mention": _iso(f.get("last_mention")),
        })
    if is_self:
        for items in by.values():
            items.sort(key=lambda i: (i["last_mention"] or i["message_date"] or ""), reverse=True)
    order = [t for t in repo.FACT_TYPE_ORDER if t in by] + sorted(t for t in by if t not in repo.FACT_TYPE_ORDER)
    return [{"type": t, "items": by[t]} for t in order]


@router.get("/people/duplicates")
async def duplicates(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Пары-кандидаты в один человек (подсказка; ничего не склеивает).
    Объявлен ДО /people/{person_id}, иначе «duplicates» уйдёт в int-параметр."""
    user_id = await _owner(tg_id)
    rows = await asyncio.to_thread(repo.list_people, user_id, None, None)
    pairs = find_duplicates(rows)
    brief = lambda p: {"id": p["id"], "name": p["name"], "aliases": list(p.get("aliases") or []),
                       "birthday": birthday_iso(p.get("birthday"), bool(p.get("birthday_year_known", True))),
                       "status": p["status"], "counts": p.get("counts", {})}
    return {"total": len(pairs), "pairs": [
        {"a": brief(d["a"]), "b": brief(d["b"]), "score": d["score"], "reasons": d["reasons"]} for d in pairs]}


@router.get("/people/{person_id}")
async def person_card(person_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    facts = await asyncio.to_thread(repo.list_facts, person_id)
    return {**_person_out(p), "facts": _group_facts(facts, bool(p.get("is_self")))}


class PersonPatch(BaseModel):
    name: Optional[str] = None
    aliases: Optional[List[str]] = None
    birthday: Optional[date] = None
    birthday_year_known: Optional[bool] = None
    tg_id: Optional[int] = None
    tg_username: Optional[str] = None
    vk_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@router.patch("/people/{person_id}")
async def patch_person(person_id: int, body: PersonPatch,
                       tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    sent = body.model_fields_set          # отличаем «не прислали» от «обнулить»
    vals: Dict[str, Any] = {}
    for f in sent:
        vals[f] = getattr(body, f)
    if "name" in vals and not (vals["name"] or "").strip():
        raise HTTPException(status_code=422, detail="name must not be empty")
    if "name" in vals:
        vals["name"] = vals["name"].strip()
    if "status" in vals and vals["status"] not in PERSON_STATUSES:
        raise HTTPException(status_code=422, detail="bad status")
    if "aliases" in vals:
        vals["aliases"] = [a.strip() for a in (vals["aliases"] or []) if a and a.strip()]
    if vals.get("birthday_year_known") is None:
        vals.pop("birthday_year_known", None)
    # год неизвестен → заглушка 1904 (ADR-0027); ДР обнулён → флаг сбрасываем в true
    if "birthday" in vals:
        if vals["birthday"] is None:
            vals["birthday_year_known"] = True
        elif vals.get("birthday_year_known") is False:
            vals["birthday"] = vals["birthday"].replace(year=UNKNOWN_YEAR) \
                if not (vals["birthday"].month == 2 and vals["birthday"].day == 29) \
                else date(UNKNOWN_YEAR, 2, 29)
        else:
            vals.setdefault("birthday_year_known", True)
    elif vals.get("birthday_year_known") is False:
        # только флаг: переписываем год у существующей даты
        cur = await asyncio.to_thread(repo.get_person, user_id, person_id)
        if cur and cur.get("birthday"):
            b = cur["birthday"]
            vals["birthday"] = date(UNKNOWN_YEAR, b.month, b.day)
    try:
        p = await asyncio.to_thread(repo.update_person, user_id, person_id, vals)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="tg_id/vk_id already belongs to another person")
    except ValueError:
        raise HTTPException(status_code=409, detail="self_status")
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    return _person_out(p)


async def _fact_review(person_id: int, fact_id: int, status: str, tg_id: int) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    ok = await asyncio.to_thread(repo.set_fact_status, user_id, person_id, fact_id, status)
    if not ok:
        raise HTTPException(status_code=404, detail="fact not found")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "fact_id": fact_id, "review_status": status, "counts": p["counts"]}


@router.post("/people/{person_id}/facts/{fact_id}/approve")
async def approve_fact(person_id: int, fact_id: int, tg_id: int = Depends(current_user_id)):
    return await _fact_review(person_id, fact_id, "confirmed", tg_id)


@router.post("/people/{person_id}/facts/{fact_id}/reject")
async def reject_fact(person_id: int, fact_id: int, tg_id: int = Depends(current_user_id)):
    return await _fact_review(person_id, fact_id, "rejected", tg_id)


async def _review_all(person_id: int, to_status: str, tg_id: int) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    try:
        n = await asyncio.to_thread(repo.review_all, user_id, person_id, to_status)
    except ValueError:
        raise HTTPException(status_code=409, detail="self_status")
    if n is None:
        raise HTTPException(status_code=404, detail="person not found")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "changed": n, "status": p["status"], "counts": p["counts"]}


@router.post("/people/{person_id}/approve-all")
async def approve_all(person_id: int, tg_id: int = Depends(current_user_id)):
    return await _review_all(person_id, "confirmed", tg_id)


@router.post("/people/{person_id}/reject-all")
async def reject_all(person_id: int, tg_id: int = Depends(current_user_id)):
    return await _review_all(person_id, "rejected", tg_id)


class MergeBody(BaseModel):
    into_id: int


@router.post("/people/{person_id}/merge")
async def merge_person(person_id: int, body: MergeBody,
                       tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Склеить person_id В into_id (источник удаляется). Одна транзакция."""
    user_id = await _owner(tg_id)
    try:
        stats = await asyncio.to_thread(repo.merge_people, user_id, person_id, body.into_id)
    except repo.MergeError as e:
        status = {"not_found": 404, "same": 422, "conflicting_ids": 409, "self": 409}[e.code]
        raise HTTPException(status_code=status, detail=e.code)
    target = await asyncio.to_thread(repo.get_person, user_id, body.into_id)
    return {"ok": True, **stats, "person": _person_out(target)}


class ReviewBody(BaseModel):
    approve: List[int] = Field(default_factory=list)
    reject: List[int] = Field(default_factory=list)
    reset: List[int] = Field(default_factory=list)      # → pending (undo)
    person_status: str = "confirmed"


@router.post("/people/{person_id}/review")
async def review_person(person_id: int, body: ReviewBody,
                        tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Пакетное ревью: approve + reject отмеченных/снятых и статус человека — одной
    транзакцией. `reset` + `person_status: pending` — откат (undo) прошлого действия."""
    if body.person_status not in PERSON_STATUSES:
        raise HTTPException(status_code=422, detail="bad person_status")
    groups = (set(body.approve), set(body.reject), set(body.reset))
    if sum(len(g) for g in groups) != len(groups[0] | groups[1] | groups[2]):
        raise HTTPException(status_code=422, detail="a fact id is in several lists")
    user_id = await _owner(tg_id)
    try:
        p = await asyncio.to_thread(repo.review_batch, user_id, person_id, body.approve,
                                    body.reject, body.reset, body.person_status)
    except ValueError:
        raise HTTPException(status_code=404, detail="fact not found")
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    return {"ok": True, "status": p["status"], "counts": p["counts"]}


FACT_TYPES = ("important", "likes", "dislikes", "other")


class FactBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    type: str = "other"


@router.post("/people/{person_id}/facts")
async def add_fact(person_id: int, body: FactBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Ручной факт: source=manual, сразу confirmed (виден ботам). Повтор того же текста идемпотентен."""
    user_id = await _owner(tg_id)
    text_ = " ".join(body.text.split())
    if not text_ or body.type not in FACT_TYPES:
        raise HTTPException(status_code=422, detail="bad fact")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    key = "person_%d_%s_%s" % (person_id, body.type, fact_hash(text_))
    mid, _ = await PgMemoryRepo().upsert(
        fact_text(p["name"], text_), key, "👥 Люди", bot_to_scope("☀️ Nexus"),
        related_to_str(p["name"], p.get("aliases") or []), "manual", user_id,
        person_id=person_id, review_status="confirmed",
    )
    await asyncio.to_thread(repo.set_fact_status, user_id, person_id, int(mid), "confirmed")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "fact_id": int(mid), "counts": p["counts"]}
