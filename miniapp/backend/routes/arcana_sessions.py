"""miniapp/backend/routes/arcana_sessions.py — sessions as grouping over Расклады.

Главная единица — «Сессия» (поле session_name в PG). Все триплеты
с одинаковым session_name + client = одна сессия. Записи без session_name —
одиночные сессии (1 триплет = 1 сессия).

Endpoints:
  GET  /api/arcana/sessions                    — лента сессий (агрегаты)
  GET  /api/arcana/sessions/by-slug/{slug}     — сессия со всеми триплетами
  POST /api/arcana/sessions/by-slug/{slug}/summarize — сгенерировать общее саммари
  GET  /api/arcana/sessions/{session_id}       — detail одного триплета
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from arcana.repos.clients_repo import ClientsRepo
from arcana.repos.pg_sessions_repo import PgSessionsRepo
from arcana.repos.sessions_repo import TripletEntry
from core.session_cache import (
    cache_get,
    cache_set,
    session_summary_key,
    slugify,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    first_emoji,
    to_local_date,
    today_user_tz,
)
from miniapp.backend.routes._arcana_common import (
    extract_bottom_from_interp,
)
from miniapp.backend.tarot import canonical_card, parse_cards_raw, resolve_deck_id

logger = logging.getLogger("miniapp.arcana.sessions")

router = APIRouter()

_sessions_repo = PgSessionsRepo()
_clients_repo = ClientsRepo()


# ── helpers ─────────────────────────────────────────────────────────────────

_OUTCOME_VERDICT = {
    "yes": "yes", "partial": "half", "no": "no", "unverified": "wait",
}
_OUTCOME_DONE_LABEL = {
    "yes": "✅ Да", "partial": "〰️ Частично",
    "no": "❌ Нет", "unverified": "⏳ Не проверено",
}


def _verdict_of(t: TripletEntry) -> str:
    return _OUTCOME_VERDICT.get(t.outcome or "unverified", "wait")


def _compute_status(verdicts: List[str]) -> str:
    if not verdicts:
        return "wait"
    s = set(verdicts)
    if s == {"wait"}:
        return "wait"
    if s == {"yes"}:
        return "done"
    if s == {"no"}:
        return "done"
    if "wait" in s:
        return "proc"
    return "part"


def _breakdown(verdicts: List[str]) -> dict:
    out = {"yes": 0, "half": 0, "no": 0, "wait": 0}
    for v in verdicts:
        out[v if v in out else "wait"] += 1
    return out


def _index_in_title(title: str) -> Optional[int]:
    m = re.search(r"(?:^|\s|#)(\d{1,2})\s*[)\.\-:]", title or "")
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = re.search(r"вопрос\s+(\d{1,2})", (title or "").lower())
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _slug_for(session_name: str, client_id: Optional[str]) -> str:
    return f"{slugify(session_name)}__{client_id or 'self'}"


def _clients_name_map(clients_list) -> dict:
    """Build {pg_client_id: name} map from ClientsRepo.list_all() result."""
    return {c.id: (c.name or "") for c in clients_list}


def _clients_type_map(clients_list) -> dict:
    """Build {pg_client_id: type_full} map."""
    from arcana.repos.pg_clients_repo import TYPE_CODE_TO_FULL
    return {c.id: TYPE_CODE_TO_FULL.get(c.type_code or "", "") for c in clients_list}


# ── serializers ─────────────────────────────────────────────────────────────

def _serialize_triplet_pg(
    t: TripletEntry,
    name_map: dict,
    tz_offset: int,
) -> dict:
    """Serialize a TripletEntry to the detail API format."""
    interp_raw = t.interpretation or ""
    bottom_name_legacy, interp_cleaned = extract_bottom_from_interp(interp_raw)

    deck_raw = t.deck or None
    deck_id = resolve_deck_id(deck_raw)

    bottom_name = t.bottom_card or bottom_name_legacy or ""
    cards = parse_cards_raw(t.cards, deck_id) if t.cards else []
    bottom_card = canonical_card(deck_id, bottom_name) if bottom_name else None

    client_id = t.client_id
    client_name = name_map.get(client_id, "Личный") if client_id else "Личный"

    date_local = to_local_date(t.date or "", tz_offset)

    verdict = _verdict_of(t)
    done_label = _OUTCOME_DONE_LABEL.get(t.outcome or "unverified", "⏳ Не проверено")

    return {
        "id": t.id,
        "question": t.question or "",
        "client": client_name,
        "client_id": client_id,
        "self_client": client_id is None,
        "area": [t.area] if t.area else [],
        "deck": deck_raw,
        "deck_id": deck_id,
        "type": t.category_display or None,
        "date": date_local.isoformat() if date_local else None,
        "cards_raw": t.cards or None,
        "cards": cards,
        "bottom_card": bottom_card,
        "bottom": (
            {"name": bottom_name, "icon": first_emoji(bottom_name) or None}
            if bottom_name else None
        ),
        "interpretation": interp_cleaned or None,
        "summary": t.triplet_summary or None,
        "verdict": verdict,
        "done": done_label,
        "price": int(round(float(t.amount or 0))),
        "paid": int(round(float(t.paid or 0))),
        "photo_url": t.photo_url,
    }


# ── list ────────────────────────────────────────────────────────────────────

def _parse_filter(filter_str: str) -> dict:
    if not filter_str or filter_str == "all":
        return {}
    out: dict = {}
    for chunk in filter_str.split("|"):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        out[k.strip()] = v.strip()
    return out


@router.get("/arcana/sessions")
async def list_sessions(
    tg_id: int = Depends(current_user_id),
    filter: str = Query("all"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    filters = _parse_filter(filter)
    today_date, tz_offset = await today_user_tz(tg_id)
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    outcome_filter: Optional[str] = None
    status_f = filters.get("status")
    if status_f in ("unchecked", "wait"):
        outcome_filter = "unverified"
    elif status_f == "done":
        outcome_filter = "yes"

    all_triplets = await _sessions_repo.list_all(
        user_notion_id=user_notion_id, outcome_filter=outcome_filter
    )
    clients_list = await _clients_repo.list_all(user_notion_id)
    name_map = _clients_name_map(clients_list)
    type_map = _clients_type_map(clients_list)

    area_f = filters.get("area")
    client_f = filters.get("client_id")

    # Group by (session_name, client_id). Empty session_name → solo triplet.
    from typing import Dict, Tuple
    groups: Dict[Tuple[str, Optional[str]], List[TripletEntry]] = {}
    for t in all_triplets:
        if client_f and t.client_id != client_f:
            continue
        if area_f and t.area != area_f:
            continue
        key = (t.session_name, t.client_id) if t.session_name else (f"__solo__{t.id}", t.client_id)
        groups.setdefault(key, []).append(t)

    items: List[dict] = []
    for (sname_or_solo, cid), triplets in groups.items():
        triplets.sort(key=lambda x: (
            _index_in_title(x.question) or 9999,
            x.date or "",
        ))
        first = triplets[0]
        verdicts = [_verdict_of(t) for t in triplets]
        status = _compute_status(verdicts)

        sname = "" if sname_or_solo.startswith("__solo__") else sname_or_solo
        is_solo = not sname

        client_name = name_map.get(cid, "Личный") if cid else "Личный"
        ctype_full = type_map.get(cid, "") if cid else ""
        client_type_icon = ctype_full.split()[0] if ctype_full else ""

        decks: List[str] = []
        for t in triplets:
            if t.deck and t.deck not in decks:
                decks.append(t.deck)

        all_dates = [t.date for t in triplets if t.date]
        first_date = min(all_dates) if all_dates else None
        last_date = max(all_dates) if all_dates else None
        first_date_local = to_local_date(first_date or "", tz_offset)
        last_date_local = to_local_date(last_date or "", tz_offset)

        ru_title = sname or first.question or "—"
        first_q = first.question or ""
        category = first.category_display or None

        group_has_barter = any(bool(t.barter_what) for t in triplets)
        unique_areas = list(dict.fromkeys(t.area for t in triplets if t.area))

        done_label = _OUTCOME_DONE_LABEL.get(first.outcome or "unverified", "⏳ Не проверено")

        items.append({
            "slug": _slug_for(sname, cid) if sname else first.id,
            "session_name": sname or None,
            "ru_title": ru_title,
            "first_question": first_q,
            "category": category,
            "client": client_name,
            "client_id": cid,
            "client_type": client_type_icon,
            "client_type_full": ctype_full,
            "has_barter": group_has_barter,
            "type": "",
            "areas": unique_areas,
            "decks": decks,
            "first_date": first_date_local.isoformat() if first_date_local else None,
            "last_date": last_date_local.isoformat() if last_date_local else None,
            "triplet_count": len(triplets),
            "status": status,
            "breakdown": _breakdown(verdicts),
            "is_solo": is_solo,
            "id": first.id if is_solo else None,
            "first_triplet_id": first.id,
            "done": done_label,
        })

    rank = {"wait": 0, "proc": 0, "part": 1, "done": 2}
    items.sort(key=lambda x: (
        rank.get(x["status"], 9),
        -(int((x["first_date"] or "").replace("-", ""))
          if x["first_date"] else 0)
    ))

    return {
        "filter": filter,
        "total": len(items),
        "sessions": items[:limit],
    }


# ── session by slug ──────────────────────────────────────────────────────────

@router.get("/arcana/sessions/by-slug/{slug}")
async def session_by_slug(
    slug: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)

    clients_list = await _clients_repo.list_all(user_notion_id)
    name_map = _clients_name_map(clients_list)

    # Try numeric id → single triplet (direct GET /by-slug/<pg_id>)
    if "__" not in slug:
        try:
            t = await _sessions_repo.find_by_id(slug)
        except Exception:
            t = None
        if t:
            triplet_data = _serialize_triplet_pg(t, name_map, tz_offset)
            return {
                "slug": slug,
                "session_name": None,
                "ru_title": triplet_data["question"],
                "first_question": triplet_data["question"],
                "category": triplet_data["type"],
                "client": triplet_data["client"],
                "client_id": triplet_data["client_id"],
                "type": "",
                "areas": triplet_data.get("area", []),
                "decks": [triplet_data["deck"]] if triplet_data["deck"] else [],
                "first_date": triplet_data["date"],
                "summary": None,
                "session_summaries": [],
                "photo_url": triplet_data.get("photo_url"),
                "is_solo": True,
                "triplets": [triplet_data],
            }
        raise HTTPException(status_code=404, detail="session not found")

    matching = await _sessions_repo.list_by_slug(slug, user_notion_id)
    if not matching:
        raise HTTPException(status_code=404, detail="session not found")

    matching.sort(key=lambda x: (
        _index_in_title(x.question) or 9999,
        x.date or "",
    ))

    triplets = [_serialize_triplet_pg(t, name_map, tz_offset) for t in matching]
    first = matching[0]
    sname = first.session_name
    cid = first.client_id

    client_name = name_map.get(cid, "Личный") if cid else "Личный"
    category = first.category_display or None
    decks: List[str] = []
    for t in matching:
        if t.deck and t.deck not in decks:
            decks.append(t.deck)

    first_date_local = to_local_date(first.date or "", tz_offset)
    # Сводка ТЕМЫ (кросс-день) — theme_summary якоря группы; кеш — fallback для
    # домиграционных записей (#165).
    theme_summary = (first.theme_summary or "").strip() or None
    if not theme_summary and sname:
        theme_summary = cache_get(session_summary_key(sname, cid))

    # Саммари СОБЫТИЙ по дням: на каждый occurred_at группы — session_summary
    # дневного якоря (первый непустой триплет дня в порядке сортировки), #165.
    _by_day: dict = {}
    for t in matching:  # matching уже отсортирован по (index_in_title, date)
        d = t.date or ""
        if not d:
            continue
        _by_day.setdefault(d, "")
        if not _by_day[d] and (t.session_summary or "").strip():
            _by_day[d] = t.session_summary.strip()
    session_summaries = [
        {"date": d, "summary": _by_day[d] or None} for d in sorted(_by_day)
    ]
    session_photo = next((t["photo_url"] for t in triplets if t.get("photo_url")), None)
    slug_areas = list(dict.fromkeys(t.area for t in matching if t.area))

    return {
        "slug": slug,
        "session_name": sname or None,
        "ru_title": sname or first.question,
        "first_question": first.question,
        "category": category,
        "client": client_name,
        "client_id": cid,
        "type": "",
        "areas": slug_areas,
        "decks": decks,
        "first_date": first_date_local.isoformat() if first_date_local else None,
        "summary": theme_summary,
        "session_summaries": session_summaries,
        "photo_url": session_photo,
        "is_solo": False,
        "triplets": triplets,
    }


# ── session summarize ────────────────────────────────────────────────────────

@router.post("/arcana/sessions/by-slug/{slug}/summarize")
async def session_summarize(
    slug: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    from core.claude_client import ask_claude
    from core.config import config as _cfg

    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    matching = await _sessions_repo.list_by_slug(slug, user_notion_id)
    if not matching or not matching[0].session_name:
        raise HTTPException(status_code=404, detail="session not found")

    matching.sort(key=lambda x: (
        _index_in_title(x.question) or 9999,
        x.date or "",
    ))
    anchor = matching[0]
    sname = anchor.session_name
    cid = anchor.client_id
    cache_key = session_summary_key(sname, cid)

    # Источник истины — theme_summary якоря (кросс-дневная сводка ТЕМЫ, #165).
    # Гейт ТОЛЬКО на theme_summary: если есть — отдаём (не жжём Sonnet); если
    # пуста — пересчитываем. На session_summary/кеш не короткозамыкаемся —
    # session_summary это саммари СОБЫТИЯ (другое), а пустой theme_summary после
    # пополнения темы должен приводить к свежему пересчёту.
    existing = (anchor.theme_summary or "").strip()
    if existing:
        return {"summary": existing, "cached": True}

    parts: List[str] = []
    for t in matching:
        q = t.question or "—"
        cards = t.cards or ""
        ts = t.triplet_summary or ""
        parts.append(f"❓ {q}\n   🃏 {cards}\n   ✏️ {ts}")
    aggregated = "\n\n".join(parts)

    prompt = (
        f"Ты — ассистент-таролог. Сделай ОБЩЕЕ саммари сессии «{sname}» "
        f"({len(matching)} триплетов): что в целом показывают карты, "
        f"какой вектор, на что обратить внимание. 3-5 предложений. "
        f"Output as plain Russian text, no formatting, no markdown, "
        f"no HTML tags, no emojis. Обращайся на 'ты'.\n\n"
        f"--- ТРИПЛЕТЫ ---\n{aggregated}"
    )
    try:
        summary = await ask_claude(
            prompt, max_tokens=500, model=_cfg.model_sonnet, temperature=0.5
        )
    except Exception as e:
        logger.error("session summarize failed: %s", e)
        raise HTTPException(status_code=500, detail="summarize failed")

    from core.html_sanitize import sanitize_summary
    summary = sanitize_summary(summary or "")
    if not summary:
        raise HTTPException(status_code=500, detail="empty summary")
    # Сводка ТЕМЫ — в theme_summary якоря (НЕ в session_summary), кеш fast-path (#165).
    await _sessions_repo.set_theme_summary(anchor.id, summary)
    cache_set(cache_key, summary)
    return {"summary": summary, "cached": False}


# ── single (legacy / direct id) ─────────────────────────────────────────────

@router.get("/arcana/sessions/{session_id}")
async def session_detail(
    session_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    t = await _sessions_repo.find_by_id(session_id)
    if not t:
        raise HTTPException(status_code=404, detail="session not found")

    clients_list = await _clients_repo.list_all(user_notion_id)
    name_map = _clients_name_map(clients_list)
    _, tz_offset = await today_user_tz(tg_id)
    return _serialize_triplet_pg(t, name_map, tz_offset)
