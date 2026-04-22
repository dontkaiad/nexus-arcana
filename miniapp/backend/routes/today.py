"""miniapp/backend/routes/today.py — GET /api/today."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends

from core.config import config
from core.notion_client import memory_get, query_pages
from core.claude_client import ask_claude
from core.user_manager import get_user_notion_id

from miniapp.backend import cache
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    cat_from_notion,
    extract_time,
    first_emoji,  # re-exported для back-compat тестов
    prio_from_notion,
    rich_text,
    select_name,
    title_text,
    to_local_date,
    today_user_tz,
)

logger = logging.getLogger("miniapp.today")

router = APIRouter()

_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_DEFAULT_BUDGET_DAY = 4166


def _date_start(prop: dict) -> str:
    d = prop.get("date") or {}
    return d.get("start") or ""


def _parse_iso(s: str):
    from datetime import datetime, timezone
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _task_summary(page: dict, tz_offset: int) -> dict:
    props = page.get("properties", {})
    return {
        "id": page.get("id", ""),
        "title": title_text(props.get("Задача", {})),
        "cat": first_emoji(select_name(props.get("Категория", {}))),
        "prio": first_emoji(select_name(props.get("Приоритет", {}))),
        "deadline_raw": _date_start(props.get("Дедлайн", {})),
        "reminder_raw": _date_start(props.get("Напоминание", {})),
        "repeat_time": rich_text(props.get("Время повтора", {})).strip(),
        "repeat": select_name(props.get("Повтор", {})) or None,
    }


async def _fetch_nexus_tasks(user_notion_id: str) -> list[dict]:
    # База задач — только для Nexus (Arcana-работы в отдельной базе 🔮),
    # поэтому фильтр по "Бот" не нужен и вызывает 400 от Notion.
    filters = {
        "and": [
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Complete"}},
        ]
    }
    if user_notion_id:
        filters["and"].append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    return await query_pages(
        config.nexus.db_tasks,
        filters=filters,
        sorts=[{"property": "Дедлайн", "direction": "ascending"}],
        page_size=100,
    )


async def _spent_today(user_notion_id: str, today_iso: str, tomorrow_iso: str) -> int:
    filters = {
        "and": [
            {"property": "Бот", "select": {"equals": BOT_NEXUS}},
            {"property": "Тип", "select": {"equals": "💸 Расход"}},
            {"property": "Дата", "date": {"on_or_after": today_iso}},
            {"property": "Дата", "date": {"before": tomorrow_iso}},
        ]
    }
    if user_notion_id:
        filters["and"].append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    pages = await query_pages(config.nexus.db_finance, filters=filters, page_size=100)
    total = 0.0
    for p in pages:
        amt = (p.get("properties", {}).get("Сумма", {}).get("number")) or 0
        total += amt
    return int(round(total))


async def _budget_day_limit() -> int:
    raw = await memory_get("budget_day_limit")
    if raw:
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            pass
    return _DEFAULT_BUDGET_DAY


async def _adhd_context_memories(user_notion_id: str) -> list[str]:
    db_id = config.nexus.db_memory
    if not db_id:
        return []
    filters = {"property": "Категория", "select": {"equals": "🧠 СДВГ"}}
    try:
        pages = await query_pages(db_id, filters=filters, page_size=3)
    except Exception as e:
        logger.warning("adhd memories fetch failed: %s", e)
        return []
    out = []
    for p in pages:
        props = p.get("properties", {})
        text = title_text(props.get("Текст", {}))
        if text:
            out.append(text)
    return out


async def _generate_adhd_tip(tg_id: int, today_str: str,
                             active_titles: list[str], user_notion_id: str) -> str:
    cached = cache.get_tip(tg_id, today_str)
    if cached:
        return cached

    memories = await _adhd_context_memories(user_notion_id)
    tasks_ctx = "\n".join(f"- {t}" for t in active_titles[:10]) or "нет активных задач"
    mem_ctx = "\n".join(f"- {m}" for m in memories) or "нет"

    prompt = (
        f"Сегодня {today_str}.\n\n"
        f"Активные задачи:\n{tasks_ctx}\n\n"
        f"Что знаю про её СДВГ:\n{mem_ctx}"
    )
    system = (
        "Ты — внешний мозг Кай. Сгенерируй ОДНО предложение (максимум 15 слов) — "
        "СДВГ-friendly совет на сегодня. Женский род. Конкретный, практичный, без воды. "
        "Можешь выделить **ключевое действие** в markdown-жирный (двойные звёздочки)."
    )
    try:
        text = await ask_claude(prompt=prompt, system=system, max_tokens=200)
    except Exception as e:
        logger.error("Haiku tip generation failed: %s", e)
        text = ""
    text = (text or "").strip()
    if text:
        cache.set_tip(tg_id, today_str, text)
    return text


@router.get("/today")
async def get_today(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    from nexus.handlers.streaks import get_streak, is_rest_day_available

    today_date, tz_offset = await today_user_tz(tg_id)
    today_str = today_date.isoformat()
    tomorrow_str = (today_date + timedelta(days=1)).isoformat()
    weekday = _WEEKDAYS_RU[today_date.weekday()]

    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    tasks_raw = await _fetch_nexus_tasks(user_notion_id)
    summaries = [_task_summary(p, tz_offset) for p in tasks_raw]

    overdue: list[dict] = []
    scheduled: list[dict] = []
    today_no_time: list[dict] = []
    no_date: list[dict] = []  # wave7.3: без дедлайна и без напоминалки
    future: list[dict] = []

    for s in summaries:
        deadline_date = to_local_date(s["deadline_raw"], tz_offset)
        deadline_time = extract_time(s["deadline_raw"], tz_offset)
        reminder_date = to_local_date(s["reminder_raw"], tz_offset)
        repeat_time = s["repeat_time"] or None

        if deadline_date and deadline_date < today_date:
            overdue.append({
                "id": s["id"],
                "title": s["title"],
                "cat": s["cat"],
                "prio": s["prio"],
                "days_ago": (today_date - deadline_date).days,
            })
            continue

        is_today = deadline_date == today_date or reminder_date == today_date
        has_time_today = (deadline_date == today_date) and deadline_time is not None

        if repeat_time or has_time_today:
            t = repeat_time if repeat_time else deadline_time
            reminder_min: Optional[int] = None
            if has_time_today and s["reminder_raw"]:
                dl_dt = _parse_iso(s["deadline_raw"])
                rm_dt = _parse_iso(s["reminder_raw"])
                if dl_dt and rm_dt:
                    delta = (dl_dt - rm_dt).total_seconds() / 60
                    if delta > 0:
                        reminder_min = int(round(delta))
            scheduled.append({
                "id": s["id"],
                "title": s["title"],
                "cat": s["cat"],
                "prio": s["prio"],
                "time": t,
                "reminder_min": reminder_min,
                "streak": 0,
                "repeat": s["repeat"],
            })
            continue

        if is_today:
            today_no_time.append({
                "id": s["id"],
                "title": s["title"],
                "cat": s["cat"],
                "prio": s["prio"],
                "date": today_str,
                "repeat": s["repeat"],
            })
        elif deadline_date and deadline_date > today_date:
            future.append({
                "id": s["id"],
                "title": s["title"],
                "cat": s["cat"],
                "prio": s["prio"],
                "date": deadline_date.isoformat(),
                "repeat": s["repeat"],
            })
        elif not deadline_date and not reminder_date:
            no_date.append({
                "id": s["id"],
                "title": s["title"],
                "cat": s["cat"],
                "prio": s["prio"],
                "repeat": s["repeat"],
            })

    future.sort(key=lambda x: x["date"])
    # wave7.3: на главном экране — только «Сегодня», без будущих
    tasks_out = today_no_time

    scheduled.sort(key=lambda x: x["time"] or "")
    overdue.sort(key=lambda x: -x["days_ago"])

    spent_today = await _spent_today(user_notion_id, today_str, tomorrow_str)
    day_limit = await _budget_day_limit()
    left = day_limit - spent_today
    pct = int(round(spent_today / day_limit * 100)) if day_limit else 0

    streak_data = get_streak(tg_id)
    rest_available = is_rest_day_available(tg_id)

    active_titles = [s["title"] for s in summaries if s["title"]]
    tip = await _generate_adhd_tip(tg_id, today_str, active_titles, user_notion_id)

    return {
        "date": today_str,
        "weekday": weekday,
        "tz_offset": tz_offset,
        "streak": {
            "current": streak_data.get("streak", 0),
            "rest_day_available": rest_available,
        },
        "budget": {
            "day": day_limit,
            "spent_today": spent_today,
            "left": left,
            "pct": pct,
        },
        "overdue": overdue,
        "scheduled": scheduled,
        "tasks": tasks_out,
        "no_date": no_date,
        "adhd_tip": tip,
    }
