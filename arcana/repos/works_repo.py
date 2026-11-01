"""arcana/repos/works_repo.py — domain repository for 🔮 Работы.

Pure PG — no Notion calls. Callers receive plain Work dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Work:
    id: str
    title: str
    priority: str
    deadline_str: str
    category_str: str
    has_client: bool
    # extended fields (all defaulted — backward-compatible)
    status: str = "open"
    client_id: Optional[str] = None
    deadline_dt: Optional[Any] = None   # datetime with tz from PG
    reminder_dt: Optional[Any] = None   # datetime with tz from PG
    deadline_iso: str = ""
    category: str = ""
    # repeat (recurring works — ADR-0023). `repeat` holds the RU label
    # ("Нет"/"Ежедневно"/"Еженедельно"/"Ежемесячно"), like Task.repeat.
    repeat: str = "Нет"
    day_of_week: str = ""
    repeat_time: str = ""
    notes: str = ""


def _pg_repo():
    from arcana.repos.pg_works_repo import PgWorksRepo
    return PgWorksRepo()


class WorksRepo:
    async def list_open(self, user_id: str = "") -> List[Work]:
        return await _pg_repo().list_open(user_id=user_id)

    async def mark_done(self, work_id: str) -> bool:
        return await _pg_repo().mark_done(work_id)


def work_deadline_str(work: "Work", tz_offset: int) -> str:
    """Текст дедлайна для списков (' · 📅 dd.mm[ HH:MM]') в поясе ПОЛЬЗОВАТЕЛЯ.

    Как Nexus при показе списков: настоящий UTC из PG → локальное настенное время
    (core.date_extract.dt_to_local_wall = to_local_wall для datetime). Пусто, если
    дедлайна нет. 00:00 локально = «без времени» → показываем только дату.
    """
    from core.date_extract import dt_to_local_wall
    wall = dt_to_local_wall(work.deadline_dt, tz_offset) if work.deadline_dt else ""
    if not wall:
        return ""
    day = f"{wall[8:10]}.{wall[5:7]}"
    hhmm = wall[11:16]
    return f" · 📅 {day}" + (f" {hhmm}" if hhmm and hhmm != "00:00" else "")
