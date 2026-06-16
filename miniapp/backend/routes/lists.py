"""miniapp/backend/routes/lists.py — GET /api/lists."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id
from core.repos.pg_nexus_lists_repo import (
    PgNexusListsRepo,
    PG_STATUS_TO_NOTION,
    PG_PRIORITY_TO_NOTION,
)

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    cat_from_notion,
    checkbox_value,
    date_start,
    extract_time,
    number_value,
    prio_from_notion,
    rich_text,
    select_name,
    status_name,
    title_text,
    to_local_date,
    today_user_tz,
)

_nexus_lists_repo = PgNexusListsRepo()

logger = logging.getLogger("miniapp.lists")

router = APIRouter()

_TYPE_MAP = {
    "buy": "🛒 Покупки",
    "check": "📋 Чеклист",
    "inv": "📦 Инвентарь",
}

# wave6.1: для сопоставления real-Notion опций (с возможным другим emoji-variant
# или пробелом) используем набор ключевых слов. Если точный select match не сработал,
# фильтруем client-side по этим подстрокам.
_TYPE_KEYWORDS = {
    "buy": ("покупк",),
    "check": ("чеклист", "чек-лист", "чеклисты"),
    "inv": ("инвентар",),
}


def _serialize(page: dict) -> dict:
    props = page.get("properties", {})
    status = status_name(props.get("Статус", {}))
    return {
        "id": page.get("id", ""),
        "name": title_text(props.get("Название", {})),
        "cat": cat_from_notion(select_name(props.get("Категория", {}))),
        "done": status == "Done",
        "status": status,
        "qty": number_value(props.get("Количество", {})),
        "price": number_value(props.get("Цена", {})),
        # v1.2 — план / магазин / этап
        "price_plan": number_value(props.get("Цена план", {})),
        "source": rich_text(props.get("Магазин", {})) or None,
        "stage": number_value(props.get("Этап", {})),
        "note": rich_text(props.get("Заметка", {})) or None,
        "priority": select_name(props.get("Приоритет", {})) or None,
        "expires": date_start(props.get("Срок годности", {})) or None,
        "group": rich_text(props.get("Группа", {})) or None,
        "recurring": checkbox_value(props.get("Повторяющийся", {})),
    }


def _serialize_pg(item) -> dict:
    """ListItem → dict совместимый с фронтом (PG путь)."""
    return {
        "id": item.id,
        "name": item.name,
        "cat": cat_from_notion(item.category) if item.category else "",
        "done": item.status == "done",
        "status": PG_STATUS_TO_NOTION.get(item.status, "Not started"),
        "qty": item.quantity,
        "price": item.price_actual,
        "price_plan": item.price_plan,
        "source": item.store or None,
        "stage": item.stage,
        "note": item.note or None,
        "priority": PG_PRIORITY_TO_NOTION.get(item.priority) or None,
        "expires": item.expires_at or None,
        "group": item.group_name or None,
        "recurring": item.is_recurring,
    }


def _summary(items: list[dict]) -> dict:
    """v1.2: агрегации план/факт по списку items (после фильтров)."""
    plan = 0.0
    actual = 0.0
    done = 0
    for it in items:
        if it.get("price_plan"):
            plan += float(it["price_plan"] or 0)
        if it.get("done"):
            done += 1
            if it.get("price"):
                actual += float(it["price"] or 0)
    return {
        "plan_total": plan,
        "actual_total": actual,
        "count_total": len(items),
        "count_open": len(items) - done,
        "count_done": done,
    }


@router.get("/lists")
async def get_lists(
    tg_id: int = Depends(current_user_id),
    type: str = Query("buy", description="buy|check|inv"),
    q: Optional[str] = Query(None, description="case-insensitive contains по Название/Заметка"),
    group: Optional[str] = Query(None, description="точное совпадение по полю Группа"),
) -> dict[str, Any]:
    if type not in _TYPE_MAP:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_TYPE_MAP)}")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        pg_items = await _nexus_lists_repo.get_summary_items(
            user_notion_id, list_type=_TYPE_MAP[type]
        )
    except Exception as e:
        logger.warning("lists PG query failed: %s", e)
        pg_items = []

    items = [_serialize_pg(i) for i in pg_items]
    # get_summary_items excludes archived; double-check
    items = [i for i in items if i["status"] != "Archived"]

    if q:
        needle = q.lower().strip()
        items = [
            i for i in items
            if needle in (i["name"] or "").lower()
            or needle in (i["note"] or "").lower()
        ]

    if group:
        g_target = group.strip().lower()
        items = [i for i in items if (i.get("group") or "").strip().lower() == g_target]

    if type == "inv":
        items.sort(key=lambda i: (i["expires"] is None, i["expires"] or ""))

    if type == "check" and items:
        await _attach_parent_tasks(items, tg_id, user_notion_id)
        if not group:
            items = [
                i for i in items
                if (i.get("parent") or {}).get("status") not in ("Done", "Complete", "Archived")
            ]

    return {"type": type, "items": items, "summary": _summary(items)}


def _norm_title(s: str) -> str:
    """wave8.62.1: нормализация title для матча Группа↔Задача — lowercase + схлопнутые пробелы.
    Чинит регрессию когда title и Группа отличаются регистром/whitespace."""
    if not s:
        return ""
    return " ".join(s.lower().split())


async def _attach_parent_tasks(items: list[dict], tg_id: int, user_notion_id: str) -> None:
    groups = {_norm_title(i.get("group") or "") for i in items if (i.get("group") or "").strip()}
    if not groups:
        return
    db_tasks = getattr(config.nexus, "db_tasks", None) or getattr(config, "db_tasks", None)
    if not db_tasks:
        return
    today_date, tz_offset = await today_user_tz(tg_id)
    # wave8.62.1: user-relation = contains OR is_empty (как для самих items в lists.py:99).
    # Иначе родитель без relation 🪪 Пользователи (создан в Notion-UI) не находится,
    # parent=None, фильтр closed-родителя пропускает item.
    filters: dict = {}
    if user_notion_id:
        filters = {"or": [
            {"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}},
            {"property": "🪪 Пользователи", "relation": {"is_empty": True}},
        ]}
    try:
        pages = await query_pages(db_tasks, filters=filters or None, page_size=500)
    except Exception as e:
        logger.warning("attach_parent_tasks query failed: %s", e)
        return
    by_title: dict[str, dict] = {}
    for p in pages:
        props = p.get("properties", {})
        title_raw = title_text(props.get("Задача", {})).strip()
        title = _norm_title(title_raw)
        if not title or title not in groups or title in by_title:
            continue
        deadline_raw = date_start(props.get("Дедлайн", {}))
        reminder_raw = date_start(props.get("Напоминание", {}))
        deadline_local = to_local_date(deadline_raw, tz_offset)
        deadline_time = extract_time(deadline_raw, tz_offset)
        repeat_time = rich_text(props.get("Время повтора", {})).strip() or None
        repeat = select_name(props.get("Повтор", {})) or None
        reminder_min = None
        if deadline_raw and reminder_raw:
            from datetime import datetime, timezone
            try:
                dl = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
                rm = datetime.fromisoformat(reminder_raw.replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if rm.tzinfo is None:
                    rm = rm.replace(tzinfo=timezone.utc)
                delta = (dl - rm).total_seconds() / 60
                reminder_min = int(round(delta)) if delta > 0 else None
            except ValueError:
                pass
        by_title[title] = {
            "cat": cat_from_notion(select_name(props.get("Категория", {}))),
            "prio": prio_from_notion(select_name(props.get("Приоритет", {}))),
            "deadline": deadline_local.isoformat() if deadline_local else None,
            "deadline_time": deadline_time,
            "repeat": repeat,
            "repeat_time": repeat_time,
            "reminder_min": reminder_min,
            "status": status_name(props.get("Статус", {})),
        }
    for it in items:
        g = _norm_title(it.get("group") or "")
        if g and g in by_title:
            it["parent"] = by_title[g]
