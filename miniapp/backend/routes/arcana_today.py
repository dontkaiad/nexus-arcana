"""miniapp/backend/routes/arcana_today.py — GET /api/arcana/today."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.config import config
from core.notion_client import query_pages, rituals_all, sessions_all, update_page_select
from core.user_manager import get_user_notion_id

from miniapp.backend._moon import moon_phase, next_phases
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    cat_from_notion,
    date_start,
    extract_time,
    prio_from_notion,
    select_of,
    title_plain,
    to_local_date,
    today_user_tz,
)
from core.claude_client import ask_claude
from miniapp.backend import cache as _cache
from miniapp.backend.routes._arcana_common import (
    RITUAL_NO,
    RITUAL_PARTIAL,
    RITUAL_UNVERIFIED,
    RITUAL_YES,
    SESSION_NO,
    SESSION_PARTIAL,
    SESSION_UNVERIFIED,
    SESSION_YES,
    SUPPLIES_CATEGORIES,
    load_clients_map,
    month_bounds,
    query_arcana_finance,
    serialize_session_brief,
)

logger = logging.getLogger("miniapp.arcana.today")

router = APIRouter()

_TIP_TTL = 20 * 60 * 60  # 20 часов — обновляется раз в сутки


def _init_tip_cache() -> None:
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS arcana_tip_cache ("
            "tg_id INTEGER PRIMARY KEY, tip TEXT, updated_at INTEGER)"
        )
        con.commit()
    finally:
        con.close()


def _cached_tip(tg_id: int) -> Optional[str]:
    _init_tip_cache()
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        row = con.execute(
            "SELECT tip, updated_at FROM arcana_tip_cache WHERE tg_id = ?", (tg_id,)
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    if time.time() - (row[1] or 0) > _TIP_TTL:
        return None
    return row[0]


def _store_tip(tg_id: int, tip: str) -> None:
    _init_tip_cache()
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO arcana_tip_cache (tg_id, tip, updated_at) VALUES (?, ?, ?)",
            (tg_id, tip, int(time.time())),
        )
        con.commit()
    finally:
        con.close()


@router.get("/arcana/tip")
async def get_arcana_tip(
    tg_id: int = Depends(current_user_id),
    sessions: int = Query(0),
    works: int = Query(0),
) -> dict[str, Any]:
    cached = _cached_tip(tg_id)
    if cached:
        return {"tip": cached}
    if sessions == 0 and works == 0:
        desc = "сегодня нет сеансов и работ"
    elif sessions > 0 and works > 0:
        desc = f"сегодня {sessions} сеанс(а/ов) и {works} работ(а)"
    elif sessions > 0:
        desc = f"сегодня {sessions} сеанс(а/ов), работ нет"
    else:
        desc = f"сегодня {works} работ(а), сеансов нет"
    prompt = (
        f"Ты — ассистент практика эзотерики. Загрузка на сегодня: {desc}.\n"
        "Напиши одну короткую фразу-подпись (5–8 слов, строчными, без точки в конце, без смайлов). "
        "Тон — спокойный, поэтичный, немного мистический. Только сама фраза."
    )
    tip = await ask_claude(prompt, model=config.model_sonnet, max_tokens=60)
    tip = tip.strip().rstrip(".!").lower() if tip else ""
    if tip:
        _store_tip(tg_id, tip)
    return {"tip": tip}


_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _pct(count: int, total: int) -> int:
    return int(round(count / total * 100)) if total else 0


async def _works_schedule(user_notion_id: str, today_date: date, tz_offset: int) -> tuple[list[dict], list[dict]]:
    """Возвращает (overdue, scheduled): просроченные + сегодняшние работы."""
    db_id = config.arcana.db_works
    if not db_id:
        return [], []
    today_iso = today_date.isoformat()
    not_done: list[dict] = [
        {"property": "Status", "status": {"does_not_equal": "Done"}},
        {"property": "Status", "status": {"does_not_equal": "Complete"}},
    ]
    user_cond = [{"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}}] if user_notion_id else []
    date_or = {"or": [
        {"property": "Дедлайн", "date": {"before": today_iso}},
        {"property": "Дедлайн", "date": {"equals": today_iso}},
        {"property": "Напоминание", "date": {"equals": today_iso}},
    ]}
    filters = {"and": not_done + user_cond + [date_or]}
    try:
        pages = await query_pages(db_id, filters=filters, page_size=100)
    except Exception as e:
        logger.warning("works_schedule query failed: %s", e)
        return [], []
    overdue: list[dict] = []
    scheduled: list[dict] = []
    seen: set[str] = set()
    for p in pages:
        pid = p.get("id", "")
        if pid in seen:
            continue
        seen.add(pid)
        props = p.get("properties", {})
        deadline_raw = date_start(props.get("Дедлайн", {}))
        reminder_raw = date_start(props.get("Напоминание", {}))
        deadline_date = to_local_date(deadline_raw, tz_offset)
        reminder_date = to_local_date(reminder_raw, tz_offset)
        title = title_plain(p, "Работа")
        cat = cat_from_notion(select_of(p, "Категория"))
        prio = prio_from_notion(select_of(p, "Приоритет"))
        if deadline_date and deadline_date < today_date:
            overdue.append({
                "id": pid, "title": title, "cat": cat, "prio": prio,
                "days_ago": (today_date - deadline_date).days,
            })
            continue
        # сегодняшние: дедлайн = сегодня или напоминание = сегодня
        if deadline_date == today_date or reminder_date == today_date:
            time_str = extract_time(reminder_raw, tz_offset) if reminder_date == today_date else None
            if not time_str:
                time_str = extract_time(deadline_raw, tz_offset)
            scheduled.append({
                "id": pid, "title": title, "cat": cat, "prio": prio,
                "time": time_str,
            })
    overdue.sort(key=lambda x: -x["days_ago"])
    scheduled.sort(key=lambda x: x["time"] or "")
    return overdue, scheduled


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

    works_overdue, works = await _works_schedule(user_notion_id, today_date, tz_offset)
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

    # Аккуратность по сеансам + ритуалам (взвешенно за всё время)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    acc = _compute_accuracy(all_sessions, rituals, scope="all")
    pending_sessions, pending_rituals = _count_pending(all_sessions, rituals)

    # works_today счётчики (без обвязки с локальным done state)
    works_total_today = len(works) + len(works_overdue)
    works_done_today = 0  # done логика на фронте локальная

    return {
        "date": today_iso,
        "weekday": weekday,
        "tz_offset": tz_offset,
        "moon": moon,
        "sessions_today": sessions_today,
        "works_today": works,
        "works_overdue": works_overdue,
        "unchecked_30d": unchecked,
        "accuracy": accuracy_overall,
        "works_total_today": works_total_today,
        "works_done_today": works_done_today,
        "income_month": int(round(income)),
        "accuracy_pct": acc["pct"],
        "accuracy_checked": acc["total"],
        "accuracy_total": acc["total"] + pending_sessions + pending_rituals,
        "pending_sessions": pending_sessions,
        "pending_rituals": pending_rituals,
        "month_stats": {
            "label": month_label,
            "income": int(round(income)),
            "supplies": int(round(supplies)),
            "accuracy": month_accuracy,
            "sessions": sessions_in_month,
        },
    }


# ── Accuracy: общий компьют + endpoints ─────────────────────────────────────

def _session_verdict(p: dict) -> Optional[str]:
    """→ 'yes' | 'half' | 'no' | None (не проверено)."""
    val = select_of(p, "Сбылось")
    if val == SESSION_YES:
        return "yes"
    if val == SESSION_PARTIAL:
        return "half"
    if val == SESSION_NO:
        return "no"
    return None


def _ritual_verdict(p: dict) -> Optional[str]:
    val = select_of(p, "Результат")
    if val == RITUAL_YES:
        return "yes"
    if val == RITUAL_PARTIAL:
        return "half"
    if val == RITUAL_NO:
        return "no"
    return None


def _compute_accuracy(sessions: list[dict], rituals: list[dict], scope: str) -> dict:
    yes = half = no = 0
    if scope in ("all", "sessions"):
        for p in sessions:
            v = _session_verdict(p)
            if v == "yes":
                yes += 1
            elif v == "half":
                half += 1
            elif v == "no":
                no += 1
    if scope in ("all", "rituals"):
        for p in rituals:
            v = _ritual_verdict(p)
            if v == "yes":
                yes += 1
            elif v == "half":
                half += 1
            elif v == "no":
                no += 1
    total = yes + half + no
    weighted = yes + 0.5 * half
    pct = int(round(weighted / total * 100)) if total else 0
    return {"pct": pct, "yes": yes, "half": half, "no": no, "total": total}


def _count_pending(sessions: list[dict], rituals: list[dict]) -> tuple[int, int]:
    ps = sum(1 for p in sessions if _session_verdict(p) is None)
    pr = sum(1 for p in rituals if _ritual_verdict(p) is None)
    return ps, pr


def _pending_list(sessions: list[dict], rituals: list[dict], scope: str, clients_map: dict) -> list[dict]:
    out: list[dict] = []
    if scope in ("all", "sessions"):
        for p in sessions:
            if _session_verdict(p) is not None:
                continue
            props = p.get("properties", {})
            raw_date = (props.get("Дата", {}).get("date") or {}).get("start", "") or ""
            from miniapp.backend.routes._arcana_common import client_name_from
            client_name, _ = client_name_from(p, clients_map)
            out.append({
                "id": p.get("id", ""),
                "type": "session",
                "title": title_plain(p, "Тема") or "—",
                "client": client_name,
                "date": raw_date[:10] if raw_date else "",
            })
    if scope in ("all", "rituals"):
        for p in rituals:
            if _ritual_verdict(p) is not None:
                continue
            props = p.get("properties", {})
            raw_date = (props.get("Дата", {}).get("date") or {}).get("start", "") or ""
            from miniapp.backend.routes._arcana_common import client_name_from
            client_name, _ = client_name_from(p, clients_map)
            out.append({
                "id": p.get("id", ""),
                "type": "ritual",
                "title": title_plain(p, "Название") or "—",
                "client": client_name,
                "date": raw_date[:10] if raw_date else "",
            })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


@router.get("/arcana/accuracy")
async def get_arcana_accuracy(
    tg_id: int = Depends(current_user_id),
    scope: str = Query("all"),
) -> dict[str, Any]:
    if scope not in ("all", "sessions", "rituals"):
        scope = "all"
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    clients_map = await load_clients_map(user_notion_id)
    acc = _compute_accuracy(sessions, rituals, scope)
    pending_sessions, pending_rituals = _count_pending(sessions, rituals)
    pending = _pending_list(sessions, rituals, scope, clients_map)
    return {
        "pct": acc["pct"],
        "total": acc["total"],
        "checked": {"yes": acc["yes"], "half": acc["half"], "no": acc["no"]},
        "pending": pending,
        "pending_sessions_count": pending_sessions,
        "pending_rituals_count": pending_rituals,
    }


_VERDICT_TO_SESSION = {"yes": SESSION_YES, "half": SESSION_PARTIAL, "no": SESSION_NO}
_VERDICT_TO_RITUAL = {"yes": RITUAL_YES, "half": RITUAL_PARTIAL, "no": RITUAL_NO}


class VerifyAccuracyBody(BaseModel):
    id: str
    type: str  # "session" | "ritual"
    verdict: str  # "yes" | "half" | "no"


@router.post("/arcana/accuracy/verify")
async def post_arcana_accuracy_verify(
    body: VerifyAccuracyBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    if body.verdict not in _VERDICT_TO_SESSION:
        raise HTTPException(status_code=400, detail="verdict must be yes|half|no")
    if body.type == "session":
        ok = await update_page_select(body.id, "Сбылось", _VERDICT_TO_SESSION[body.verdict])
    elif body.type == "ritual":
        ok = await update_page_select(body.id, "Результат", _VERDICT_TO_RITUAL[body.verdict])
    else:
        raise HTTPException(status_code=400, detail="type must be session|ritual")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    acc = _compute_accuracy(sessions, rituals, "all")
    pending_sessions, pending_rituals = _count_pending(sessions, rituals)
    return {
        "ok": True,
        "pct": acc["pct"],
        "total": acc["total"],
        "checked": {"yes": acc["yes"], "half": acc["half"], "no": acc["no"]},
        "pending_sessions_count": pending_sessions,
        "pending_rituals_count": pending_rituals,
    }
