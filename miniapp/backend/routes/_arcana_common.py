"""miniapp/backend/routes/_arcana_common.py — общие утилиты для Arcana-роутов.

Клиенто-маппинг, парсинг полей сеанса/ритуала, константы.
"""
from __future__ import annotations

import re
from typing import Optional

from core.config import config
from core.notion_client import arcana_clients_summary, query_pages

from miniapp.backend._helpers import (
    cat_from_notion,
    date_of,
    extract_time,
    multi_select_names,
    number_of,
    relation_ids_of,
    rich_text_plain,
    select_of,
    title_plain,
    to_local_date,
)

# ── Константы ───────────────────────────────────────────────────────────────

SESSION_UNVERIFIED = {"", "⏳ Не проверено"}
SESSION_YES = "✅ Да"
SESSION_NO = "❌ Нет"
SESSION_PARTIAL = "〰️ Частично"

RITUAL_YES = "✅ Сработало"
RITUAL_NO = "❌ Не сработало"
RITUAL_PARTIAL = "〰️ Частично"
RITUAL_UNVERIFIED = {"", "⏳ Не проверено"}

SUPPLIES_CATEGORIES = {"🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды"}

_BOTTOM_RE = re.compile(r"🂠\s*Дно:\s*([^\n]+?)(?:\n|$)")


def _resolve_card_en(raw: str) -> str:
    """RU → EN (The Fool, ...). Если не нашли — возвращаем сырое имя."""
    try:
        from miniapp.backend.tarot import find_card
        c = find_card("rider-waite", raw)
        if c and c.get("en"):
            return c["en"]
    except Exception:
        pass
    return raw


# ── Client name map ─────────────────────────────────────────────────────────

async def load_clients_map(user_notion_id: str) -> dict[str, dict]:
    """Загружает всех клиентов юзера, возвращает {client_id: {...details...}}."""
    pages = await arcana_clients_summary(user_notion_id=user_notion_id)
    out: dict[str, dict] = {}
    for p in pages:
        props = p.get("properties", {})
        status = (props.get("Статус", {}).get("status") or {}).get("name", "")
        out[p["id"]] = {
            "id": p["id"],
            "name": title_plain(p, "Имя"),
            "status": status,
            "contact": rich_text_plain(p, "Контакт"),
            "request": rich_text_plain(p, "Запрос"),
            "notes": rich_text_plain(p, "Заметки"),
        }
    return out


def client_name_from(page: dict, clients_map: dict[str, dict]) -> tuple[str, Optional[str]]:
    """→ (name, client_id | None). Если relation пуст → ('Личный', None)."""
    ids = relation_ids_of(page, "👥 Клиенты")
    if not ids:
        return "Личный", None
    cid = ids[0]
    info = clients_map.get(cid)
    if info:
        return info["name"] or "…", cid
    return cid[:8] + "…", cid


# ── Session parsing ─────────────────────────────────────────────────────────

def extract_bottom_from_interp(interp: str) -> tuple[Optional[str], str]:
    """('Король Кубков', interp без строки с дном) или (None, interp as-is)."""
    if not interp:
        return None, interp or ""
    m = _BOTTOM_RE.search(interp)
    if not m:
        return None, interp
    name = m.group(1).strip()
    cleaned = _BOTTOM_RE.sub("", interp).rstrip()
    return name, cleaned


def split_cards_raw(raw: str) -> list[dict]:
    """'Шут, Маг · Жрица\\nТуз' → [{name: 'Шут', ...}, ...]. Всегда 4 null-поля."""
    if not raw:
        return []
    parts: list[str] = []
    for line in raw.splitlines():
        for chunk in re.split(r"[·,]", line):
            s = chunk.strip(" ·,")
            if s:
                parts.append(s)
    return [
        {"name": p, "pos": None, "icon": None, "image_url": None}
        for p in parts
    ]


