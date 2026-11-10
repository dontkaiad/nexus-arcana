"""core/repos/pg_cat_health_repo.py — здоровье котов: вес, визиты к ветеринару / прививки, курсы лекарств.

Sync SQLAlchemy Core, как pg_cats_repo (async-вызывающие оборачивают в asyncio.to_thread). Денег нет. Каждая операция привязана к владельцу:
кот должен принадлежать `user_id` и не быть «rejected», строки читаются / правятся только по `user_id`. Чужой или несуществующий кот —
NotFound (вызывающий отвечает 404). Напоминания живут в обычных задачах Nexus: здесь только хранится `task_id`, саму задачу ведёт
core.cat_health.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.db import get_engine
from core.repos.cats_tables import MED_STATUSES, VET_KINDS, VET_STATUSES

GRAMS_MIN, GRAMS_MAX = 100, 30000
EDITABLE_VISIT = ("title", "kind", "visit_on", "clinic", "note", "next_due", "status", "sitter_visible")
EDITABLE_MED = ("name", "dose", "note", "starts_on", "days", "times_per_day", "status", "sitter_hidden")


class NotFound(LookupError):
    """Кота нет у этого владельца (или он убран), либо строки нет / она чужая."""


def _row(r) -> Dict[str, Any]:
    return dict(r._mapping)


def _clean(v: Optional[str], limit: int) -> Optional[str]:
    v = " ".join((v or "").split())
    if len(v) > limit:
        raise ValueError("text too long")
    return v or None


def _owned_cat(conn, user_id: str, cat_id: int) -> None:
    if not conn.execute(text("SELECT 1 FROM cats WHERE id = :c AND user_id = :u AND status <> 'rejected'"), {"c": cat_id, "u": user_id}).first():
        raise NotFound("cat")


# ── вес ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def add_weight(user_id: str, cat_id: int, weighed_on: date, grams: int, note: Optional[str] = None) -> Dict[str, Any]:
    """Взвешивание. Повторная запись за тот же день заменяет граммы и заметку (флаг видимости сохраняется)."""
    if not isinstance(grams, int) or isinstance(grams, bool) or not GRAMS_MIN <= grams <= GRAMS_MAX:
        raise ValueError("grams out of range")
    note = _clean(note, 300)
    with get_engine().begin() as c:
        _owned_cat(c, user_id, cat_id)
        return _row(c.execute(text(
            "INSERT INTO cat_weights (cat_id, user_id, weighed_on, grams, note) VALUES (:c, :u, :d, :g, :n) "
            "ON CONFLICT (cat_id, weighed_on) DO UPDATE SET grams = EXCLUDED.grams, note = EXCLUDED.note, updated_at = now() RETURNING *"),
            {"c": cat_id, "u": user_id, "d": weighed_on, "g": grams, "n": note}).one())


def list_weights(user_id: str, cat_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    with get_engine().connect() as c:
        _owned_cat(c, user_id, cat_id)
        return [_row(r) for r in c.execute(text(
            "SELECT * FROM cat_weights WHERE cat_id = :c AND user_id = :u ORDER BY weighed_on DESC LIMIT :l"),
            {"c": cat_id, "u": user_id, "l": max(1, min(int(limit), 1000))})]


def set_weight_visible(user_id: str, weight_id: int, visible: bool) -> Optional[Dict[str, Any]]:
    with get_engine().begin() as c:
        r = c.execute(text("UPDATE cat_weights SET sitter_visible = :v, updated_at = now() WHERE id = :i AND user_id = :u RETURNING *"),
                      {"v": bool(visible), "i": weight_id, "u": user_id}).first()
    return _row(r) if r else None


def delete_weight(user_id: str, weight_id: int) -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(text("DELETE FROM cat_weights WHERE id = :i AND user_id = :u"), {"i": weight_id, "u": user_id}).rowcount)


# ── визиты / прививки ─────────────────────────────────────────────────────────────────────────────────────────────────

def _check_visit(title: Any, kind: str, status: str, visit_on: date, next_due: Optional[date]) -> str:
    title = _clean(title, 120)
    if not title:
        raise ValueError("title is empty")
    if kind not in VET_KINDS or status not in VET_STATUSES:
        raise ValueError("bad kind or status")
    if next_due is not None and next_due < visit_on:
        raise ValueError("next_due before visit_on")
    return title


def add_visit(user_id: str, cat_id: int, title: str, visit_on: date, *, kind: str = "visit", clinic: Optional[str] = None,
              note: Optional[str] = None, next_due: Optional[date] = None, status: str = "planned",
              sitter_visible: bool = False) -> Dict[str, Any]:
    title = _check_visit(title, kind, status, visit_on, next_due)
    clinic, note = _clean(clinic, 120), _clean(note, 500)
    with get_engine().begin() as c:
        _owned_cat(c, user_id, cat_id)
        return _row(c.execute(text(
            "INSERT INTO cat_vet_visits (cat_id, user_id, kind, title, visit_on, clinic, note, next_due, status, sitter_visible) "
            "VALUES (:c, :u, :k, :t, :d, :cl, :n, :nd, :s, :v) RETURNING *"),
            {"c": cat_id, "u": user_id, "k": kind, "t": title, "d": visit_on, "cl": clinic, "n": note, "nd": next_due,
             "s": status, "v": bool(sitter_visible)}).one())


def get_visit(user_id: str, visit_id: int) -> Optional[Dict[str, Any]]:
    with get_engine().connect() as c:
        r = c.execute(text("SELECT * FROM cat_vet_visits WHERE id = :i AND user_id = :u"), {"i": visit_id, "u": user_id}).first()
    return _row(r) if r else None


def list_visits(user_id: str, cat_id: int, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with get_engine().connect() as c:
        _owned_cat(c, user_id, cat_id)
        return [_row(r) for r in c.execute(text(
            "SELECT * FROM cat_vet_visits WHERE cat_id = :c AND user_id = :u AND (CAST(:s AS text) IS NULL OR status = :s) "
            "ORDER BY visit_on DESC, id DESC LIMIT :l"), {"c": cat_id, "u": user_id, "s": status, "l": max(1, min(int(limit), 500))})]


def update_visit(user_id: str, visit_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Правка по белому списку полей. None — строки нет. Проверка согласованности идёт по итоговой строке (дата следующего ≥ даты визита и т.д.)."""
    values = {k: v for k, v in (values or {}).items() if k in EDITABLE_VISIT}
    with get_engine().begin() as c:
        cur = c.execute(text("SELECT * FROM cat_vet_visits WHERE id = :i AND user_id = :u FOR UPDATE"), {"i": visit_id, "u": user_id}).first()
        if cur is None:
            return None
        merged = {**_row(cur), **values}
        merged["title"] = _check_visit(merged["title"], merged["kind"], merged["status"], merged["visit_on"], merged["next_due"])
        merged["clinic"], merged["note"] = _clean(merged["clinic"], 120), _clean(merged["note"], 500)
        merged["sitter_visible"] = bool(merged["sitter_visible"])
        r = c.execute(text(
            "UPDATE cat_vet_visits SET title=:title, kind=:kind, visit_on=:visit_on, clinic=:clinic, note=:note, next_due=:next_due, "
            "status=:status, sitter_visible=:sitter_visible, updated_at=now() WHERE id=:i AND user_id=:u RETURNING *"),
            {**{k: merged[k] for k in EDITABLE_VISIT}, "i": visit_id, "u": user_id}).one()
    return _row(r)


