"""miniapp/backend/_helpers.py — общие утилиты для роутов.

Делим между today/tasks/finance/lists/memory/calendar: извлечение свойств
Notion, маппинг категорий/приоритетов, расчёт локального "сегодня" по
пользовательскому tz, проверка Бот == ☀️ Nexus.
"""
from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

from core.shared_handlers import get_user_tz

BOT_NEXUS = "☀️ Nexus"
BOT_ARCANA = "🌒 Arcana"

PRIO_FALLBACK = "⚪"


# ── Extraction ───────────────────────────────────────────────────────────────

def title_text(prop: dict) -> str:
    items = prop.get("title") or []
    return "".join(i.get("plain_text") or i.get("text", {}).get("content", "") for i in items)


def rich_text(prop: dict) -> str:
    items = prop.get("rich_text") or []
    return "".join(i.get("plain_text") or i.get("text", {}).get("content", "") for i in items)


def select_name(prop: dict) -> str:
    sel = prop.get("select") or {}
    return sel.get("name") or ""


def status_name(prop: dict) -> str:
    st = prop.get("status") or {}
    return st.get("name") or ""


def number_value(prop: dict) -> Optional[float]:
    return prop.get("number")


def checkbox_value(prop: dict) -> bool:
    return bool(prop.get("checkbox"))


def date_start(prop: dict) -> str:
    d = prop.get("date") or {}
    return d.get("start") or ""


def relation_ids(prop: dict) -> list[str]:
    return [r.get("id", "") for r in (prop.get("relation") or []) if r.get("id")]


def multi_select_list(prop: dict) -> list[str]:
    return [x.get("name", "") for x in (prop.get("multi_select") or []) if x.get("name")]


# ── Page-level конверторы (удобно вне today.py) ─────────────────────────────

def relation_ids_of(page: dict, prop_name: str) -> list[str]:
    return relation_ids((page.get("properties", {}) or {}).get(prop_name, {}))


def multi_select_names(page: dict, prop_name: str) -> list[str]:
    return multi_select_list((page.get("properties", {}) or {}).get(prop_name, {}))


def rich_text_plain(page: dict, prop_name: str) -> str:
    return rich_text((page.get("properties", {}) or {}).get(prop_name, {}))


def title_plain(page: dict, prop_name: str) -> str:
    return title_text((page.get("properties", {}) or {}).get(prop_name, {}))


def select_of(page: dict, prop_name: str) -> str:
    return select_name((page.get("properties", {}) or {}).get(prop_name, {}))


def number_of(page: dict, prop_name: str) -> float:
    return float(number_value((page.get("properties", {}) or {}).get(prop_name, {})) or 0)


def date_of(page: dict, prop_name: str) -> str:
    return date_start((page.get("properties", {}) or {}).get(prop_name, {}))


# ── Emoji / category / priority ──────────────────────────────────────────────

def first_emoji(s: str) -> str:
    """Первый символ, если это Symbol (категория `S*` в unicodedata)."""
    if not s:
        return ""
    ch = s[0]
    return ch if unicodedata.category(ch).startswith("S") else ""


def prio_from_notion(select_name_value: Optional[str]) -> str:
    """'🔴 Срочно' → '🔴'; fallback → '⚪'. Используется и для задач и для списков."""
    return first_emoji(select_name_value or "") or PRIO_FALLBACK


def cat_from_notion(select_name_value: Optional[str]) -> dict:
    """'💻 Подписки' → {'emoji': '💻', 'name': 'Подписки', 'full': '💻 Подписки'}.

    Если эмодзи нет — `emoji` = '', `name` = full = значение как есть.
    """
    full = select_name_value or ""
    emoji = first_emoji(full)
    name = full[len(emoji):].strip() if emoji else full
    return {"emoji": emoji, "name": name, "full": full}


# ── Date parsing ─────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(notion_date_prop: Optional[dict], tz_offset: int = 3) -> Optional[dict]:
    """Notion date-prop → {"iso": "YYYY-MM-DD", "has_time": bool, "time": "HH:MM" | None}.

    Возвращает None, если даты нет. Время конвертируется в локальный tz_offset.
    """
    if not notion_date_prop:
        return None
    d = notion_date_prop.get("date") if "date" in notion_date_prop else notion_date_prop
    if not d:
        return None
    start = d.get("start") or ""
    if not start:
        return None

    if len(start) == 10:
        return {"iso": start, "has_time": False, "time": None}

    dt = _parse_iso(start)
    if not dt:
        return {"iso": start[:10], "has_time": False, "time": None}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone(timedelta(hours=tz_offset)))
    return {
        "iso": local.date().isoformat(),
        "has_time": True,
        "time": local.strftime("%H:%M"),
    }


def to_local_date(iso: str, tz_offset: int) -> Optional[date]:
    """Notion ISO (date or datetime) → local date in tz_offset. None если не парсится."""
    if not iso:
        return None
    if len(iso) == 10:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            return None
    dt = _parse_iso(iso)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=tz_offset))).date()


def extract_time(iso: str, tz_offset: int) -> Optional[str]:
    """ISO datetime → 'HH:MM' в локальном tz; None если чистая дата."""
    if not iso or len(iso) <= 10:
        return None
    dt = _parse_iso(iso)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=tz_offset))).strftime("%H:%M")


# ── Today / tz ───────────────────────────────────────────────────────────────

async def today_user_tz(tg_id: int) -> Tuple[date, int]:
    """(today_local, tz_offset). today_local — date в пользовательском tz."""
    tz_offset = await get_user_tz(tg_id)
    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(timezone(timedelta(hours=tz_offset)))
    return local.date(), tz_offset


# ── Bot filter ───────────────────────────────────────────────────────────────

def is_bot_nexus(page: dict) -> bool:
    """Проверка: поле 'Бот' страницы равно '☀️ Nexus'."""
    props = page.get("properties", {})
    return select_name(props.get("Бот", {})) == BOT_NEXUS


def is_bot_arcana(page: dict) -> bool:
    """Проверка: поле 'Бот' страницы равно '🌒 Arcana'."""
    props = page.get("properties", {})
    return select_name(props.get("Бот", {})) == BOT_ARCANA
