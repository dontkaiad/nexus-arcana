"""miniapp/backend/routes/cat_health.py — /api/cat-health/*: здоровье котов для приложения cats (вес, визиты / прививки, курсы лекарств).

Доступ ТОЛЬКО по Bearer-токену CATS_WRITE_TOKEN (жёсткий шлюз cats_write_gate, от имени владельца CATS_WRITE_OWNER_TG_ID); initData и cookie здесь
не принимаются. Чтение и запись одним токеном; ничего за пределами /api/cat-health/* этот токен не открывает. Денег в данных нет.
Записи привязаны к владельцу: чужой / убранный / несуществующий кот или строка — 404. Напоминания ведёт core.cat_health в обычных задачах.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core import cat_blocks
from core import cat_health as svc
from core.repos import pg_cat_health_repo as repo
from core.repos import pg_cats_repo as cats_repo
from core.user_manager import get_user_id
from miniapp.backend.cats_write_auth import cats_write_gate

router = APIRouter(prefix="/cat-health", dependencies=[Depends(cats_write_gate)])

Kind = Literal["visit", "vaccine", "deworm", "flea", "other"]
VisitStatus = Literal["planned", "done", "cancelled"]
MedStatus = Literal["active", "done", "cancelled"]


def _out(row: Dict[str, Any]) -> Dict[str, Any]:
    """Строка БД → JSON: даты в ISO, без user_id."""
    o: Dict[str, Any] = {}
    for k, v in row.items():
        if k == "user_id":
            continue
        o[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return o


async def _uid(owner_tg: int) -> str:
    user_id = (await get_user_id(owner_tg)) or ""
    if not user_id:
        raise HTTPException(status_code=404, detail="not found")
    return user_id


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, repo.NotFound):
        return HTTPException(status_code=404, detail="not found")
    return HTTPException(status_code=422, detail=str(exc))


# ── вес ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

class WeightIn(BaseModel):
    grams: int = Field(ge=repo.GRAMS_MIN, le=repo.GRAMS_MAX)
    weighed_on: Optional[date] = None
    note: Optional[str] = Field(default=None, max_length=300)


class WeightPatch(BaseModel):
    sitter_visible: bool


@router.get("/cats/{cat_id}/weights")
async def list_weights(cat_id: int, limit: int = Query(200, ge=1, le=1000), owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    try:
        rows = await asyncio.to_thread(repo.list_weights, await _uid(owner_tg), cat_id, limit)
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)
    return {"weights": [_out(r) for r in rows]}


@router.post("/cats/{cat_id}/weights")
async def add_weight(cat_id: int, body: WeightIn, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    try:
        return {"weight": _out(await svc.add_weight(owner_tg, cat_id, body.grams, body.weighed_on, body.note))}
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)


@router.patch("/weights/{weight_id}")
async def patch_weight(weight_id: int, body: WeightPatch, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    row = await asyncio.to_thread(repo.set_weight_visible, await _uid(owner_tg), weight_id, body.sitter_visible)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"weight": _out(row)}


@router.delete("/weights/{weight_id}")
async def delete_weight(weight_id: int, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    if not await asyncio.to_thread(repo.delete_weight, await _uid(owner_tg), weight_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


# ── визиты / прививки ─────────────────────────────────────────────────────────────────────────────────────────────────

class VisitIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    visit_on: date
    kind: Kind = "visit"
    clinic: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)
    next_due: Optional[date] = None
    status: VisitStatus = "planned"
    sitter_visible: bool = False


class VisitPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    visit_on: Optional[date] = None
    kind: Optional[Kind] = None
    clinic: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)
    next_due: Optional[date] = None
    status: Optional[VisitStatus] = None
    sitter_visible: Optional[bool] = None


@router.get("/cats/{cat_id}/vet")
async def list_visits(cat_id: int, status: Optional[VisitStatus] = None, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    try:
        rows = await asyncio.to_thread(repo.list_visits, await _uid(owner_tg), cat_id, status)
    except repo.NotFound as e:
        raise _guard(e)
    return {"visits": [_out(r) for r in rows]}


@router.post("/cats/{cat_id}/vet")
async def add_visit(cat_id: int, body: VisitIn, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    try:
        v = await svc.add_visit(owner_tg, cat_id, body.title, body.visit_on, kind=body.kind, clinic=body.clinic, note=body.note,
                                next_due=body.next_due, status=body.status, sitter_visible=body.sitter_visible)
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)
    return {"visit": _out(v)}


@router.patch("/vet/{visit_id}")
async def patch_visit(visit_id: int, body: VisitPatch, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    values = body.model_dump(exclude_unset=True)                  # переданный null у next_due / clinic / note очищает поле
    for k in ("title", "visit_on", "kind", "status", "sitter_visible"):
        if k in values and values[k] is None:
            raise HTTPException(status_code=422, detail=f"{k} must not be null")
    try:
        v = await svc.update_visit(owner_tg, visit_id, values)
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)
    if v is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"visit": _out(v)}


@router.delete("/vet/{visit_id}")
async def delete_visit(visit_id: int, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    if not await svc.delete_visit(owner_tg, visit_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


# ── курсы лекарств ────────────────────────────────────────────────────────────────────────────────────────────────────

class MedIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    starts_on: Optional[date] = None
    days: Optional[int] = Field(default=None, ge=1, le=365)
    dose: Optional[str] = Field(default=None, max_length=80)
    times_per_day: int = Field(default=1, ge=1, le=4)
    note: Optional[str] = Field(default=None, max_length=500)
    sitter_hidden: bool = False


class MedPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    starts_on: Optional[date] = None
    days: Optional[int] = Field(default=None, ge=1, le=365)
    dose: Optional[str] = Field(default=None, max_length=80)
    times_per_day: Optional[int] = Field(default=None, ge=1, le=4)
    note: Optional[str] = Field(default=None, max_length=500)
    status: Optional[MedStatus] = None
    sitter_hidden: Optional[bool] = None


@router.get("/cats/{cat_id}/meds")
async def list_meds(cat_id: int, status: Optional[MedStatus] = None, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    await svc.finish_expired(owner_tg)
    try:
        rows = await asyncio.to_thread(repo.list_meds, await _uid(owner_tg), cat_id, status)
    except repo.NotFound as e:
        raise _guard(e)
    return {"meds": [_out(r) for r in rows]}


@router.post("/cats/{cat_id}/meds")
async def add_med(cat_id: int, body: MedIn, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    try:
        m = await svc.add_med(owner_tg, cat_id, body.name, body.starts_on, days=body.days, dose=body.dose, times_per_day=body.times_per_day,
                              note=body.note, sitter_hidden=body.sitter_hidden)
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)
    return {"med": _out(m)}


@router.patch("/meds/{med_id}")
async def patch_med(med_id: int, body: MedPatch, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    for k in ("name", "starts_on", "times_per_day", "status", "sitter_hidden"):
        if k in values and values[k] is None:
            raise HTTPException(status_code=422, detail=f"{k} must not be null")
    try:
        m = await svc.update_med(owner_tg, med_id, values)
    except (repo.NotFound, ValueError) as e:
        raise _guard(e)
    if m is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"med": _out(m)}


@router.delete("/meds/{med_id}")
async def delete_med(med_id: int, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    if not await svc.delete_med(owner_tg, med_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


# ── неразобранные факты про котов (быстрая разметка блока владельцем из приложения cats) ──────────────────────────────────

@router.get("/facts/unsorted")
async def unsorted_facts(owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    """Факты про котов без блока (NULL или unsorted), только факты владельца: для разметки одним нажатием. Токен здоровья — токен владельца: ситтеру он не выдаётся."""
    user_id = await _uid(owner_tg)
    facts = await asyncio.to_thread(cats_repo.list_unsorted_facts, user_id)
    return {"facts": [{"id": f["id"], "text": f["text"], "cat": f["cat"]} for f in facts]}


class FactBlock(BaseModel):
    block: str


@router.patch("/facts/{memory_id}/block")
async def set_fact_block(memory_id: int, body: FactBlock, owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    """Поставить блок факту (то же правило, что PATCH /api/cats/facts/{id}/block): неизвестный блок 422, чужой / не про котов / карточка человека 404."""
    if body.block not in cat_blocks.BLOCKS:
        raise HTTPException(status_code=422, detail="bad block")
    user_id = await _uid(owner_tg)
    if not await asyncio.to_thread(cats_repo.set_fact_block, user_id, memory_id, body.block):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "fact_id": memory_id, "block": body.block}


# ── сводка ────────────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/upcoming")
async def upcoming(days: int = Query(60, ge=1, le=365), owner_tg: int = Depends(cats_write_gate)) -> Dict[str, Any]:
    """Ближайшие события по всем котам (запланированные и просроченные визиты, срок следующей прививки, конец курса) и действующие курсы.

    Часовой пояс владельца учитывает ТОЛЬКО Nexus: «сегодня» считается здесь, каждое событие приходит с готовой датой `date` (ГГГГ-ММ-ДД) и числом дней
    до неё `days_until` (0 — сегодня, меньше нуля — просрочено), у курса `days_left` (0 — последний день сегодня, None — без конца). Потребитель
    (приложение cats) ничего не пересчитывает и не сравнивает со своей датой."""
    await svc.finish_expired(owner_tg)
    user_id = await _uid(owner_tg)
    today, _tz = await svc._today_tz(owner_tg)
    events = await asyncio.to_thread(repo.upcoming, user_id, today, days)
    meds = await asyncio.to_thread(repo.current_meds, user_id, today)
    out_events = [{**_out(e), "days_until": (e["date"] - today).days} for e in events]
    out_meds = [{**_out(m), "days_left": (m["ends_on"] - today).days if m["ends_on"] else None} for m in meds]
    return {"today": today.isoformat(), "events": out_events, "current_meds": out_meds}
