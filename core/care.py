"""core/care.py — уход за котами через ситтера: период ухода, расписание для ситтера, глушение напоминаний владельцу.

Хозяин расписания — Nexus: задачи с `tasks.for_sitter` (их срок = `tasks.reminder`, цикл повторения считает Nexus).
Период ухода (`care_periods`, одна строка на владельца) — когда ситтер активен: только в это время расписание отдаёт задачи ситтеру,
а напоминания таких задач владельцу молчат (вместо них — одно предупреждение, если цикл просрочен больше GRACE).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select, text

from core.db import get_engine
from core.recurrence import interval_label, parse_repeat_time
from nexus.repos.tasks_tables import (
    care_alerts, care_periods, task_completions, task_overrides, task_repeat, task_status, tasks,
)

logger = logging.getLogger("core.care")

OVERDUE_GRACE = timedelta(hours=3)
MAX_PERIOD_DAYS = 370
MAX_LABEL = 80
CADENCE = {"Ежедневно": "ежедневно", "Еженедельно": "еженедельно", "Ежемесячно": "ежемесячно"}


def local_today(tz_offset: int) -> date:
    return datetime.now(timezone(timedelta(hours=tz_offset))).date()


def get_period(user_id: str) -> Optional[Dict[str, Any]]:
    """{starts_on, ends_on, source}: source 'manual' (владелец, переопределение) | 'auto' (из дат доступа ситтеров)."""
    if not user_id:
        return None
    with get_engine().connect() as c:
        r = c.execute(select(care_periods.c.starts_on, care_periods.c.ends_on, care_periods.c.source)
                      .where(care_periods.c.user_id == user_id)).first()
    return {"starts_on": r.starts_on, "ends_on": r.ends_on, "source": r.source} if r else None


def _check_dates(starts_on: date, ends_on: date) -> None:
    if ends_on < starts_on:
        raise ValueError("ends_on before starts_on")
    if (ends_on - starts_on).days > MAX_PERIOD_DAYS:
        raise ValueError("period too long")


def set_period(user_id: str, starts_on: date, ends_on: date) -> None:
    """Период задаёт ВЛАДЕЛЕЦ (/care_period, Mini App): source='manual' — переопределение, автосинхронизация его не трогает."""
    _check_dates(starts_on, ends_on)
    with get_engine().begin() as c:
        c.execute(text(
            "INSERT INTO care_periods (user_id, starts_on, ends_on, source) VALUES (:u, :s, :e, 'manual') "
            "ON CONFLICT (user_id) DO UPDATE SET starts_on = EXCLUDED.starts_on, ends_on = EXCLUDED.ends_on, source = 'manual'"),
            {"u": user_id, "s": starts_on, "e": ends_on})


def apply_auto_period(user_id: str, starts_on: Optional[date], ends_on: Optional[date]) -> Dict[str, Any]:
    """Период из дат доступа ситтеров (присылает cats отдельным токеном). Даты None/None — «периода нет».
    Ручной период (source='manual') НЕ перезаписывается и не снимается: {applied: False, reason: 'manual_override'}.
    → {applied, changed, period | None}. Идемпотентно; гонка с ручной установкой всегда заканчивается ручным значением."""
    if (starts_on is None) != (ends_on is None):
        raise ValueError("both dates or none")
    if starts_on is not None:
        _check_dates(starts_on, ends_on)
    with get_engine().begin() as c:
        cur = c.execute(select(care_periods.c.starts_on, care_periods.c.ends_on, care_periods.c.source)
                        .where(care_periods.c.user_id == user_id).with_for_update()).first()
        if cur and cur.source == "manual":
            return {"applied": False, "reason": "manual_override", "changed": False,
                    "period": {"starts_on": cur.starts_on, "ends_on": cur.ends_on, "source": "manual"}}
        if starts_on is None:
            if cur:
                c.execute(care_periods.delete().where(care_periods.c.user_id == user_id, care_periods.c.source == "auto"))
            return {"applied": True, "changed": bool(cur), "period": None}
        c.execute(text(
            "INSERT INTO care_periods (user_id, starts_on, ends_on, source) VALUES (:u, :s, :e, 'auto') "
            "ON CONFLICT (user_id) DO UPDATE SET starts_on = EXCLUDED.starts_on, ends_on = EXCLUDED.ends_on "
            "WHERE care_periods.source <> 'manual'"), {"u": user_id, "s": starts_on, "e": ends_on})
        now = c.execute(select(care_periods.c.starts_on, care_periods.c.ends_on, care_periods.c.source)
                        .where(care_periods.c.user_id == user_id)).first()
    if now.source == "manual":                                       # владелец успел поставить свой период между чтением и записью
        return {"applied": False, "reason": "manual_override", "changed": False,
                "period": {"starts_on": now.starts_on, "ends_on": now.ends_on, "source": "manual"}}
    changed = not cur or (cur.starts_on, cur.ends_on) != (starts_on, ends_on)
    return {"applied": True, "changed": changed, "period": {"starts_on": starts_on, "ends_on": ends_on, "source": "auto"}}


def clear_period(user_id: str) -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(care_periods.delete().where(care_periods.c.user_id == user_id)).rowcount)


def is_active(user_id: str, tz_offset: int, on: Optional[date] = None) -> bool:
    p = get_period(user_id)
    d = on or local_today(tz_offset)
    return bool(p and p["starts_on"] <= d <= p["ends_on"])


def is_muted(task: Any, tz_offset: int) -> bool:
    """Напоминания задачи владельцу молчат: задача в расписании ситтера и идёт период ухода."""
    try:
        return bool(getattr(task, "for_sitter", False)) and is_active(getattr(task, "user_id", ""), tz_offset)
    except Exception:
        logger.warning("care: mute check failed, not muting", exc_info=True)
        return False                         # при сбое лучше лишнее напоминание, чем молчание


def set_task_flag(user_id: str, task_id: int, value: bool) -> bool:
    """Снятие for_sitter очищает и care_label: метка живёт только у задач в расписании ситтера."""
    vals: Dict[str, Any] = {"for_sitter": bool(value), "updated_at": text("now()")}
    if not value:
        vals["care_label"] = None
    with get_engine().begin() as c:
        r = c.execute(tasks.update().where(tasks.c.id == int(task_id), tasks.c.user_id == user_id).values(**vals))
    return bool(r.rowcount)


MAX_CARE_LABEL = 30


def set_care_label(user_id: str, task_id: int, label: Optional[str]) -> bool:
    """Метка для блока «последний раз» («лотки»). Только у СВОЕЙ задачи с for_sitter; None / пусто — снять. ValueError — длиннее 30.
    False — задачи нет, она чужая или не в расписании ситтера."""
    clean = _clip(label, 10 ** 6) if label is not None else ""
    if len(clean) > MAX_CARE_LABEL:
        raise ValueError("care label too long")
    with get_engine().begin() as c:
        r = c.execute(tasks.update().where(tasks.c.id == int(task_id), tasks.c.user_id == user_id, tasks.c.for_sitter.is_(True))
                      .values(care_label=clean or None, updated_at=text("now()")))
    return bool(r.rowcount)


def care_summary(user_id: str) -> List[Dict[str, Any]]:
    """Блок «последний раз»: [{label, last_done_at, last_done_by}] по задачам владельца с for_sitter и непустой care_label
    (не в архиве), по порядку создания. last_done_at — max(task_completions.done_at) в UTC; last_done_by — «sitter», если последнее
    выполнение пришло от ситтера (source='sitter'), иначе «owner»; нет выполнений — оба null. Без id, названий, заметок и user_id."""
    if not user_id:
        return []
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT t.care_label AS label, l.done_at, l.source "
            "FROM tasks t JOIN task_status s ON s.id = t.status_id "
            "LEFT JOIN LATERAL (SELECT done_at, source FROM task_completions WHERE task_id = t.id ORDER BY done_at DESC, id DESC LIMIT 1) l ON true "
            "WHERE t.user_id = :u AND t.for_sitter AND t.care_label IS NOT NULL AND btrim(t.care_label) <> '' AND s.code <> 'Archived' "
            "ORDER BY t.id"), {"u": user_id}).all()
    return [{"label": _clip(r.label, MAX_CARE_LABEL), "last_done_at": _iso(r.done_at),
             "last_done_by": None if r.done_at is None else ("sitter" if r.source == "sitter" else "owner")} for r in rows]


def _clip(v: Any, n: int) -> str:
    return "".join(ch for ch in str(v or "") if ch >= " " and ch != "\x7f").strip()[:n]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _cadence(repeat: str, repeat_time: str) -> str:
    _, ivl = parse_repeat_time(repeat_time or "")
    if ivl > 1:
        return interval_label(ivl)
    return CADENCE.get(repeat or "", "")


def schedule(user_id: str, tz_offset: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Расписание для ситтера. Вне периода ухода задач нет (`active: false`)."""
    now = now or datetime.now(timezone.utc)
    p = get_period(user_id) if user_id else None
    today = now.astimezone(timezone(timedelta(hours=tz_offset))).date()
    active = bool(p and p["starts_on"] <= today <= p["ends_on"])
    out: Dict[str, Any] = {
        "active": active,
        "period": {"starts_on": p["starts_on"].isoformat(), "ends_on": p["ends_on"].isoformat()} if p else None,
        "now": _iso(now), "tasks": [],
    }
    if not active:
        return out
    due = func.coalesce(task_overrides.c.new_due, tasks.c.reminder)
    with get_engine().connect() as c:
        rows = c.execute(
            select(tasks.c.id, tasks.c.title, tasks.c.reminder, tasks.c.repeat_time, task_repeat.c.code.label("repeat"),
                   due.label("due_at"), task_overrides.c.new_due.label("override"))
            .select_from(tasks.join(task_status, task_status.c.id == tasks.c.status_id)
                         .outerjoin(task_repeat, task_repeat.c.id == tasks.c.repeat_id)
                         .outerjoin(task_overrides, and_(task_overrides.c.task_id == tasks.c.id,
                                                         task_overrides.c.cycle_due == tasks.c.reminder)))
            .where(tasks.c.user_id == user_id, tasks.c.for_sitter.is_(True), tasks.c.reminder.isnot(None),
                   task_status.c.code.notin_(["Done", "Archived"]))
            .order_by(due, tasks.c.id)).all()
        last = {r.task_id: r.last for r in c.execute(
            select(task_completions.c.task_id, text("max(done_at) AS last")).where(
                task_completions.c.task_id.in_([r.id for r in rows] or [0])).group_by(task_completions.c.task_id))}
    for r in rows:
        out["tasks"].append({
            "id": str(r.id), "label": _clip(r.title, MAX_LABEL), "cycle_due": _iso(r.reminder),
            "due_at": _iso(r.due_at), "postponed": r.override is not None,
            "overdue": r.due_at < now, "last_done_at": _iso(last.get(r.id)),
            "cadence": _cadence(r.repeat or "", r.repeat_time or ""),
        })
    return out