def set_visit_task(user_id: str, visit_id: int, task_id: Optional[int]) -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE cat_vet_visits SET task_id = :t, updated_at = now() WHERE id = :i AND user_id = :u"), {"t": task_id, "i": visit_id, "u": user_id})


def delete_visit(user_id: str, visit_id: int) -> Optional[Dict[str, Any]]:
    """Удаляет визит и возвращает удалённую строку (вызывающий снимает связанную задачу)."""
    with get_engine().begin() as c:
        r = c.execute(text("DELETE FROM cat_vet_visits WHERE id = :i AND user_id = :u RETURNING *"), {"i": visit_id, "u": user_id}).first()
    return _row(r) if r else None


# ── курсы лекарств ────────────────────────────────────────────────────────────────────────────────────────────────────

def _ends(starts_on: date, days: Optional[int]) -> Optional[date]:
    return starts_on + timedelta(days=days - 1) if days else None


def _check_med(name: Any, times: int, days: Optional[int], status: str) -> str:
    name = _clean(name, 80)
    if not name:
        raise ValueError("name is empty")
    if not isinstance(times, int) or isinstance(times, bool) or not 1 <= times <= 4:
        raise ValueError("times_per_day out of range")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365):
        raise ValueError("days out of range")
    if status not in MED_STATUSES:
        raise ValueError("bad status")
    return name


def add_med(user_id: str, cat_id: int, name: str, starts_on: date, *, days: Optional[int] = None, dose: Optional[str] = None,
            times_per_day: int = 1, note: Optional[str] = None, sitter_hidden: bool = False) -> Dict[str, Any]:
    name = _check_med(name, times_per_day, days, "active")
    dose, note = _clean(dose, 80), _clean(note, 500)
    with get_engine().begin() as c:
        _owned_cat(c, user_id, cat_id)
        return _row(c.execute(text(
            "INSERT INTO cat_medications (cat_id, user_id, name, dose, starts_on, days, ends_on, times_per_day, note, sitter_hidden) "
            "VALUES (:c, :u, :n, :d, :s, :days, :e, :t, :note, :h) RETURNING *"),
            {"c": cat_id, "u": user_id, "n": name, "d": dose, "s": starts_on, "days": days, "e": _ends(starts_on, days),
             "t": times_per_day, "note": note, "h": bool(sitter_hidden)}).one())


