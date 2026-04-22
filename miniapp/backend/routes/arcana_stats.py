"""miniapp/backend/routes/arcana_stats.py — GET /api/arcana/stats."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from core.notion_client import sessions_all
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import select_of, today_user_tz
from miniapp.backend.routes._arcana_common import (
    SESSION_NO,
    SESSION_PARTIAL,
    SESSION_YES,
    month_bounds,
    query_arcana_finance,
)

logger = logging.getLogger("miniapp.arcana.stats")

router = APIRouter()

_RU_MONTH = {
    "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
    "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
    "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь",
}


def _pct(count: int, total: int) -> int:
    return int(round(count / total * 100)) if total else 0


def _last_n_months(today_date, n: int = 6) -> list[str]:
    """Список ['YYYY-MM', ...] последних n месяцев, от свежих к старым."""
    out: list[str] = []
    y, m = today_date.year, today_date.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


@router.get("/arcana/stats")
async def get_arcana_stats(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    today_date, _ = await today_user_tz(tg_id)
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    sessions = await sessions_all(user_notion_id=user_notion_id)

    yes_total = no_total = partial_total = 0
    by_month: dict[str, dict] = {}
    for p in sessions:
        val = select_of(p, "Сбылось")
        raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
        ym = raw[:7] if raw else ""
        bucket = by_month.setdefault(ym, {"total": 0, "yes": 0, "no": 0,
                                          "partial": 0, "pending": 0})
        bucket["total"] += 1
        if val == SESSION_YES:
            yes_total += 1
            bucket["yes"] += 1
        elif val == SESSION_NO:
            no_total += 1
            bucket["no"] += 1
        elif val == SESSION_PARTIAL:
            partial_total += 1
            bucket["partial"] += 1
        else:
            bucket["pending"] += 1

    verified_total = yes_total + no_total + partial_total
    accuracy_overall = _pct(yes_total + partial_total, verified_total)

    months: list[dict] = []
    for ym in _last_n_months(today_date, 6):
        b = by_month.get(ym, {"total": 0, "yes": 0, "no": 0, "partial": 0, "pending": 0})
        verified = b["yes"] + b["no"] + b["partial"]
        months.append({
            "name": _RU_MONTH[ym[5:7]],
            "month": ym,
            "total": b["total"],
            "yes": b["yes"],
            "partial": b["partial"],
            "no": b["no"],
            "pending": b["pending"],
            "pct": _pct(b["yes"] + b["partial"], verified),
        })

    # Practice finance текущего месяца
    cur_month = today_date.strftime("%Y-%m")
    start, end = month_bounds(cur_month)
    try:
        fin = await query_arcana_finance(user_notion_id, start, end)
    except Exception as e:
        logger.warning("arcana finance fetch failed: %s", e)
        fin = []
    income = 0.0
    expense = 0.0
    for p in fin:
        amt = (p.get("properties", {}).get("Сумма", {}).get("number")) or 0
        type_name = select_of(p, "Тип")
        if "Доход" in type_name:
            income += amt
        elif "Расход" in type_name:
            expense += amt

    return {
        "accuracy_overall": accuracy_overall,
        "verified_total": verified_total,
        "months": months,
        "practice_finance": {
            "current_month": {
                "income": int(round(income)),
                "expense": int(round(expense)),
                "profit": int(round(income - expense)),
            }
        },
    }