def overdue_to_alert(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Циклы, просроченные больше OVERDUE_GRACE в активный период, о которых владельца ещё не предупреждали."""
    now = now or datetime.now(timezone.utc)
    due = func.coalesce(task_overrides.c.new_due, tasks.c.reminder)
    with get_engine().connect() as c:
        rows = c.execute(
            select(tasks.c.id, tasks.c.title, tasks.c.user_id, tasks.c.reminder, due.label("due_at"))
            .select_from(tasks.join(task_status, task_status.c.id == tasks.c.status_id)
                         .outerjoin(task_overrides, and_(task_overrides.c.task_id == tasks.c.id,
                                                         task_overrides.c.cycle_due == tasks.c.reminder)))
            .where(tasks.c.for_sitter.is_(True), tasks.c.reminder.isnot(None), due < now - OVERDUE_GRACE,
                   task_status.c.code.notin_(["Done", "Archived"]))).all()
        sent = {(r.task_id, r.cycle_due) for r in c.execute(select(care_alerts.c.task_id, care_alerts.c.cycle_due))}
    out = []
    for r in rows:
        if (r.id, r.reminder) in sent:
            continue
        p = get_period(r.user_id)
        # период смотрим на момент срока цикла: просрочка внутри периода
        if p and p["starts_on"] <= r.reminder.date() <= p["ends_on"] + timedelta(days=1):
            out.append({"task_id": r.id, "title": _clip(r.title, MAX_LABEL), "user_id": r.user_id, "cycle_due": r.reminder})
    return out


def mark_alerted(task_id: int, cycle_due: datetime) -> bool:
    """True — запись создана впервые (предупреждать нужно); False — уже было (гонка двух проходов)."""
    with get_engine().begin() as c:
        r = c.execute(text("INSERT INTO care_alerts (task_id, cycle_due) VALUES (:t, :d) ON CONFLICT DO NOTHING"),
                      {"t": task_id, "d": cycle_due})
    return bool(r.rowcount)


async def send_overdue_alerts(now: Optional[datetime] = None) -> int:
    """Раз в проход: по одному предупреждению владельцу на каждый просроченный цикл. Возвращает число отправленных."""
    import asyncio
    from core.bot_notify import notify_user
    from core.config import config
    from core.location import get_user_tz
    from core.user_manager import get_user_id

    todo = await asyncio.to_thread(overdue_to_alert, now)
    sent = 0
    for item in todo:
        if not await asyncio.to_thread(mark_alerted, item["task_id"], item["cycle_due"]):
            continue
        for tg in config.allowed_ids:
            try:
                if (await get_user_id(tg)) != item["user_id"]:
                    continue
                tz = await get_user_tz(tg)
                when = item["cycle_due"].astimezone(timezone(timedelta(hours=tz))).strftime("%d.%m %H:%M")
                await notify_user(tg, f"⚠️ Не сделано вовремя: {item['title']} (срок {when})", bot="nexus")
                sent += 1
            except Exception:
                logger.warning("care: overdue alert failed", exc_info=True)
    return sent


# ── запись ситтера: выполнить цикл, разовый перенос ─────────────────────────────

def cycle_done_exists(task_id: int, cycle_due: datetime) -> bool:
    with get_engine().connect() as c:
        return c.execute(select(task_completions.c.id).where(task_completions.c.task_id == int(task_id),
                                                             task_completions.c.cycle_due == cycle_due)).first() is not None


def record_cycle_done(task_id: int, done_by: str, cycle_due: datetime) -> bool:
    """Выполнение цикла. True — запись создана; False — этот цикл уже закрыт (повторное нажатие / гонка): уникальный индекс
    (task_id, cycle_due) и ON CONFLICT DO NOTHING делают проверку и вставку одной атомарной операцией."""
    with get_engine().begin() as c:
        r = c.execute(text(
            "INSERT INTO task_completions (task_id, done_by, source, cycle_due) VALUES (:t, :b, 'sitter', :d) "
            "ON CONFLICT (task_id, cycle_due) WHERE cycle_due IS NOT NULL DO NOTHING"),
            {"t": int(task_id), "b": _clip(done_by, 40) or None, "d": cycle_due})
    return bool(r.rowcount)


def add_override(task_id: int, cycle_due: datetime, new_due: datetime, created_by: str) -> bool:
    """Разовый перенос цикла. False — на этот цикл перенос уже есть (один на цикл)."""
    with get_engine().begin() as c:
        r = c.execute(text(
            "INSERT INTO task_overrides (task_id, cycle_due, new_due, created_by) VALUES (:t, :c, :n, :b) ON CONFLICT DO NOTHING"),
            {"t": int(task_id), "c": cycle_due, "n": new_due, "b": _clip(created_by, 40) or None})
    return bool(r.rowcount)
