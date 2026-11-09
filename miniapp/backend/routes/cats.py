"""miniapp/backend/routes/cats.py — /api/cats: домен «Коты» (карточка кота + его факты из Памяти).

Раздел строится от таблицы `cats`, а не от категории: карточка = cats + memories.cat_id. Факты категории
«🐾 Коты» без cat_id — отдельный блок «Общее про котов». Доступ как у «Людей»: только владелец.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from core import care
from core.cats import cat_fact_key, group_facts, safe_photo_url
from core.cloudinary_client import cloudinary_upload
from core.people import FACT_TYPES, fact_text
from core.repos import pg_cats_repo as repo
from core.repos.pg_memory_repo import PgMemoryRepo, bot_to_scope
from core.user_manager import get_user_id
from miniapp.backend.auth import current_user_id
from miniapp.backend.cats_read_auth import cats_read_gate

logger = logging.getLogger("miniapp.cats")

router = APIRouter()

PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTO_FOLDER = "cats"


async def _owner(tg_id: int) -> str:
    user_id = await get_user_id(tg_id)
    if not user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return user_id


def _cat_out(c: Dict[str, Any], facts_count: int = 0) -> Dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "aliases": list(c.get("aliases") or []),
        # Только безопасный https-адрес, иначе None (фронт рисует заглушку и кнопку «Загрузить фото»)
        "photo_url": safe_photo_url(c.get("photo_url")),
        "status": c["status"],
        "facts_count": facts_count,
    }


@router.get("/cats", dependencies=[Depends(cats_read_gate)])
async def list_cats(with_facts: bool = Query(False, description="добавить факты по типам каждому коту (один запрос)"),
                    tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    rows: List[Dict[str, Any]] = await asyncio.to_thread(repo.list_cats, user_id)
    ids = [r["id"] for r in rows]
    general = await asyncio.to_thread(repo.list_general_facts, user_id)
    if not with_facts:
        counts = await asyncio.to_thread(repo.facts_count, user_id, ids)
        out = [_cat_out(r, counts.get(r["id"], 0)) for r in rows]
    else:
        by_cat = await asyncio.to_thread(repo.list_facts_many, user_id, ids)
        out = []
        for r in rows:
            facts = by_cat.get(r["id"], [])
            out.append({**_cat_out(r, len(facts)), "facts": group_facts(facts, r["name"], r.get("aliases"))})
    care_block = await asyncio.to_thread(care.care_summary, user_id)
    return {"total": len(rows), "cats": out, "general_count": len(general), "care": care_block}


@router.get("/cats/general")
async def general_facts(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """«Общее про котов». Объявлен ДО /cats/{cat_id}, иначе «general» уйдёт в int-параметр."""
    user_id = await _owner(tg_id)
    facts = await asyncio.to_thread(repo.list_general_facts, user_id)
    return {"total": len(facts), "facts": group_facts(facts, strip_name=False)}


@router.get("/cats/{cat_id}", dependencies=[Depends(cats_read_gate)])
async def cat_card(cat_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    c = await asyncio.to_thread(repo.get_cat, user_id, cat_id)
    if not c or c["status"] == "rejected":
        raise HTTPException(status_code=404, detail="cat not found")
    facts = await asyncio.to_thread(repo.list_facts, user_id, cat_id)
    return {**_cat_out(c, len(facts)), "facts": group_facts(facts, c["name"], c.get("aliases"))}


# ── запись ───────────────────────────────────────────────────────────────────

def _clean_aliases(name: str, aliases: List[str]) -> List[str]:
    """Клички: без пустых, без повторов (без учёта регистра) и без совпадающих с именем."""
    seen = {name.casefold()}
    out: List[str] = []
    for a in aliases or []:
        a = " ".join((a or "").split())
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    return out


class CatCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    aliases: List[str] = Field(default_factory=list, max_length=10)


class CatPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    aliases: Optional[List[str]] = Field(default=None, max_length=10)
    status: Optional[str] = None             # confirmed | rejected («убрать» карточку и вернуть)


async def _get_active(user_id: str, cat_id: int) -> Dict[str, Any]:
    c = await asyncio.to_thread(repo.get_cat, user_id, cat_id)
    if not c or c["status"] == "rejected":
        raise HTTPException(status_code=404, detail="cat not found")
    return c


@router.post("/cats")
async def create_cat(body: CatCreate, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="empty name")
    if any(len(a) > 40 for a in body.aliases):
        raise HTTPException(status_code=422, detail="alias too long")
    if await asyncio.to_thread(repo.name_taken, user_id, name):
        raise HTTPException(status_code=409, detail="cat with this name already exists")
    cid = await asyncio.to_thread(repo.create, user_id, name, _clean_aliases(name, body.aliases))
    c = await asyncio.to_thread(repo.get_cat, user_id, cid)
    return _cat_out(c, 0)


@router.patch("/cats/{cat_id}")
async def patch_cat(cat_id: int, body: CatPatch, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    c = await asyncio.to_thread(repo.get_cat, user_id, cat_id)
    if not c:
        raise HTTPException(status_code=404, detail="cat not found")
    vals: Dict[str, Any] = {}
    if body.status is not None:
        if body.status not in ("confirmed", "rejected"):
            raise HTTPException(status_code=422, detail="bad status")
        vals["status"] = body.status
    name = c["name"]
    if body.name is not None:
        name = " ".join(body.name.split())
        if not name:
            raise HTTPException(status_code=422, detail="empty name")
        vals["name"] = name
    if body.aliases is not None:
        if any(len(a) > 40 for a in body.aliases):
            raise HTTPException(status_code=422, detail="alias too long")
        vals["aliases"] = _clean_aliases(name, body.aliases)
    elif "name" in vals:
        vals["aliases"] = _clean_aliases(name, c.get("aliases") or [])     # новое имя не должно дублироваться кличкой
    if not vals:
        raise HTTPException(status_code=422, detail="nothing to change")
    final_status = vals.get("status", c["status"])
    if final_status != "rejected" and await asyncio.to_thread(repo.name_taken, user_id, name, cat_id):
        raise HTTPException(status_code=409, detail="cat with this name already exists")
    c = await asyncio.to_thread(repo.update_cat, user_id, cat_id, vals)
    n = (await asyncio.to_thread(repo.facts_count, user_id, [cat_id])).get(cat_id, 0)
    return _cat_out(c, n)


@router.post("/cats/{cat_id}/photo")
async def upload_cat_photo(cat_id: int, file: UploadFile = File(...),
                           tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    await _get_active(user_id, cat_id)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="only image/* allowed")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="empty file")
    if len(content) > PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 5 MB)")
    url = await cloudinary_upload(content, file.filename or "cat.jpg", folder=PHOTO_FOLDER)
    if not url:
        raise HTTPException(status_code=501, detail="cloudinary not configured")
    if safe_photo_url(url) is None:                                # ответ хранилища с небезопасным адресом не сохраняем
        raise HTTPException(status_code=502, detail="storage returned an unsafe url")
    await asyncio.to_thread(repo.update_cat, user_id, cat_id, {"photo_url": url})
    return {"ok": True, "url": url}


class CatFactBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    type: str = "other"


@router.post("/cats/{cat_id}/facts")
async def add_cat_fact(cat_id: int, body: CatFactBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Ручной факт о коте: source=manual, сразу confirmed. Повтор того же текста идемпотентен (upsert по ключу)."""
    user_id = await _owner(tg_id)
    c = await _get_active(user_id, cat_id)
    text_ = " ".join(body.text.split())
    if not text_ or body.type not in FACT_TYPES:
        raise HTTPException(status_code=422, detail="bad fact")
    text_ = fact_text(c["name"], text_)                      # «Имя: …» ради контекста бота; в карточке префикс срезается
    mid, was_updated = await PgMemoryRepo().upsert(
        text_, cat_fact_key(cat_id, body.type, text_), repo.CATS_CATEGORY, bot_to_scope("☀️ Nexus"),
        c["name"], "manual", user_id, cat_id=cat_id, review_status="confirmed",
    )
    n = (await asyncio.to_thread(repo.facts_count, user_id, [cat_id])).get(cat_id, 0)
    return {"ok": True, "fact_id": int(mid), "was_updated": bool(was_updated), "facts_count": n}


@router.post("/cats/{cat_id}/facts/{memory_id}/attach")
async def attach_fact(cat_id: int, memory_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Привязать «общий» факт (категория «🐾 Коты» без cat_id) к коту. Чужой/привязанный к другому — 409/404."""
    user_id = await _owner(tg_id)
    c = await _get_active(user_id, cat_id)
    res = await asyncio.to_thread(repo.attach_fact, user_id, c, memory_id)
    if res == "not_found":
        raise HTTPException(status_code=404, detail="fact not found")
    if res == "not_cat_fact":
        raise HTTPException(status_code=422, detail="not a cat fact")
    if res == "taken":
        raise HTTPException(status_code=409, detail="fact already belongs to another cat or person")
    if res == "conflict":
        raise HTTPException(status_code=409, detail="the cat already has this fact")
    n = (await asyncio.to_thread(repo.facts_count, user_id, [cat_id])).get(cat_id, 0)
    return {"ok": True, "fact_id": memory_id, "attached": res == "ok", "facts_count": n}
