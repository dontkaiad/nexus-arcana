"""miniapp/backend/routes/arcana_today.py — GET /api/arcana/today."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.repos.pg_finance_repo import PgArcanaPnlRepo
from arcana.repos.pg_sessions_repo import PgSessionsRepo as _PgSessionsRepoClass
_pg_sessions_repo = _PgSessionsRepoClass()
from arcana.repos.pg_works_repo import PgWorksRepo as _PgWorksRepoClass
_pg_works_repo = _PgWorksRepoClass()
from arcana.repos.pg_rituals_repo import PgRitualsRepo as _PgRitualsRepoClass
_pg_rituals_repo = _PgRitualsRepoClass()
from core.repos.pg_nexus_lists_repo import PgArcanaInventoryRepo as _PgArcanaInventoryRepoClass
_arcana_inv_repo_lists = _PgArcanaInventoryRepoClass()
_pnl_repo = PgArcanaPnlRepo()
from core.user_manager import get_user_notion_id
from core.bot_notify import notify_user

from miniapp.backend._moon import moon_phase, next_phases
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    cat_from_notion,
    date_start,
    extract_time,
    multi_select_names,
    prio_from_notion,
    rich_text_plain,
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
    RITUAL_YES,
    SESSION_NO,
    SESSION_PARTIAL,
    SESSION_UNVERIFIED,
    SESSION_YES,
    SUPPLIES_CATEGORIES,
    load_clients_map,
    ritual_to_stub,
    serialize_session_brief,
    triplet_to_stub,
)


async def _load_rituals(user_notion_id: str) -> list[dict]:
    """Все ритуалы юзера из PG → Notion-подобные стабы для аналитики.

    Defensive: при сбое запроса возвращает [] (вкладка «Сегодня» не падает),
    как раньше делал Notion-путь."""
    try:
        _pg = await _pg_rituals_repo.list_all(user_notion_id=user_notion_id)
    except Exception as e:
        logger.warning("load_rituals pg query failed: %s", e)
        return []
    return [ritual_to_stub(r) for r in _pg]

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
    # Haiku: подпись-настроение это короткая фраза, Sonnet тут лишний (деньги).
    # Sonnet в Аркане — только трактовка/саммари сессий (#163).
    tip = await ask_claude(
        prompt, model="claude-haiku-4-5-20251001", max_tokens=60, temperature=0.7
    )
    tip = tip.strip().rstrip(".!").lower() if tip else ""
    if tip:
        _store_tip(tg_id, tip)
    return {"tip": tip}


_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _pct(count: int, total: int) -> int:
    return int(round(count / total * 100)) if total else 0


def _work_local_date(dt: Optional[Any], tz_offset: int) -> Optional[date]:
    if not dt:
        return None
    try:
        utc_dt = dt.astimezone(timezone.utc)
        local_dt = utc_dt + timedelta(hours=tz_offset)
        return local_dt.date()
    except Exception:
        return None


def _work_local_time(dt: Optional[Any], tz_offset: int) -> Optional[str]:
    if not dt:
        return None
    try:
        utc_dt = dt.astimezone(timezone.utc)
        local_dt = utc_dt + timedelta(hours=tz_offset)
        if local_dt.hour == 0 and local_dt.minute == 0:
            return None
        return f"{local_dt.hour:02d}:{local_dt.minute:02d}"
    except Exception:
        return None


async def _works_schedule(user_notion_id: str, today_date: date, tz_offset: int) -> tuple[list[dict], list[dict]]:
    """Возвращает (overdue, scheduled): просроченные + сегодняшние работы."""
    try:
        works_list = await _pg_works_repo.list_all(user_notion_id)
    except Exception as e:
        logger.warning("works_schedule pg query failed: %s", e)
        return [], []
    overdue: list[dict] = []
    scheduled: list[dict] = []
    seen: set[str] = set()
    for w in works_list:
        if w.id in seen:
            continue
        if w.status in ("done", "archived"):
            continue
        seen.add(w.id)
        deadline_date = _work_local_date(w.deadline_dt, tz_offset)
        reminder_date = _work_local_date(w.reminder_dt, tz_offset)
        if deadline_date and deadline_date < today_date:
            overdue.append({
                "id": w.id, "title": w.title, "cat": w.category, "prio": w.priority,
                "days_ago": (today_date - deadline_date).days,
            })
            continue
        if deadline_date == today_date or reminder_date == today_date:
            time_str = _work_local_time(w.reminder_dt, tz_offset) if reminder_date == today_date else None
            if not time_str:
                time_str = _work_local_time(w.deadline_dt, tz_offset)
            scheduled.append({
                "id": w.id, "title": w.title, "cat": w.category, "prio": w.priority,
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

    # Все сеансы юзера из PG → конвертируем в стабы для совместимости аналитических fn
    _pg_sessions = await _pg_sessions_repo.list_all(user_notion_id=user_notion_id)
    all_sessions = [triplet_to_stub(t) for t in _pg_sessions]

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

    # «Встречи» сегодня для подписи-настроения: считаем distinct КЛИЕНТСКИЕ
    # сессии (триплеты одной сессии = одна встреча), исключая self/личные
    # расклады — личное гадание это не встреча (#163).
    _meeting_keys: set = set()
    for _t in _pg_sessions:
        if to_local_date(_t.date or "", tz_offset) != today_date:
            continue
        _cid = _t.client_id
        if not _cid:
            continue  # личный расклад без клиента
        if ((clients_map.get(_cid) or {}).get("type_code") or "") == "self":
            continue  # self-client (сама Кай) — не встреча
        _sn = (_t.session_name or "").strip().lower()
        _meeting_keys.add((_sn, _cid) if _sn else (f"solo:{_t.id}", _cid))
    client_sessions_today = len(_meeting_keys)

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
    try:
        fin_records = await _pnl_repo.query_month(month, user_notion_id=user_notion_id)
    except Exception as e:
        logger.warning("arcana finance fetch failed: %s", e)
        fin_records = []
    income = 0.0
    supplies = 0.0
    for entry in fin_records:
        amt = entry.amount
        type_name = entry.type_
        cat = entry.category
        if "Доход" in type_name:
            income += amt
        elif "Расход" in type_name and cat in SUPPLIES_CATEGORIES:
            supplies += amt
    month_accuracy, _, sessions_in_month = _accuracy(all_sessions, month)

    # Аккуратность по сеансам + ритуалам (взвешенно за всё время)
    rituals = await _load_rituals(user_notion_id)
    acc = _compute_accuracy(all_sessions, rituals, scope="all")
    pending_sessions, pending_rituals = _count_pending(all_sessions, rituals)

    # «Работы N/M» — счётчик практики за сегодня:
    #   done = уникальные сеансы сегодня (session_name+client_id — один сеанс
    #          может иметь несколько триплетов/раскладов, считаем сеансы) +
    #          ритуалы сегодня с Результатом != ⏳ +
    #          работы из 🔮 Работы со статусом выполнено сегодня (db уже фильтрует)
    #   total = done + pending works (из _works_schedule, они != Done)
    _session_keys_today: set = set()
    for _t in _pg_sessions:
        if to_local_date(_t.date or "", tz_offset) != today_date:
            continue
        _sn = (_t.session_name or "").strip().lower()
        _cid = _t.client_id or ""
        _session_keys_today.add((_sn, _cid) if _sn else (f"solo:{_t.id}", _cid))
    unique_sessions_today = len(_session_keys_today)

    rituals_done_today = 0
    for p in rituals:
        raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
        d_local = to_local_date(raw, tz_offset)
        if d_local != today_date:
            continue
        result = select_of(p, "Результат") or ""
        if result and not result.startswith("⏳"):
            rituals_done_today += 1

    works_done_today = unique_sessions_today + rituals_done_today
    works_total_today = works_done_today + len(works) + len(works_overdue)

    return {
        "date": today_iso,
        "weekday": weekday,
        "tz_offset": tz_offset,
        "moon": moon,
        "sessions_today": sessions_today,
        "client_sessions_today": client_sessions_today,
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


_MONTH_RU = {
    "01": "Январь", "02": "Февраль", "03": "Март",     "04": "Апрель",
    "05": "Май",    "06": "Июнь",    "07": "Июль",    "08": "Август",
    "09": "Сентябрь","10": "Октябрь","11": "Ноябрь",  "12": "Декабрь",
}


def _month_key(p: dict) -> Optional[str]:
    raw = (p.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
    if not raw or len(raw) < 7:
        return None
    return raw[:7]


def _date_iso(p: dict) -> str:
    return ((p.get("properties", {}).get("Дата", {}).get("date") or {})
            .get("start", "") or "")[:10]


def _last_edited_iso(p: dict) -> str:
    return (p.get("last_edited_time") or "")[:10]


def _avg_check_delay(items: list[dict], verdict_fn) -> Optional[float]:
    """Среднее число дней между Дата (создание расклада) и last_edited (проверка)."""
    from datetime import date as _date
    deltas: list[int] = []
    for p in items:
        if verdict_fn(p) is None:
            continue
        d_iso = _date_iso(p)
        e_iso = _last_edited_iso(p)
        if not d_iso or not e_iso:
            continue
        try:
            d = _date.fromisoformat(d_iso)
            e = _date.fromisoformat(e_iso)
        except ValueError:
            continue
        delta = (e - d).days
        if delta >= 0:
            deltas.append(delta)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 1)


async def _client_types_map(user_notion_id: str) -> dict[str, str]:
    """Возвращает {pg_client_id: type_full}. Uses PG clients (post-migration)."""
    clients_map = await load_clients_map(user_notion_id)
    return {cid: info.get("type_full", "") for cid, info in clients_map.items()}


def _client_id_of(p: dict) -> Optional[str]:
    rel = p.get("properties", {}).get("👥 Клиенты", {}).get("relation") or []
    return rel[0].get("id") if rel else None


def _amount_paid(p: dict, sum_field: str, paid_field: str) -> tuple[float, float]:
    s = (p.get("properties", {}).get(sum_field, {}) or {}).get("number") or 0
    pd = (p.get("properties", {}).get(paid_field, {}) or {}).get("number") or 0
    return float(s or 0), float(pd or 0)


@router.get("/arcana/stats")
async def get_arcana_stats(
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Развёрнутая статистика практики для StatsSheet."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _pg_sess = await _pg_sessions_repo.list_all(user_notion_id=user_notion_id)
    sessions = [triplet_to_stub(t) for t in _pg_sess]
    rituals = await _load_rituals(user_notion_id)
    clients_map = await load_clients_map(user_notion_id)

    acc_overall = _compute_accuracy(sessions, rituals, "all")
    acc_sessions = _compute_accuracy(sessions, [], "sessions")
    acc_rituals = _compute_accuracy([], rituals, "rituals")

    pending_sessions, pending_rituals = _count_pending(sessions, rituals)
    pending = _pending_list(sessions, rituals, "all", clients_map)
    pending_sess = [x for x in pending if x["type"] == "session"][:10]
    pending_rit = [x for x in pending if x["type"] == "ritual"][:10]

    # Разрез по месяцам — последние 6
    by_month: dict[str, dict] = {}
    for p in sessions:
        m = _month_key(p)
        if not m:
            continue
        v = _session_verdict(p)
        bucket = by_month.setdefault(m, {
            "month": m, "label": _MONTH_RU.get(m[5:7], m),
            "sessions_total": 0, "sessions_yes": 0, "sessions_half": 0, "sessions_no": 0,
            "rituals_total": 0, "rituals_yes": 0, "rituals_half": 0, "rituals_no": 0,
        })
        bucket["sessions_total"] += 1
        if v == "yes":
            bucket["sessions_yes"] += 1
        elif v == "half":
            bucket["sessions_half"] += 1
        elif v == "no":
            bucket["sessions_no"] += 1
    for p in rituals:
        m = _month_key(p)
        if not m:
            continue
        v = _ritual_verdict(p)
        bucket = by_month.setdefault(m, {
            "month": m, "label": _MONTH_RU.get(m[5:7], m),
            "sessions_total": 0, "sessions_yes": 0, "sessions_half": 0, "sessions_no": 0,
            "rituals_total": 0, "rituals_yes": 0, "rituals_half": 0, "rituals_no": 0,
        })
        bucket["rituals_total"] += 1
        if v == "yes":
            bucket["rituals_yes"] += 1
        elif v == "half":
            bucket["rituals_half"] += 1
        elif v == "no":
            bucket["rituals_no"] += 1

    months_sorted = sorted(by_month.values(), key=lambda x: x["month"], reverse=True)[:6]
    for m in months_sorted:
        s_checked = m["sessions_yes"] + m["sessions_half"] + m["sessions_no"]
        m["sessions_checked"] = s_checked
        m["sessions_pct"] = (
            round((m["sessions_yes"] + 0.5 * m["sessions_half"]) / s_checked * 100, 1)
            if s_checked else 0
        )
        r_checked = m["rituals_yes"] + m["rituals_half"] + m["rituals_no"]
        m["rituals_checked"] = r_checked
        m["rituals_pct"] = (
            round((m["rituals_yes"] + 0.5 * m["rituals_half"]) / r_checked * 100, 1)
            if r_checked else 0
        )

    # Разрез по категориям (Тип расклада)
    by_cat: dict[str, dict] = {}
    for p in sessions:
        cats = multi_select_names(p, "Тип расклада")
        cat = cats[0] if cats else "—"
        bucket = by_cat.setdefault(cat, {"category": cat, "total": 0, "checked": 0,
                                          "yes": 0, "half": 0, "no": 0})
        bucket["total"] += 1
        v = _session_verdict(p)
        if v is not None:
            bucket["checked"] += 1
            bucket[v] += 1
    cats_list = []
    for c in by_cat.values():
        c["pct"] = (
            round((c["yes"] + 0.5 * c["half"]) / c["checked"] * 100, 1)
            if c["checked"] else 0
        )
        cats_list.append(c)
    cats_list.sort(key=lambda x: -x["total"])

    out: dict[str, Any] = {
        "total_sessions": len(sessions),
        "total_rituals": len(rituals),
        "total_triplets": len(sessions),
        "checked_triplets": acc_sessions["total"],
        "checked_rituals": acc_rituals["total"],
        "accuracy_pct_overall": acc_overall["pct"],
        "accuracy_pct_sessions": acc_sessions["pct"],
        "accuracy_pct_rituals": acc_rituals["pct"],
        "breakdown_overall": {"yes": acc_overall["yes"], "half": acc_overall["half"], "no": acc_overall["no"]},
        "breakdown_sessions": {"yes": acc_sessions["yes"], "half": acc_sessions["half"], "no": acc_sessions["no"]},
        "breakdown_rituals": {"yes": acc_rituals["yes"], "half": acc_rituals["half"], "no": acc_rituals["no"]},
        "pending_sessions_count": pending_sessions,
        "pending_rituals_count": pending_rituals,
        "pending_sessions": pending_sess,
        "pending_rituals": pending_rit,
        "by_month": months_sorted,
        "by_category": cats_list,
        "avg_check_delay_sessions_days": _avg_check_delay(sessions, _session_verdict),
        "avg_check_delay_rituals_days": _avg_check_delay(rituals, _ritual_verdict),
    }
    type_map = await _client_types_map(user_notion_id)
    out["by_client_type"] = _by_client_type(sessions, type_map)
    out["by_payment_source"] = _by_payment_source(sessions, rituals, type_map)
    out["barters_pending"] = _pending_barters(sessions, rituals, clients_map)
    return out


def _by_client_type(sessions: list[dict], type_map: dict[str, str]) -> dict:
    """Разрез сессий-триплетов по типу клиента."""
    out: dict[str, dict] = {
        "🌟 Self":       {"sessions": 0, "checked": 0, "yes": 0, "half": 0, "no": 0, "pct": 0},
        "🤝 Платный":    {"sessions": 0, "checked": 0, "yes": 0, "half": 0, "no": 0, "pct": 0},
        "🎁 Бесплатный": {"sessions": 0, "checked": 0, "yes": 0, "half": 0, "no": 0, "pct": 0},
    }
    for p in sessions:
        cid = _client_id_of(p)
        ctype = type_map.get(cid or "", "🤝 Платный")
        bucket = out.setdefault(ctype, {
            "sessions": 0, "checked": 0, "yes": 0, "half": 0, "no": 0, "pct": 0,
        })
        bucket["sessions"] += 1
        v = _session_verdict(p)
        if v is not None:
            bucket["checked"] += 1
            bucket[v] += 1
    for b in out.values():
        if b["checked"]:
            b["pct"] = round((b["yes"] + 0.5 * b["half"]) / b["checked"] * 100, 1)
    return out


def _by_payment_source(
    sessions: list[dict], rituals: list[dict],
    type_map: Optional[dict[str, str]] = None,
) -> dict:
    """Способы оплаты — учитывает ТОЛЬКО клиентские записи (🤝 Платный).
    Self и Бесплатных пропускаем — у них нет понятия источника оплаты."""
    out = {
        "💵 Наличные": {"sessions": 0, "rituals": 0, "total_rub": 0},
        "💳 Карта":    {"sessions": 0, "rituals": 0, "total_rub": 0},
        "🔄 Бартер":   {"sessions": 0, "rituals": 0, "items": []},
        "🎁 Подарок":  {"sessions": 0, "rituals": 0},
    }
    type_map = type_map or {}

    def _is_relevant_client(page: dict) -> bool:
        cid = _client_id_of(page)
        if not cid:
            return False  # без клиента (legacy) — не учитываем
        ctype = type_map.get(cid, "🤝 Платный")
        return ctype == "🤝 Платный"

    for p in sessions:
        if not _is_relevant_client(p):
            continue
        src = select_of(p, "Источник") or ""
        amt, paid = _amount_paid(p, "Сумма", "Оплачено")
        if src in ("💵 Наличные", "💳 Карта"):
            out[src]["sessions"] += 1
            out[src]["total_rub"] += int(paid)
        elif src == "🔄 Бартер":
            out["🔄 Бартер"]["sessions"] += 1
            what = rich_text_plain(p, "Бартер · что")
            if what:
                out["🔄 Бартер"]["items"].append(what)
        elif amt == 0 and paid == 0:
            out["🎁 Подарок"]["sessions"] += 1
    for p in rituals:
        if not _is_relevant_client(p):
            continue
        src = select_of(p, "Источник оплаты") or ""
        amt, paid = _amount_paid(p, "Цена за ритуал", "Оплачено")
        if src in ("💵 Наличные", "💳 Карта"):
            out[src]["rituals"] += 1
            out[src]["total_rub"] += int(paid)
        elif src == "🔄 Бартер":
            out["🔄 Бартер"]["rituals"] += 1
            what = rich_text_plain(p, "Бартер · что")
            if what:
                out["🔄 Бартер"]["items"].append(what)
        elif amt == 0 and paid == 0:
            out["🎁 Подарок"]["rituals"] += 1
    return out


def _pending_barters(sessions: list[dict], rituals: list[dict], clients_map: dict) -> list:
    """Записи с Источник=🔄 Бартер AND Оплачено=0."""
    out: list[dict] = []
    from miniapp.backend.routes._arcana_common import client_name_from
    for p in sessions:
        if (select_of(p, "Источник") or "") != "🔄 Бартер":
            continue
        _, paid = _amount_paid(p, "Сумма", "Оплачено")
        if paid > 0:
            continue
        what = rich_text_plain(p, "Бартер · что")
        cname, _cid = client_name_from(p, clients_map)
        date = ((p.get("properties", {}).get("Дата", {}).get("date") or {})
                .get("start", "") or "")[:10]
        out.append({
            "page_id": p.get("id", ""), "target": "sessions",
            "client": cname, "what": what, "since": date,
        })
    for p in rituals:
        if (select_of(p, "Источник оплаты") or "") != "🔄 Бартер":
            continue
        _, paid = _amount_paid(p, "Цена за ритуал", "Оплачено")
        if paid > 0:
            continue
        what = rich_text_plain(p, "Бартер · что")
        cname, _cid = client_name_from(p, clients_map)
        date = ((p.get("properties", {}).get("Дата", {}).get("date") or {})
                .get("start", "") or "")[:10]
        out.append({
            "page_id": p.get("id", ""), "target": "rituals",
            "client": cname, "what": what, "since": date,
        })
    return out


@router.get("/arcana/works")
async def get_arcana_works(
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Активные Работы юзера из PG: status != done/archived, сорт по deadline ASC nulls last."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    today_date, tz_offset = await today_user_tz(tg_id)
    try:
        works_list = await _pg_works_repo.list_all(user_notion_id)
    except Exception as e:
        logger.warning("works list fetch failed: %s", e)
        works_list = []
    clients_map = await load_clients_map(user_notion_id)
    open_works = [w for w in works_list if w.status not in ("done", "archived")]

    # batch-fetch subtasks from arcana_inventory (works_id = PG work id)
    subtasks_by_work: dict = {}
    if open_works:
        try:
            work_ids = [w.id for w in open_works]
            sub_items = await _arcana_inv_repo_lists.get_items_for_works(work_ids, user_notion_id)
            for s in sub_items:
                if s.works_id:
                    subtasks_by_work.setdefault(s.works_id, []).append({
                        "id": s.id,
                        "name": s.name,
                        "done": s.status == "done",
                    })
        except Exception as e:
            logger.warning("subtasks fetch failed: %s", e)

    items: list[dict] = []
    for w in open_works:
        deadline_date = _work_local_date(w.deadline_dt, tz_offset)
        is_overdue = bool(deadline_date and deadline_date < today_date)
        cli_id = w.client_id
        cli_info = clients_map.get(cli_id or "") or {}
        cli_name = cli_info.get("name", "")
        ctype_full = cli_info.get("type_full", "")
        ctype = ctype_full.split()[0] if ctype_full else ""
        items.append({
            "id": w.id,
            "title": w.title or "—",
            "status": w.status,
            "priority": w.priority,
            "category": w.category,
            "deadline": w.deadline_iso,
            "deadline_label": w.deadline_iso[:16].replace("T", " ") if w.deadline_iso else "",
            "is_overdue": is_overdue,
            "client": {"id": cli_id, "name": cli_name, "type": ctype} if cli_id else None,
            "subtasks": subtasks_by_work.get(w.id, []),
        })
    return {"works": items, "total": len(items)}


@router.get("/arcana/accuracy")
async def get_arcana_accuracy(
    tg_id: int = Depends(current_user_id),
    scope: str = Query("all"),
) -> dict[str, Any]:
    if scope not in ("all", "sessions", "rituals"):
        scope = "all"
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _pg_sess = await _pg_sessions_repo.list_all(user_notion_id=user_notion_id)
    sessions = [triplet_to_stub(t) for t in _pg_sess]
    rituals = await _load_rituals(user_notion_id)
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
        # PG-backed session verdict: map "yes"/"half"/"no" → outcome code
        _outcome_map = {"yes": "yes", "half": "partial", "no": "no"}
        ok = await _pg_sessions_repo.set_outcome(body.id, _outcome_map[body.verdict])
    elif body.type == "ritual":
        # PG-backed ritual verdict: "yes"/"half"/"no" → outcome code
        _ritual_outcome_map = {"yes": "positive", "half": "partial", "no": "negative"}
        ok = await _pg_rituals_repo.set_result(body.id, _ritual_outcome_map[body.verdict])
    else:
        raise HTTPException(status_code=400, detail="type must be session|ritual")
    if not ok:
        raise HTTPException(status_code=500, detail="failed to update")
    _kind = "Расклад" if body.type == "session" else "Ритуал"
    _verdict_word = {"yes": "сбылось ✅", "half": "частично 🌗", "no": "не сбылось ❌"}[body.verdict]
    await notify_user(tg_id, f"🔮 {_kind}: {_verdict_word}", bot="arcana")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _pg_sess = await _pg_sessions_repo.list_all(user_notion_id=user_notion_id)
    sessions = [triplet_to_stub(t) for t in _pg_sess]
    rituals = await _load_rituals(user_notion_id)
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
