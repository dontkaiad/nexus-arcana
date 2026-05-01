"""miniapp/backend/routes/arcana_sessions.py — sessions as grouping over Расклады.

Главная единица — «Сессия» (поле rich_text «Сессия» в Notion). Все триплеты
с одинаковым session_name + client = одна сессия. Записи без session_name —
одиночные сессии (legacy: 1 триплет = 1 сессия).

Endpoints:
  GET  /api/arcana/sessions                    — лента сессий (агрегаты)
  GET  /api/arcana/sessions/by-slug/{slug}     — сессия со всеми триплетами
  POST /api/arcana/sessions/by-slug/{slug}/summarize — сгенерировать общее саммари
  GET  /api/arcana/sessions/{session_id}       — legacy: detail одного триплета
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.notion_client import get_page, sessions_all
from core.session_cache import (
    cache_delete,
    cache_get,
    cache_set,
    session_summary_key,
    slugify,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    first_emoji,
    multi_select_names,
    number_of,
    relation_ids_of,
    rich_text_plain,
    select_of,
    title_plain,
    to_local_date,
    today_user_tz,
)
from miniapp.backend.routes._arcana_common import (
    extract_bottom_from_interp,
    load_clients_map,
    serialize_session_brief,
    client_name_from,
)
from miniapp.backend.tarot import canonical_card, parse_cards_raw, resolve_deck_id

logger = logging.getLogger("miniapp.arcana.sessions")

router = APIRouter()


# ── helpers ─────────────────────────────────────────────────────────────────

_VERDICT_ICON = {
    "✅ Да": "yes", "〰️ Частично": "half", "❌ Нет": "no",
    "⏳ Не проверено": "wait", "": "wait",
}


def _verdict_of(page: dict) -> str:
    raw = select_of(page, "Сбылось") or "⏳ Не проверено"
    return _VERDICT_ICON.get(raw, "wait")


def _compute_status(verdicts: list[str]) -> str:
    """wait | proc | part | done — статус сессии по списку вердиктов триплетов.

    все wait → wait; все yes → done; все no → done; смесь без wait → part;
    смесь с wait → proc.
    """
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


def _breakdown(verdicts: list[str]) -> dict[str, int]:
    out = {"yes": 0, "half": 0, "no": 0, "wait": 0}
    for v in verdicts:
        out[v if v in out else "wait"] += 1
    return out


def _index_in_title(title: str) -> Optional[int]:
    """Извлекает '1)…', '#2', 'вопрос 3' → 1/2/3 для сортировки."""
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


def _session_name_of(page: dict) -> str:
    return (rich_text_plain(page, "Сессия") or "").strip()


def _triplet_summary_of(page: dict) -> str:
    return (rich_text_plain(page, "Саммари триплета") or "").strip()


def _slug_for(session_name: str, client_id: Optional[str]) -> str:
    return f"{slugify(session_name)}__{client_id or 'self'}"


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

    sbylos_filter: Optional[str] = None
    status_f = filters.get("status")
    if status_f == "unchecked" or status_f == "wait":
        sbylos_filter = "⏳ Не проверено"
    elif status_f == "done":
        sbylos_filter = "✅ Да"

    all_pages = await sessions_all(
        user_notion_id=user_notion_id, sbylos_filter=sbylos_filter
    )
    clients_map = await load_clients_map(user_notion_id)

    area_f = filters.get("area")
    client_f = filters.get("client_id")

    # Группируем по (session_name, client_id). Если session_name == "" — ключ
    # уникальный per-page (= отдельная сессия из одного триплета).
    groups: dict[tuple[str, Optional[str]], list[dict]] = {}
    for p in all_pages:
        if client_f and client_f not in relation_ids_of(p, "👥 Клиенты"):
            continue
        if area_f and area_f not in multi_select_names(p, "Область"):
            continue
        sname = _session_name_of(p)
        cids = relation_ids_of(p, "👥 Клиенты")
        cid = cids[0] if cids else None
        key = (sname, cid) if sname else (f"__solo__{p.get('id','')}", cid)
        groups.setdefault(key, []).append(p)

    items: list[dict] = []
    for (sname_or_solo, cid), pages in groups.items():
        # сортируем триплеты внутри группы: по индексу в названии, потом по дате
        pages.sort(key=lambda x: (
            _index_in_title(title_plain(x, "Тема")) or 9999,
            (x.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", ""),
        ))
        first = pages[0]
        verdicts = [_verdict_of(p) for p in pages]
        status = _compute_status(verdicts)

        sname = "" if sname_or_solo.startswith("__solo__") else sname_or_solo
        is_solo = not sname

        client_name, client_id = client_name_from(first, clients_map)
        session_type = select_of(first, "Тип сеанса") or ""
        decks: list[str] = []
        for p in pages:
            for d in multi_select_names(p, "Колоды"):
                if d not in decks:
                    decks.append(d)

        first_date_raw = (first.get("properties", {})
                          .get("Дата", {}).get("date") or {}).get("start", "")
        date_local = to_local_date(first_date_raw, tz_offset)

        ru_title = sname or title_plain(first, "Тема") or "—"
        first_q = title_plain(first, "Тема") or ""

        # категория сессии: «Тип расклада» первого триплета
        cat_list = multi_select_names(first, "Тип расклада")
        category = cat_list[0] if cat_list else (
            "🌐 Сфера жизни" if not is_solo else "🔺 Триплет"
        )

        items.append({
            "slug": _slug_for(sname, client_id) if sname else first.get("id", ""),
            "session_name": sname or None,
            "ru_title": ru_title,
            "first_question": first_q,
            "category": category,
            "client": client_name,
            "client_id": client_id,
            "type": session_type,
            "decks": decks,
            "first_date": date_local.isoformat() if date_local else None,
            "triplet_count": len(pages),
            "status": status,
            "breakdown": _breakdown(verdicts),
            "is_solo": is_solo,
            # для совместимости старого фронта — отдаём минимум полей одиночного триплета
            "id": first.get("id", "") if is_solo else None,
            "done": select_of(first, "Сбылось") or "⏳ Не проверено",
        })

    # Сортировка: незавершённые впереди, потом по дате DESC
    rank = {"wait": 0, "proc": 0, "part": 1, "done": 2}
    items.sort(key=lambda x: (rank.get(x["status"], 9), -(x["first_date"] or "")
                              .replace("-", "").isdigit() and 0,
                              x["first_date"] or ""), reverse=False)
    # выше — стабильно отсортируем заново: сначала по rank ASC, потом по дате DESC
    items.sort(key=lambda x: (rank.get(x["status"], 9),
                              -(int(x["first_date"].replace("-", ""))
                                if x["first_date"] else 0)))

    return {
        "filter": filter,
        "total": len(items),
        "sessions": items[:limit],
    }


# ── single triplet detail (legacy / solo) ──────────────────────────────────

async def _serialize_triplet(page: dict, clients_map: dict, tz_offset: int) -> dict:
    interp_raw = rich_text_plain(page, "Трактовка")
    bottom_name, interp_cleaned = extract_bottom_from_interp(interp_raw)
    cards_raw = rich_text_plain(page, "Карты")

    deck_raw = ", ".join(multi_select_names(page, "Колоды")) or None
    deck_id = resolve_deck_id(deck_raw)

    cards = parse_cards_raw(cards_raw, deck_id) if cards_raw else []
    bottom_card = canonical_card(deck_id, bottom_name) if bottom_name else None

    client_name, client_id = client_name_from(page, clients_map)
    session_type = select_of(page, "Тип сеанса")
    self_client = (session_type == "🌟 Личный") and not relation_ids_of(page, "👥 Клиенты")

    deadline_raw = (page.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
    date_local = to_local_date(deadline_raw, tz_offset)
    photo_url = (page.get("properties", {}).get("Фото", {}).get("url")) or None

    return {
        "id": page.get("id", ""),
        "question": title_plain(page, "Тема"),
        "client": client_name,
        "client_id": client_id,
        "self_client": self_client,
        "area": multi_select_names(page, "Область"),
        "deck": deck_raw,
        "deck_id": deck_id,
        "type": (multi_select_names(page, "Тип расклада") or [None])[0],
        "date": date_local.isoformat() if date_local else None,
        "cards_raw": cards_raw or None,
        "cards": cards,
        "bottom_card": bottom_card,
        "bottom": (
            {"name": bottom_name, "icon": first_emoji(bottom_name) or None}
            if bottom_name else None
        ),
        "interpretation": interp_cleaned or None,
        "summary": _triplet_summary_of(page) or None,
        "verdict": _verdict_of(page),
        "done": select_of(page, "Сбылось") or "⏳ Не проверено",
        "price": int(round(number_of(page, "Сумма"))),
        "paid": int(round(number_of(page, "Оплачено"))),
        "photo_url": photo_url,
    }


@router.get("/arcana/sessions/by-slug/{slug}")
async def session_by_slug(
    slug: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Сессия по slug (slugify(session_name) + '__' + client_id|self).

    Legacy fallback: если slug не вида '*__*' — пробуем как notion page id.
    """
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)
    clients_map = await load_clients_map(user_notion_id)

    # Прямой fallback на единичную страницу — если slug это id (нет '__')
    if "__" not in slug and len(slug) >= 16:
        try:
            page = await get_page(slug)
        except Exception:
            page = None
        if page:
            owners = relation_ids_of(page, "🪪 Пользователи")
            if user_notion_id and user_notion_id not in owners:
                raise HTTPException(status_code=404, detail="session not found")
            t = await _serialize_triplet(page, clients_map, tz_offset)
            return {
                "slug": slug,
                "session_name": None,
                "ru_title": t["question"],
                "first_question": t["question"],
                "category": t["type"],
                "client": t["client"],
                "client_id": t["client_id"],
                "type": select_of(page, "Тип сеанса") or "",
                "decks": [t["deck"]] if t["deck"] else [],
                "first_date": t["date"],
                "summary": None,
                "is_solo": True,
                "triplets": [t],
            }

    # Группа по сессии: ищем pages с совпадающим slug
    all_pages = await sessions_all(user_notion_id=user_notion_id)
    matching: list[dict] = []
    for p in all_pages:
        sname = _session_name_of(p)
        cids = relation_ids_of(p, "👥 Клиенты")
        cid = cids[0] if cids else None
        if sname and _slug_for(sname, cid) == slug:
            matching.append(p)
    if not matching:
        raise HTTPException(status_code=404, detail="session not found")

    matching.sort(key=lambda x: (
        _index_in_title(title_plain(x, "Тема")) or 9999,
        (x.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", ""),
    ))

    triplets = [await _serialize_triplet(p, clients_map, tz_offset) for p in matching]
    first = matching[0]
    sname = _session_name_of(first)
    cids = relation_ids_of(first, "👥 Клиенты")
    cid = cids[0] if cids else None
    client_name, client_id = client_name_from(first, clients_map)

    cat_list = multi_select_names(first, "Тип расклада")
    category = cat_list[0] if cat_list else "🌐 Сфера жизни"

    decks: list[str] = []
    for p in matching:
        for d in multi_select_names(p, "Колоды"):
            if d not in decks:
                decks.append(d)

    first_date_raw = (first.get("properties", {})
                      .get("Дата", {}).get("date") or {}).get("start", "")
    date_local = to_local_date(first_date_raw, tz_offset)

    summary_cached = cache_get(session_summary_key(sname, cid)) if sname else None

    return {
        "slug": slug,
        "session_name": sname or None,
        "ru_title": sname or title_plain(first, "Тема"),
        "first_question": title_plain(first, "Тема"),
        "category": category,
        "client": client_name,
        "client_id": client_id,
        "type": select_of(first, "Тип сеанса") or "",
        "decks": decks,
        "first_date": date_local.isoformat() if date_local else None,
        "summary": summary_cached,
        "is_solo": False,
        "triplets": triplets,
    }


@router.post("/arcana/sessions/by-slug/{slug}/summarize")
async def session_summarize(
    slug: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    from core.claude_client import ask_claude

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)
    clients_map = await load_clients_map(user_notion_id)

    all_pages = await sessions_all(user_notion_id=user_notion_id)
    matching: list[dict] = []
    sname = ""
    cid: Optional[str] = None
    for p in all_pages:
        s = _session_name_of(p)
        cids = relation_ids_of(p, "👥 Клиенты")
        c = cids[0] if cids else None
        if s and _slug_for(s, c) == slug:
            matching.append(p)
            sname, cid = s, c
    if not matching or not sname:
        raise HTTPException(status_code=404, detail="session not found")

    cache_key = session_summary_key(sname, cid)
    cached = cache_get(cache_key)
    if cached:
        return {"summary": cached, "cached": True}

    matching.sort(key=lambda x: (
        _index_in_title(title_plain(x, "Тема")) or 9999,
        (x.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", ""),
    ))

    parts: list[str] = []
    for p in matching:
        q = title_plain(p, "Тема") or "—"
        cards = rich_text_plain(p, "Карты") or ""
        ts = _triplet_summary_of(p)
        parts.append(f"❓ {q}\n   🃏 {cards}\n   ✏️ {ts}")
    aggregated = "\n\n".join(parts)

    prompt = (
        f"Ты — ассистент-таролог. Сделай ОБЩЕЕ саммари сессии «{sname}» "
        f"({len(matching)} триплетов): что в целом показывают карты, "
        f"какой вектор, на что обратить внимание. 3-5 предложений, без HTML, "
        f"обращайся на 'ты'.\n\n"
        f"--- ТРИПЛЕТЫ ---\n{aggregated}"
    )
    try:
        summary = await ask_claude(
            prompt, max_tokens=500, model="claude-sonnet-4-20250514"
        )
    except Exception as e:
        logger.error("session summarize failed: %s", e)
        raise HTTPException(status_code=500, detail="summarize failed")

    summary = (summary or "").strip()
    if not summary:
        raise HTTPException(status_code=500, detail="empty summary")
    cache_set(cache_key, summary)
    return {"summary": summary, "cached": False}


# ── single (legacy) ─────────────────────────────────────────────────────────


@router.get("/arcana/sessions/{session_id}")
async def session_detail(
    session_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        page = await get_page(session_id)
    except Exception as e:
        logger.warning("session get_page failed: %s", e)
        raise HTTPException(status_code=404, detail="session not found")
    if not page:
        raise HTTPException(status_code=404, detail="session not found")

    owners = relation_ids_of(page, "🪪 Пользователи")
    if user_notion_id and user_notion_id not in owners:
        raise HTTPException(status_code=404, detail="session not found")

    clients_map = await load_clients_map(user_notion_id)
    _, tz_offset = await today_user_tz(tg_id)
    return await _serialize_triplet(page, clients_map, tz_offset)
