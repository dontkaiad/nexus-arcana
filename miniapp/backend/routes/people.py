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
    RELATIONS, UNKNOWN_YEAR, age, alias_disputes, birthday_iso, clean_about, ABOUT_MAX, fact_hash, find_duplicates, person_label,
    related_to_str, strip_name_prefix,
)
from core.repos.pg_memory_repo import PgMemoryRepo, bot_to_scope
from core.repos import pg_client_links as client_links
from core.repos import pg_people_repo as repo
from core import self_dedup
from core.memory_subjects import SELF_CATEGORIES
from core.user_manager import get_user_id

from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.people")

router = APIRouter()

PERSON_STATUSES = ("pending", "confirmed", "rejected")


async def _attach_clients(user_id: str, rows: List[Dict[str, Any]]) -> None:
    by = await asyncio.to_thread(client_links.clients_by_person, user_id, [r["id"] for r in rows])
    for r in rows:
        r["client_id"] = by.get(r["id"])


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
        # производные от ОСНОВНЫХ аккаунтов (для списка и обратной совместимости)
        "tg_id": p.get("tg_id"),
        "tg_username": p.get("tg_username"),
        "vk_id": p.get("vk_id"),
        "accounts": [{**a, "url": repo.account_url(a["platform"], a["external_id"], a.get("username"))}
                     for a in p.get("accounts", [])],
        # бейдж «Клиент Арканы»: наружу только id клиента — без сеансов, оплат и прочего из Арканы
        "client_id": p.get("client_id"),
        "notes": p.get("notes"),
        "relation": p.get("relation"),
        "about": p.get("about"),
        "label": person_label(p["name"], p.get("relation"), p.get("about")),
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
    await asyncio.to_thread(client_links.ensure_self_link, user_id)
    rows = await asyncio.to_thread(repo.list_people, user_id, status, q)
    await _attach_clients(user_id, rows)
    return {"total": len(rows), "people": [_person_out(p) for p in rows]}


def _iso(d) -> Any:
    return d.isoformat() if d else None


def _with_merged(facts: List[Dict[str, Any]], merged: Dict[int, List[Dict[str, Any]]]) -> None:
    """Оставшийся факт показывает суммарные упоминания и крайние даты своих объединённых дублей (в БД ничего не меняется)."""
    for f in facts:
        kids = merged.get(f["id"])
        if not kids:
            continue
        f["mention_count"] = (f.get("mention_count") or 1) + sum(k["mention_count"] for k in kids)
        firsts = [d for d in [f.get("first_mention"), *[k["first_mention"] or k["message_date"] for k in kids]] if d]
        lasts = [d for d in [f.get("last_mention"), *[k["last_mention"] or k["message_date"] for k in kids]] if d]
        f["first_mention"], f["last_mention"] = (min(firsts) if firsts else None), (max(lasts) if lasts else None)
        f["merged"] = [{"id": k["id"], "text": k["text"], "quote": k["quote"], "mention_count": k["mention_count"],
                        "message_date": _iso(k["message_date"]), "platform": k["platform"]} for k in kids]


