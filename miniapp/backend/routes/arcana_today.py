"""miniapp/backend/routes/arcana_today.py — GET /api/arcana/today."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from core.config import config
from core.notion_client import query_pages, sessions_all
from core.user_manager import get_user_notion_id

from miniapp.backend._moon import moon_phase, next_phases
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    cat_from_notion,
    prio_from_notion,
    select_of,
    title_plain,
    to_local_date,
    today_user_tz,
)
from miniapp.backend.routes._arcana_common import (
    SESSION_UNVERIFIED,
    SESSION_YES,
    SESSION_NO,
    SESSION_PARTIAL,
    SUPPLIES_CATEGORIES,
    load_clients_map,
    month_bounds,
    query_arcana_finance,
    serialize_session_brief,
)

logger = logging.getLogger("miniapp.arcana.today")

router = APIRouter()

_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _pct(count: int, total: int) -> int:
    return int(round(count / total * 100)) if total else 0


async def _works_today(user_notion_id: str, today_date: date) -> list[dict]:
    db_id = config.arcana.db_works
    if not db_id:
        return []
    conds: list[dict] = [
        {"property": "Status", "status": {"does_not_equal": "Done"}},
        {"property": "Status", "status": {"does_not_equal": "Complete"}},
        {"property": "Дедлайн", "date": {"equals": today_date.isoformat()}},
    ]
    if user_notion_id:
        conds.append({"property": "🪪 Пользователи",
                      "relation": {"contains": user_notion_id}})
    try:
        pages = await query_pages(db_id, filters={"and": conds}, page_size=50)
    except Exception as e:
        logger.warning("works_today query failed: %s", e)
        return []
    out: list[dict] = []
    for p in pages:
        out.append({
            "id": p.get("id", ""),
            "title": title_plain(p, "Работа"),
            "cat": cat_from_notion(select_of(p, "Категория")),
            "prio": prio_from_notion(select_of(p, "Приоритет")),
        })
    return out


async def _unchecked_30d(sessions: list[dict], today_date: date) -> int:
    cutoff = today_date - timedelta(days=30)
    count = 0
    for p in sessions:
        done = select_of(p, "Сбылось")
        if done not in SESSION_UNVERIFIED:
            continue
        raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
        if not raw:
            continue
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d <= cutoff:
            count += 1
    return count


def _accuracy(sessions: list[dict], date_prefix: str = "") -> tuple[int, int, int]:
    """→ (pct, verified_count, total_in_scope). date_prefix — опционально 'YYYY-MM'."""
    yes = partial = no = 0
    for p in sessions:
        raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
        if date_prefix and not raw.startswith(date_prefix):
            continue
        val = select_of(p, "Сбылось")
        if val == SESSION_YES:
            yes += 1
        elif val == SESSION_PARTIAL:
            partial += 1
        elif val == SESSION_NO:
            no += 1
    verified = yes + partial + no
    pct = _pct(yes + partial, verified)
    return pct, verified, yes + partial + no


@router.get("/arcana/moon-phases")
async def get_moon_phases(
    tg_id: int = Depends(current_user_id),
    count: int = 4,
) -> dict[str, Any]:
    """Следующие N крупных фаз (новолуние, четверти, полнолуние)."""
    count = max(1, min(count, 12))
    current = moon_phase(datetime.now(timezone.utc))
    upcoming = next_phases(count=count)
    return {"current": current, "upcoming": upcoming}


@router.get("/arcana/today")
async def get_arcana_today(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    today_date, tz_offset = await today_user_tz(tg_id)
    today_iso = today_date.isoformat()
    weekday = _WEEKDAYS_RU[today_date.weekday()]
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    # Moon — по текущему UTC
    moon = moon_phase(datetime.now(timezone.utc))

    # Clients map — нужен для sessions_today
    clients_map = await load_clients_map(user_notion_id)

    # Все сеансы юзера (используем и для today, и для unchecked_30d + accuracy)
    all_sessions = await sessions_all(user_notion_id=user_notion_id)

    sessions_today: list[dict] = []
    now_utc = datetime.now(timezone.utc)
    for p in all_sessions:
        raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
        d_local = to_local_date(raw, tz_offset)
        if d_local != today_date:
            continue
        brief = serialize_session_brief(p, clients_map, tz_offset)
        # upcoming/past
        status = "upcoming"
        if brief["date_time"]:
            from datetime import time as _time
            try:
                h, mi = [int(x) for x in brief["date_time"].split(":")]
                session_dt = datetime.combine(
                    today_date, _time(h, mi),
                    tzinfo=timezone(timedelta(hours=tz_offset))
                )
                if now_utc > session_dt:
                    status = "past"
            except ValueError:
                pass
        sessions_today.append({
            "id": brief["id"],
            "time": brief["date_time"],
            "client": brief["client"],
            "client_id": brief["client_id"],
            "self_client": brief["self_client"],
            "type": brief["type"],
            "area": brief["area"],
            "status": status,
        })

    works = await _works_today(user_notion_id, today_date)
    unchecked = await _unchecked_30d(all_sessions, today_date)
    accuracy_overall, _, _ = _accuracy(all_sessions)

    # Month stats
    month = today_date.strftime("%Y-%m")
    month_label = {
        "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
        "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
        "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь",
    }[month[5:7]]
    m_start, m_end = month_bounds(month)
    try:
        fin_records = await query_arcana_finance(user_notion_id, m_start, m_end)
    except Exception as e:
        logger.warning("arcana finance fetch failed: %s", e)
        fin_records = []
    income = 0.0
    supplies = 0.0
    for p in fin_records:
        props = p.get("properties", {})
        amt = (props.get("Сумма", {}).get("number")) or 0
        type_name = select_of(p, "Тип")
        cat = select_of(p, "Категория")
        if "Доход" in type_name:
            income += amt
        elif "Расход" in type_name and cat in SUPPLIES_CATEGORIES:
            supplies += amt
    month_accuracy, _, sessions_in_month = _accuracy(all_sessions, month)

    return {
        "date": today_iso,
        "weekday": weekday,
        "tz_offset": tz_offset,
        "moon": moon,
        "sessions_today": sessions_today,
        "works_today": works,
        "unchecked_30d": unchecked,
        "accuracy": accuracy_overall,
        "month_stats": {
            "label": month_label,
            "income": int(round(income)),
            "supplies": int(round(supplies)),
            "accuracy": month_accuracy,
            "sessions": sessions_in_month,
        },
    }
