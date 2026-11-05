"""core/repos/pg_sitter_lists_repo.py — пункты списков Nexus, помеченные «для ситтера» (nexus_lists.for_sitter).

Узкая выборка: только помеченные, только владельца, без архивных; наружу — минимум полей (без id, цен, магазина,
ссылок на задачи/работы). Пометку ставит владелец (set_flag).
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select, update, text

from core.db import get_engine
from core.repos.lists_table import nexus_lists as t

MAX_NAME, MAX_NOTE, MAX_GROUP = 120, 300, 60


def _clip(v, n: int) -> str:
    s = "".join(ch for ch in str(v or "") if ch == "\n" or ch >= " ")      # без управляющих символов
    return s.strip()[:n]


def list_for_sitter(user_id: str) -> List[Dict]:
    if not user_id:
        return []
    with get_engine().connect() as c:
        rows = c.execute(select(t.c.group_name, t.c.name, t.c.note, t.c.quantity, t.c.status, t.c.list_type)
                         .where(t.c.for_sitter.is_(True), t.c.user_id == user_id, t.c.status != "archived")
                         .order_by(t.c.group_name, t.c.id)).all()
    return [{"group": _clip(r.group_name, MAX_GROUP), "name": _clip(r.name, MAX_NAME), "note": _clip(r.note, MAX_NOTE),
             "quantity": float(r.quantity) if r.quantity is not None else None, "done": r.status == "done",
             "kind": r.list_type} for r in rows]


def set_flag(user_id: str, item_id: int, value: bool) -> bool:
    """False — пункта нет или он чужой."""
    with get_engine().begin() as c:
        r = c.execute(update(t).where(t.c.id == int(item_id), t.c.user_id == user_id)
                      .values(for_sitter=bool(value), updated_at=text("now()")))
    return bool(r.rowcount)


def set_group_flag(user_id: str, group: str, value: bool) -> int:
    """Пометить / снять весь чеклист (группу) владельца. Возвращает число затронутых пунктов (0 — группы нет)."""
    with get_engine().begin() as c:
        r = c.execute(update(t).where(t.c.user_id == user_id, t.c.group_name == group, t.c.list_type == "чеклист",
                                      t.c.status != "archived")
                      .values(for_sitter=bool(value), updated_at=text("now()")))
    return int(r.rowcount or 0)