def _group_facts(facts: List[Dict[str, Any]], is_self: bool = False, name: str = "",
                 aliases: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Группы по type; important первым, затем likes/dislikes/other, прочие по алфавиту.
    Для карточки «Я» внутри группы — по последнему упоминанию (свежие сверху)."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        by.setdefault(f["type"], []).append({
            "id": f["id"],
            # в карточке самого человека префикс «Имя: » лишний (он нужен только контексту бота)
            "text": f["text"] if is_self else strip_name_prefix(f["text"], name, aliases or []),
            "quote": f.get("quote"), "review_status": f["review_status"],
            "is_current": f["is_current"], "source": f["source"],
            "created_at": f["created_at"].isoformat() if f.get("created_at") else None,
            # происхождение («VK · 12.03.2021») и упоминания («×7 · последнее 2023»)
            "platform": f.get("platform"), "chat_id": f.get("chat_id"),
            "message_date": _iso(f.get("message_date")), "quote_author": f.get("quote_author"),
            "mention_count": f.get("mention_count") or 1,
            "first_mention": _iso(f.get("first_mention")), "last_mention": _iso(f.get("last_mention")),
            "merged": f.get("merged") or [], "category": f.get("category"),
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


@router.get("/people/alias-disputes")
async def alias_disputes_summary(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Сколько «Других имён» спорные (совпали с именем/алиасом другого) и у кого. Бейдж «Спорные имена: N».
    Объявлен ДО /people/{person_id}."""
    user_id = await _owner(tg_id)
    rows = await asyncio.to_thread(repo.alias_rows, user_id)
    disp = alias_disputes(rows, await asyncio.to_thread(repo.alias_ok_pairs, user_id))
    by_id = {r["id"]: r for r in rows}
    people_ = sorted(({"id": pid, "disputed": len(v), "aliases": len(by_id[pid]["aliases"])} for pid, v in disp.items()),
                     key=lambda d: (-d["disputed"], d["id"]))
    return {"total": sum(len(v) for v in disp.values()), "people": people_}


async def _aliases_info(user_id: str, person_id: int, aliases: List[str]) -> List[Dict[str, Any]]:
    rows = await asyncio.to_thread(repo.alias_rows, user_id)
    disp = {d["alias"]: d["also"] for d in alias_disputes(rows, await asyncio.to_thread(repo.alias_ok_pairs, user_id)).get(person_id, [])}
    return [{"alias": a, "also": disp.get(a, [])} for a in aliases]


@router.get("/people/{person_id}")
async def person_card(person_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    await _attach_clients(user_id, [p])
    facts = await asyncio.to_thread(repo.list_facts, person_id)
    _with_merged(facts, await asyncio.to_thread(self_dedup.merged_info, user_id, [f["id"] for f in facts]))
    out = {**_person_out(p), "aliases_info": await _aliases_info(user_id, person_id, list(p.get("aliases") or [])),
           "facts": _group_facts(facts, bool(p.get("is_self")), p["name"], p.get("aliases"))}
    if p.get("is_self"):                       # разделы «Я» в Памяти: категории и сколько фактов в каждой (без отклонённых)
        counts: Dict[str, int] = {}
        for f in facts:
            if f["review_status"] != "rejected":
                counts[f.get("category") or ""] = counts.get(f.get("category") or "", 0) + 1
        out["categories"] = [{"name": c, "count": counts[c]} for c in SELF_CATEGORIES if c in counts] + \
                            [{"name": c, "count": n} for c, n in sorted(counts.items()) if c not in SELF_CATEGORIES and c]
        out["category_choices"] = list(SELF_CATEGORIES)
    return out


class AliasBody(BaseModel):
    alias: str = Field(min_length=1, max_length=200)


@router.post("/people/{person_id}/aliases/remove")
async def remove_alias(person_id: int, body: AliasBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Убрать одно «другое имя» (крестик у спорного). aliases_norm и related_to пересчитывает update_person."""
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    new = [a for a in (p.get("aliases") or []) if a != body.alias]
    if len(new) == len(p.get("aliases") or []):
        raise HTTPException(status_code=404, detail="alias not found")
    p = await asyncio.to_thread(repo.update_person, user_id, person_id, {"aliases": new})
    return {**_person_out(p), "aliases_info": await _aliases_info(user_id, person_id, new)}


@router.post("/people/{person_id}/aliases/ok")
async def alias_ok(person_id: int, body: AliasBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """«Тёзки, ок»: совпадение имени проверено — не подсвечивать и не считать в бейдже. Новое совпадение подсветится."""
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    if await asyncio.to_thread(repo.mark_alias_ok, user_id, person_id, body.alias) is None:
        raise HTTPException(status_code=404, detail="alias not disputed")
    return {**_person_out(p), "aliases_info": await _aliases_info(user_id, person_id, list(p.get("aliases") or []))}


@router.post("/people/{person_id}/aliases/reset")
async def reset_aliases(person_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """«Сбросить другие имена»: остаётся имя (+ аккаунты — они в отдельной таблице). Карточку «Я» не сбрасываем."""
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    if p.get("is_self"):
        raise HTTPException(status_code=409, detail="self_aliases")
    p = await asyncio.to_thread(repo.update_person, user_id, person_id, {"aliases": []})
    return {**_person_out(p), "aliases_info": []}


class PersonPatch(BaseModel):
    name: Optional[str] = None
    aliases: Optional[List[str]] = None
    birthday: Optional[date] = None
    birthday_year_known: Optional[bool] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    relation: Optional[str] = None
    about: Optional[str] = None


def _check_who(vals: Dict[str, Any]) -> None:
    """«Кто это»: relation — из списка (или null), about — одна строка ≤120 (пусто → null)."""
    if "relation" in vals and vals["relation"] is not None and vals["relation"] not in RELATIONS:
        raise HTTPException(status_code=422, detail="bad relation")
    if "about" in vals:
        if len(" ".join((vals["about"] or "").split())) > ABOUT_MAX:
            raise HTTPException(status_code=422, detail="about too long")
        vals["about"] = clean_about(vals["about"])


class PersonCreate(BaseModel):
    name: str
    relation: Optional[str] = None
    about: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)


@router.post("/people")
async def create_person(body: PersonCreate, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """«+ Человек»: подтверждённая карточка (ручной ввод владельца). Тёзки разрешены; id уже существующих с таким же именем — в homonym_ids."""
    user_id = await _owner(tg_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    vals: Dict[str, Any] = {"relation": body.relation, "about": body.about}
    _check_who(vals)
    aliases = [a.strip() for a in body.aliases if a and a.strip()]
    same = await asyncio.to_thread(repo.resolve, user_id, [name])     # тёзки бывают настоящими — не блокируем
    pid = await asyncio.to_thread(repo.create, user_id, name, aliases, status="confirmed",
                                  relation=vals["relation"], about=vals["about"])
    p = await asyncio.to_thread(repo.get_person, user_id, pid)
    return {**_person_out(p), "homonym_ids": [h["id"] for h in same]}


@router.patch("/people/{person_id}")
async def patch_person(person_id: int, body: PersonPatch,
                       tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    sent = body.model_fields_set          # отличаем «не прислали» от «обнулить»
    vals: Dict[str, Any] = {}
    for f in sent:
        vals[f] = getattr(body, f)
    _check_who(vals)
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


@router.post("/people/{person_id}/facts/{fact_id}/restore")
async def restore_fact(person_id: int, fact_id: int, tg_id: int = Depends(current_user_id)):
    """Отклонённый факт возвращается на проверку (pending)."""
    return await _fact_review(person_id, fact_id, "pending", tg_id)


@router.post("/people/{person_id}/facts/{fact_id}/erase")
async def erase_fact(person_id: int, fact_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Стереть ОТКЛОНЁННЫЙ факт безвозвратно (остаётся tombstone: ключ + rejected)."""
    user_id = await _owner(tg_id)
    res = await asyncio.to_thread(repo.erase_fact, user_id, person_id, fact_id)
    if res == "not_found":
        raise HTTPException(status_code=404, detail="fact not found")
    if res == "not_rejected":
        raise HTTPException(status_code=409, detail="only rejected facts can be erased")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "fact_id": fact_id, "counts": p["counts"]}


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
        status = {"not_found": 404, "same": 422, "self": 409, "both_clients": 409}[e.code]
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
    # текст без префикса «Имя: » — имя рисуется из person
    mid, _ = await PgMemoryRepo().upsert(
        text_, key, "👥 Люди", bot_to_scope("☀️ Nexus"),
        related_to_str(p["name"], p.get("aliases") or []), "manual", user_id,
        person_id=person_id, review_status="confirmed",
    )
    await asyncio.to_thread(repo.set_fact_status, user_id, person_id, int(mid), "confirmed")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "fact_id": int(mid), "counts": p["counts"]}


class FactTypeBody(BaseModel):
    type: str


@router.post("/people/{person_id}/facts/{fact_id}/type")
async def set_fact_type(person_id: int, fact_id: int, body: FactTypeBody,
                        tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Ручная смена типа факта (тап по иконке типа в карточке). Ключ факта переписывается."""
    user_id = await _owner(tg_id)
    res = await asyncio.to_thread(repo.set_fact_type, user_id, person_id, fact_id, body.type)
    if res == "bad_type":
        raise HTTPException(status_code=422, detail="bad type")
    if res == "not_found":
        raise HTTPException(status_code=404, detail="fact not found")
    if res == "conflict":
        raise HTTPException(status_code=409, detail="same fact already has this type")
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    return {"ok": True, "fact_id": fact_id, "type": body.type, "counts": p["counts"]}


# ── аккаунты человека: несколько TG/VK на одного человека (PR-7) ─────────────

class AccountBody(BaseModel):
    platform: str
    external_id: int = Field(gt=0)
    username: Optional[str] = Field(default=None, max_length=64)
    is_primary: bool = False


@router.post("/people/{person_id}/accounts")
async def add_account(person_id: int, body: AccountBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    if body.platform not in repo.PLATFORMS:
        raise HTTPException(status_code=422, detail="bad platform")
    username = (body.username or "").strip().lstrip("@") or None
    if username and body.platform != "tg":
        raise HTTPException(status_code=422, detail="username is for tg only")
    user_id = await _owner(tg_id)
    try:
        p = await asyncio.to_thread(repo.add_account, user_id, person_id, body.platform, body.external_id,
                                    username, body.is_primary)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="account already belongs to another person")
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    return _person_out(p)


@router.delete("/people/{person_id}/accounts/{account_id}")
async def remove_account(person_id: int, account_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.remove_account, user_id, person_id, account_id)
    if not p:
        raise HTTPException(status_code=404, detail="account not found")
    return _person_out(p)


@router.post("/people/{person_id}/accounts/{account_id}/primary")
async def make_primary_account(person_id: int, account_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.set_primary_account, user_id, person_id, account_id)
    if not p:
        raise HTTPException(status_code=404, detail="account not found")
    return _person_out(p)


# ── Похожие факты карточки: предложения, объединить, «Это разное», разъединить ───────────────────────────────────────
@router.get("/people/{person_id}/similar")
async def similar(person_id: int, threshold: float = Query(self_dedup.DEFAULT_THRESHOLD, ge=0.8, le=0.99),
                  tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    if not await asyncio.to_thread(repo.get_person, user_id, person_id):
        raise HTTPException(status_code=404, detail="person not found")
    groups = await asyncio.to_thread(self_dedup.similar_groups, person_id, user_id, threshold)
    return {"count": len(groups), "groups": groups}


class SimilarMergeBody(BaseModel):
    keep_id: int
    ids: List[int] = Field(min_length=2, max_length=50)


class SimilarIdsBody(BaseModel):
    ids: List[int] = Field(min_length=2, max_length=50)


class SimilarUnmergeBody(BaseModel):
    keep_id: int
    id: Optional[int] = None            # без id — вернуть все объединённые дубли факта


def _dedup_http(e: ValueError) -> HTTPException:
    code = 404 if str(e) in ("not_found",) else 409 if str(e) in ("keep_hidden", "already_hidden") else 400
    return HTTPException(status_code=code, detail=str(e))


@router.post("/people/{person_id}/similar/merge")
async def similar_merge(person_id: int, body: SimilarMergeBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    try:
        n = await asyncio.to_thread(self_dedup.merge, user_id, person_id, body.keep_id, body.ids)
    except ValueError as e:
        raise _dedup_http(e)
    return {"ok": True, "merged": n}


@router.post("/people/{person_id}/similar/dismiss")
async def similar_dismiss(person_id: int, body: SimilarIdsBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    try:
        n = await asyncio.to_thread(self_dedup.dismiss, user_id, person_id, body.ids)
    except ValueError as e:
        raise _dedup_http(e)
    return {"ok": True, "pairs": n}


@router.post("/people/{person_id}/similar/unmerge")
async def similar_unmerge(person_id: int, body: SimilarUnmergeBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    try:
        n = await asyncio.to_thread(self_dedup.unmerge, user_id, person_id, body.keep_id, body.id)
    except ValueError as e:
        raise _dedup_http(e)
    return {"ok": True, "restored": n}


class FactCategoryBody(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)
    category: str


@router.post("/people/{person_id}/facts/category")
async def set_facts_category(person_id: int, body: FactCategoryBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Сменить категорию фактов карточки «Я» (один тап или «Перенести в категорию…» для выбранных). Только SELF_CATEGORIES."""
    user_id = await _owner(tg_id)
    p = await asyncio.to_thread(repo.get_person, user_id, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    if not p.get("is_self"):
        raise HTTPException(status_code=400, detail="categories only for self")
    if body.category not in SELF_CATEGORIES:
        raise HTTPException(status_code=400, detail="unknown category")
    n = await asyncio.to_thread(repo.set_facts_category, user_id, person_id, body.ids, body.category)
    return {"ok": True, "moved": n}
