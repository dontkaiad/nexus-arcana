"""core/repos/pg_sitter_lists_repo.py — что видит ситтер из списков Nexus (nexus_lists).

ЕДИНОЕ условие видимости `sitter_visible_clause` (его используют и чтение для ситтера, и поиск, и предпросмотр владельца):

    user_id = владелец AND status != 'archived' AND (
        (list_type = 'инвентарь' AND NOT sitter_hidden)                  # инвентарь открыт по умолчанию
        OR (list_type IN ('покупки', 'чеклист') AND for_sitter) )        # покупки и чеклисты только по пометке

for_sitter на инвентаре и sitter_hidden на покупках/чеклистах не учитываются. Новые пункты инвентаря видны сразу, без записи флагов.
Наружу минимум полей: без цен, магазина, ссылок на задачи/работы; id только в предпросмотре владельца.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import and_, not_, or_, select, text, update

from core.db import get_engine
from core.repos.lists_table import nexus_lists as t

MAX_NAME, MAX_NOTE, MAX_GROUP, MAX_LOC = 120, 300, 60, 200
INVENTORY = "инвентарь"
FLAGGED_TYPES = ("покупки", "чеклист")


def _clip(v, n: int) -> str:
    s = "".join(ch for ch in str(v or "") if ch == "\n" or ch >= " ")      # без управляющих символов
    return s.strip()[:n]


def sitter_visible_clause(user_id: str):
    """SQL-условие «этот пункт видит ситтер» (см. docstring модуля). Единственное место, где оно записано."""
    return and_(
        t.c.user_id == user_id,
        t.c.status != "archived",
        or_(
            and_(t.c.list_type == INVENTORY, not_(t.c.sitter_hidden)),
            and_(t.c.list_type.in_(FLAGGED_TYPES), t.c.for_sitter.is_(True)),
        ),
    )


def _qty(v):
    return float(v) if v is not None else None


def list_for_sitter(user_id: str, with_ids: bool = False) -> List[Dict]:
    """Помеченные покупки и чеклисты (без инвентаря), для ключа `lists`."""
    if not user_id:
        return []
    with get_engine().connect() as c:
        rows = c.execute(select(t.c.id, t.c.group_name, t.c.name, t.c.note, t.c.quantity, t.c.status, t.c.list_type)
                         .where(sitter_visible_clause(user_id), t.c.list_type != INVENTORY)
                         .order_by(t.c.group_name, t.c.id)).all()
    out = [{"group": _clip(r.group_name, MAX_GROUP), "name": _clip(r.name, MAX_NAME), "note": _clip(r.note, MAX_NOTE),
            "quantity": _qty(r.quantity), "done": r.status == "done", "kind": r.list_type} for r in rows]
    if with_ids:
        for o, r in zip(out, rows):
            o["id"] = int(r.id)
    return out


def list_inventory(user_id: str, with_ids: bool = False) -> List[Dict]:
    """Видимый инвентарь, по категориям, для ключа `inventory`."""
    if not user_id:
        return []
    with get_engine().connect() as c:
        rows = c.execute(select(t.c.id, t.c.category, t.c.name, t.c.note, t.c.quantity, t.c.location)
                         .where(sitter_visible_clause(user_id), t.c.list_type == INVENTORY)
                         .order_by(t.c.category, t.c.id)).all()
    out = [{"category": _clip(r.category, MAX_GROUP), "name": _clip(r.name, MAX_NAME), "note": _clip(r.note, MAX_NOTE),
            "quantity": _qty(r.quantity), "location": _clip(r.location, MAX_LOC)} for r in rows]
    if with_ids:
        for o, r in zip(out, rows):
            o["id"] = int(r.id)
    return out


def set_flag(user_id: str, item_id: int, value: bool) -> bool:
    """False — пункта нет или он чужой."""
    with get_engine().begin() as c:
        r = c.execute(update(t).where(t.c.id == int(item_id), t.c.user_id == user_id)
                      .values(for_sitter=bool(value), updated_at=text("now()")))
    return bool(r.rowcount)


def set_hidden(user_id: str, item_id: int, value: bool) -> bool:
    """Скрыть / открыть пункт ИНВЕНТАРЯ. False — пункта нет, он чужой или не из инвентаря."""
    with get_engine().begin() as c:
        r = c.execute(update(t).where(t.c.id == int(item_id), t.c.user_id == user_id, t.c.list_type == INVENTORY)
                      .values(sitter_hidden=bool(value), updated_at=text("now()")))
    return bool(r.rowcount)


def set_group_flag(user_id: str, group: str, value: bool) -> int:
    """Пометить / снять весь чеклист (группу) владельца. Возвращает число затронутых пунктов (0 — группы нет)."""
    with get_engine().begin() as c:
        r = c.execute(update(t).where(t.c.user_id == user_id, t.c.group_name == group, t.c.list_type == "чеклист",
                                      t.c.status != "archived")
                      .values(for_sitter=bool(value), updated_at=text("now()")))
    return int(r.rowcount or 0)


def group_by(items: List[Dict], key: str, label: str) -> List[Dict[str, Any]]:
    """[{label: значение, "items": [...]}] с сохранением порядка; поле key выносится из пунктов."""
    out: List[Dict[str, Any]] = []
    for i in items:
        if not out or out[-1][label] != i[key]:
            out.append({label: i[key], "items": []})
        out[-1]["items"].append({k: v for k, v in i.items() if k != key})
    return out