def serialize_session_brief(page: dict, clients_map: dict, tz_offset: int) -> dict:
    """Короткая сериализация сеанса для списков (/today, /sessions)."""
    props = page.get("properties", {})
    session_type = select_of(page, "Тип сеанса")
    client_ids = relation_ids_of(page, "👥 Клиенты")
    client_name, client_id = client_name_from(page, clients_map)
    self_client = (session_type == "🌟 Личный") and not client_ids

    cards_raw = rich_text_plain(page, "Карты")
    cards = split_cards_raw(cards_raw)

    deadline_raw = date_of(page, "Дата")
    date_local = to_local_date(deadline_raw, tz_offset)

    return {
        "id": page.get("id", ""),
        "question": title_plain(page, "Тема"),
        "client": client_name,
        "client_id": client_id,
        "self_client": self_client,
        "area": multi_select_names(page, "Область"),
        "deck": ", ".join(multi_select_names(page, "Колоды")) or None,
        "type": (multi_select_names(page, "Тип расклада") or [None])[0],
        "session_type": session_type or None,
        "date": date_local.isoformat() if date_local else None,
        "date_time": extract_time(deadline_raw, tz_offset),
        # wave7.8.5: в списке раскладов карты — EN (The Fool, The Magician, ...)
        "cards_brief": [_resolve_card_en(c["name"]) for c in cards[:3]],
        "done": select_of(page, "Сбылось") or "⏳ Не проверено",
        "price": int(round(number_of(page, "Сумма"))),
        "paid": int(round(number_of(page, "Оплачено"))),
    }


# ── Ritual parsing ──────────────────────────────────────────────────────────

def serialize_ritual_brief(page: dict, clients_map: dict, tz_offset: int) -> dict:
    """Короткая сериализация ритуала для списков."""
    client_name, client_id = client_name_from(page, clients_map)
    # Цель — multi_select в schema, но на записи иногда select; поддержим оба
    goals = multi_select_names(page, "Цель")
    if not goals:
        single = select_of(page, "Цель")
        if single:
            goals = [single]
    place = select_of(page, "Место")
    result = select_of(page, "Результат") or "⏳ Не проверено"
    deadline_raw = date_of(page, "Дата")
    date_local = to_local_date(deadline_raw, tz_offset)
    return {
        "id": page.get("id", ""),
        "name": title_plain(page, "Название"),
        "goal": goals[0] if goals else None,
        "goals": goals,
        "place": place or None,
        "date": date_local.isoformat() if date_local else None,
        "type": select_of(page, "Тип") or None,
        "client": client_name,
        "client_id": client_id,
        "result": result,
        "price": int(round(number_of(page, "Цена за ритуал"))),
        "paid": int(round(number_of(page, "Оплачено"))),
    }


def parse_supplies(raw: str) -> tuple[list[dict], int]:
    """Парсит «Свечи чёрные × 3 — 180\\nЛадан — 95» → list + total.

    Формат: одна строка — один пункт. Опциональный хвост '— <число>' = цена.
    """
    if not raw:
        return [], 0
    items: list[dict] = []
    total = 0
    for line in raw.splitlines():
        line = line.strip(" ·•-—")
        if not line:
            continue
        price: Optional[int] = None
        m = re.search(r"[—\-]\s*(\d[\d\s]*)\s*[₽р]?\s*$", line)
        name = line
        qty = None
        if m:
            try:
                price = int(m.group(1).replace(" ", ""))
                total += price
                name = line[: m.start()].rstrip(" ·•-—")
            except ValueError:
                price = None
        # qty: extract "× N" or "x N" tail from name
        qm = re.search(r"[×x]\s*(\d+)\s*$", name)
        if qm:
            qty = qm.group(1)
            name = name[: qm.start()].rstrip()
        items.append({"name": name, "qty": qty, "price": price})
    return items, total


def split_lines(raw: str) -> list[str]:
    """Разбить rich_text (структура/подношения/силы) на строки."""
    if not raw:
        return []
    return [ln.strip(" •-—·") for ln in raw.splitlines() if ln.strip()]


# ── Finance helpers ─────────────────────────────────────────────────────────

async def query_arcana_finance(user_notion_id: str, start: str, end: str,
                                type_filter: Optional[str] = None) -> list[dict]:
    """Финансовые записи Arcana за диапазон [start, end) (date-only boundaries)."""
    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": "🌒 Arcana"}},
        {"property": "Дата", "date": {"on_or_after": start}},
        {"property": "Дата", "date": {"before": end}},
    ]
    if type_filter:
        conditions.append({"property": "Тип", "select": {"equals": type_filter}})
    if user_notion_id:
        conditions.append({"property": "🪪 Пользователи",
                           "relation": {"contains": user_notion_id}})
    return await query_pages(config.nexus.db_finance,
                             filters={"and": conditions}, page_size=500)


def month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y}-{m:02d}-01"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = f"{ny}-{nm:02d}-01"
    return start, end