def get_med(user_id: str, med_id: int) -> Optional[Dict[str, Any]]:
    with get_engine().connect() as c:
        r = c.execute(text("SELECT * FROM cat_medications WHERE id = :i AND user_id = :u"), {"i": med_id, "u": user_id}).first()
    return _row(r) if r else None


def list_meds(user_id: str, cat_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_engine().connect() as c:
        _owned_cat(c, user_id, cat_id)
        return [_row(r) for r in c.execute(text(
            "SELECT * FROM cat_medications WHERE cat_id = :c AND user_id = :u AND (CAST(:s AS text) IS NULL OR status = :s) "
            "ORDER BY (status = 'active') DESC, starts_on DESC, id DESC"), {"c": cat_id, "u": user_id, "s": status})]


def update_med(user_id: str, med_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Правка по белому списку. Дата конца пересчитывается из начала и числа дней."""
    values = {k: v for k, v in (values or {}).items() if k in EDITABLE_MED}
    with get_engine().begin() as c:
        cur = c.execute(text("SELECT * FROM cat_medications WHERE id = :i AND user_id = :u FOR UPDATE"), {"i": med_id, "u": user_id}).first()
        if cur is None:
            return None
        m = {**_row(cur), **values}
        m["name"] = _check_med(m["name"], m["times_per_day"], m["days"], m["status"])
        m["dose"], m["note"] = _clean(m["dose"], 80), _clean(m["note"], 500)
        r = c.execute(text(
            "UPDATE cat_medications SET name=:name, dose=:dose, note=:note, starts_on=:starts_on, days=:days, ends_on=:ends_on, "
            "times_per_day=:times_per_day, status=:status, sitter_hidden=:sitter_hidden, updated_at=now() WHERE id=:i AND user_id=:u RETURNING *"),
            {**{k: m[k] for k in EDITABLE_MED}, "sitter_hidden": bool(m["sitter_hidden"]), "ends_on": _ends(m["starts_on"], m["days"]),
             "i": med_id, "u": user_id}).one()
    return _row(r)


def set_med_task(user_id: str, med_id: int, task_id: Optional[int]) -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE cat_medications SET task_id = :t, updated_at = now() WHERE id = :i AND user_id = :u"), {"t": task_id, "i": med_id, "u": user_id})


def delete_med(user_id: str, med_id: int) -> Optional[Dict[str, Any]]:
    with get_engine().begin() as c:
        r = c.execute(text("DELETE FROM cat_medications WHERE id = :i AND user_id = :u RETURNING *"), {"i": med_id, "u": user_id}).first()
    return _row(r) if r else None


def expire_courses(user_id: str, today: date) -> List[Dict[str, Any]]:
    """Курсы с концом раньше today становятся «done». Возвращает закрытые строки (вызывающий закрывает их задачи). Повтор безопасен."""
    with get_engine().begin() as c:
        return [_row(r) for r in c.execute(text(
            "UPDATE cat_medications SET status = 'done', updated_at = now() WHERE user_id = :u AND status = 'active' "
            "AND ends_on IS NOT NULL AND ends_on < :d RETURNING *"), {"u": user_id, "d": today})]


def current_meds(user_id: str, today: date, cat_id: Optional[int] = None, *, for_sitter: bool = False) -> List[Dict[str, Any]]:
    """Действующие сегодня курсы: активны, уже начались и не закончились. for_sitter — без скрытых владельцем."""
    with get_engine().connect() as c:
        return [_row(r) for r in c.execute(text(
            "SELECT m.* FROM cat_medications m JOIN cats ct ON ct.id = m.cat_id AND ct.user_id = m.user_id AND ct.status <> 'rejected' "
            "WHERE m.user_id = :u AND m.status = 'active' AND m.starts_on <= :d AND (m.ends_on IS NULL OR m.ends_on >= :d) "
            "AND (CAST(:c AS bigint) IS NULL OR m.cat_id = :c) AND (NOT :s OR NOT m.sitter_hidden) ORDER BY m.cat_id, m.starts_on, m.id"),
            {"u": user_id, "d": today, "c": cat_id, "s": bool(for_sitter)})]


# ── ближайшие события (для «штампа» карточки кота) ────────────────────────────────────────────────────────────────────

def upcoming(user_id: str, today: date, horizon_days: int = 60) -> List[Dict[str, Any]]:
    """Ближайшие события по всем котам: запланированные визиты (в том числе просроченные: overdue), срок следующей прививки у
    состоявшихся, конец действующего курса. Сортировка по дате."""
    until = today + timedelta(days=max(1, min(int(horizon_days), 365)))
    out: List[Dict[str, Any]] = []
    with get_engine().connect() as c:
        for r in c.execute(text(
                "SELECT v.id, v.cat_id, v.kind, v.title, v.visit_on AS d FROM cat_vet_visits v JOIN cats ct ON ct.id = v.cat_id AND ct.status <> 'rejected' "
                "WHERE v.user_id = :u AND v.status = 'planned' AND v.visit_on <= :until"), {"u": user_id, "until": until}):
            out.append({"type": "vet", "ref_id": r.id, "cat_id": r.cat_id, "kind": r.kind, "title": r.title, "date": r.d, "overdue": r.d < today})
        for r in c.execute(text(
                "SELECT v.id, v.cat_id, v.kind, v.title, v.next_due AS d FROM cat_vet_visits v JOIN cats ct ON ct.id = v.cat_id AND ct.status <> 'rejected' "
                "WHERE v.user_id = :u AND v.status = 'done' AND v.next_due IS NOT NULL AND v.next_due <= :until "
                "AND NOT EXISTS (SELECT 1 FROM cat_vet_visits w WHERE w.cat_id = v.cat_id AND w.kind = v.kind AND w.status = 'planned' AND w.visit_on >= v.next_due - 30)"),
                {"u": user_id, "until": until}):
            out.append({"type": "vet_next", "ref_id": r.id, "cat_id": r.cat_id, "kind": r.kind, "title": r.title, "date": r.d, "overdue": r.d < today})
        for r in c.execute(text(
                "SELECT m.id, m.cat_id, m.name, m.ends_on AS d FROM cat_medications m JOIN cats ct ON ct.id = m.cat_id AND ct.status <> 'rejected' "
                "WHERE m.user_id = :u AND m.status = 'active' AND m.ends_on IS NOT NULL AND m.ends_on >= :today AND m.ends_on <= :until"),
                {"u": user_id, "today": today, "until": until}):
            out.append({"type": "med_end", "ref_id": r.id, "cat_id": r.cat_id, "kind": "med", "title": r.name, "date": r.d, "overdue": False})
    out.sort(key=lambda e: (e["date"], e["cat_id"], e["ref_id"]))
    return out


# ── для ситтера (читающий токен): только то, что владелец открыл, минимальный набор полей ────────────────────────────────

SITTER_WEIGHTS_PER_CAT = 12
SITTER_VISITS_BACK_DAYS = 60
SITTER_VISITS_LIMIT = 50


def sitter_weights(user_id: str, per_cat: int = SITTER_WEIGHTS_PER_CAT) -> List[Dict[str, Any]]:
    """Взвешивания, которые владелец открыл ситтеру (sitter_visible): последние per_cat на кота. Только кот, дата, граммы: без заметок и id."""
    with get_engine().connect() as c:
        return [_row(r) for r in c.execute(text(
            "SELECT cat_id, weighed_on, grams FROM ("
            " SELECT w.cat_id, w.weighed_on, w.grams, row_number() OVER (PARTITION BY w.cat_id ORDER BY w.weighed_on DESC) AS rn"
            " FROM cat_weights w JOIN cats ct ON ct.id = w.cat_id AND ct.user_id = w.user_id AND ct.status = 'confirmed'"
            " WHERE w.user_id = :u AND w.sitter_visible) t WHERE rn <= :n ORDER BY cat_id, weighed_on DESC"),
            {"u": user_id, "n": max(1, min(int(per_cat), 100))})]


def sitter_visits(user_id: str, today: date, back_days: int = SITTER_VISITS_BACK_DAYS, limit: int = SITTER_VISITS_LIMIT) -> List[Dict[str, Any]]:
    """Визиты, которые владелец открыл ситтеру: запланированные и состоявшиеся за последние back_days. Только кот, вид, название, дата, статус:
    без клиники, заметок, следующего срока и связи с задачей."""
    since = today - timedelta(days=max(1, min(int(back_days), 365)))
    with get_engine().connect() as c:
        return [_row(r) for r in c.execute(text(
            "SELECT v.cat_id, v.kind, v.title, v.visit_on, v.status FROM cat_vet_visits v "
            "JOIN cats ct ON ct.id = v.cat_id AND ct.user_id = v.user_id AND ct.status = 'confirmed' "
            "WHERE v.user_id = :u AND v.sitter_visible AND v.status IN ('planned', 'done') AND v.visit_on >= :since "
            "ORDER BY v.visit_on, v.id LIMIT :l"), {"u": user_id, "since": since, "l": max(1, min(int(limit), 200))})]
