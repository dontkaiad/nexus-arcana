"""arcana/repos/works_repo.py — domain repository for 🔮 Работы.

Notion-specific structures (page dicts, prop helpers) are fully contained here.
Callers receive plain Work dataclass instances and stable IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from core import notion_client as _notion


@dataclass
class Work:
    id: str          # stable identifier (Notion page_id now; Postgres pk later)
    title: str
    priority: str    # "Срочно" | "Важно" | "Можно потом"
    deadline_str: str   # display-ready, e.g. " · 📅 15.06 18:00", or ""
    category_str: str   # display-ready, e.g. " · 🃏 Расклад", or ""
    has_client: bool


def _parse_work(page: dict) -> Work:
    props = page["properties"]

    title = _notion._extract_text(props.get("Работа", {}))

    priority_sel = (props.get("Приоритет") or {}).get("select") or {}
    priority = priority_sel.get("name") or "Можно потом"
    if priority not in ("Срочно", "Важно", "Можно потом"):
        priority = "Можно потом"

    deadline_val = (props.get("Дедлайн") or {}).get("date") or {}
    deadline_str = ""
    start = (deadline_val.get("start") or "")[:16]
    if start:
        deadline_str = f" · 📅 {start[8:10]}.{start[5:7]}"
        if len(start) > 10:
            deadline_str += f" {start[11:16]}"

    cat_sel = (props.get("Категория") or {}).get("select") or {}
    cat_name = cat_sel.get("name") or ""
    category_str = f" · {cat_name}" if cat_name else ""

    has_client = bool((props.get("👥 Клиенты") or {}).get("relation", []))

    return Work(
        id=page["id"],
        title=title,
        priority=priority,
        deadline_str=deadline_str,
        category_str=category_str,
        has_client=has_client,
    )


class WorksRepo:
    async def list_open(self, user_id: str = "") -> List[Work]:
        pages = await _notion.works_list(user_notion_id=user_id)
        return [_parse_work(p) for p in pages]

    async def mark_done(self, work_id: str) -> bool:
        return await _notion.work_done(work_id)
