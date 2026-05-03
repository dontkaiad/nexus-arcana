"""core/ru_calendar.py — производственный календарь РФ.

Источник: xmlcalendar.ru (учитывает Постановления Правительства о переносах).
Кэш в SQLite (TTL 30 дней). При сбое API — отдаём кэш или fallback на
базовые федеральные праздники без переносов.

Семантика поля `days` в JSON xmlcalendar:
- `1`        → нерабочий (выходной/праздник)
- `8*`       → сокращённый рабочий день
- `11+`      → нерабочий, перенос (имя берём из `transitions[].from`)
- `1+1`      → нерабочий, привязка к holiday id (имя из `holidays[]`,
               но xmlcalendar часто не возвращает массив holidays)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("core.ru_calendar")

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "ru_calendar.db")
_CACHE_TTL_DAYS = 30
_API_URL = "https://xmlcalendar.ru/data/ru/{year}/calendar.json"

# Имена федеральных праздников по дате (MM-DD).
_HOLIDAY_NAMES: dict[str, str] = {
    "01-01": "Новогодние каникулы",
    "01-02": "Новогодние каникулы",
    "01-03": "Новогодние каникулы",
    "01-04": "Новогодние каникулы",
    "01-05": "Новогодние каникулы",
    "01-06": "Новогодние каникулы",
    "01-07": "Рождество Христово",
    "01-08": "Новогодние каникулы",
    "02-23": "День защитника Отечества",
    "03-08": "Международный женский день",
    "05-01": "Праздник Весны и Труда",
    "05-09": "День Победы",
    "06-12": "День России",
    "11-04": "День народного единства",
}

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ru_calendar_cache (
    year INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def _init_db() -> None:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(_CREATE_SQL)
        con.commit()
    finally:
        con.close()


_init_db()


# ── HTTP + cache ───────────────────────────────────────────────────────────

def _cache_get(year: int) -> Optional[tuple[dict, datetime]]:
    con = sqlite3.connect(_DB_PATH)
    try:
        row = con.execute(
            "SELECT payload, fetched_at FROM ru_calendar_cache WHERE year = ?",
            (year,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    payload_raw, fetched_at_raw = row
    try:
        payload = json.loads(payload_raw)
        fetched_at = datetime.fromisoformat(fetched_at_raw)
    except Exception:
        return None
    return payload, fetched_at


def _cache_put(year: int, payload: dict) -> None:
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO ru_calendar_cache (year, payload, fetched_at) "
            "VALUES (?, ?, ?)",
            (year, json.dumps(payload, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
    finally:
        con.close()


async def _fetch_remote(year: int) -> Optional[dict]:
    url = _API_URL.format(year=year)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning("ru_calendar HTTP %s for %s", r.status_code, year)
                return None
            return r.json()
    except Exception as e:
        logger.warning("ru_calendar fetch failed for %s: %s", year, e)
        return None


# ── Parser ─────────────────────────────────────────────────────────────────

def _parse_day_token(token: str) -> tuple[int, str]:
    """'8*' → (8, 'short'). '11+' → (11, 'transition_off'). '1' → (1, 'off')."""
    t = token.strip()
    if not t:
        return 0, ""
    if t.endswith("*"):
        try:
            return int(t[:-1]), "short"
        except ValueError:
            return 0, ""
    # '+' или '+ID' → transition
    if "+" in t:
        head = t.split("+", 1)[0]
        try:
            return int(head), "transition_off"
        except ValueError:
            return 0, ""
    try:
        return int(t), "off"
    except ValueError:
        return 0, ""


def _parse_transitions(transitions: list[dict]) -> dict[str, str]:
    """Из списка `[{from: 'MM.DD', to: 'MM.DD'}]` строит {to_iso_md: source_holiday_name}.

    Source-имя ищется в _HOLIDAY_NAMES по `from` (формат у xmlcalendar — DD.MM).
    """
    out: dict[str, str] = {}
    for tr in transitions or []:
        from_str = tr.get("from") or ""
        to_str = tr.get("to") or ""
        # Формат у xmlcalendar — MM.DD
        try:
            from_mm, from_dd = from_str.split(".")
            to_mm, to_dd = to_str.split(".")
            from_md = f"{int(from_mm):02d}-{int(from_dd):02d}"
            to_md = f"{int(to_mm):02d}-{int(to_dd):02d}"
        except (ValueError, AttributeError):
            continue
        from_name = _HOLIDAY_NAMES.get(from_md)
        if from_name:
            out[to_md] = f"Перенос с {int(from_dd)} {_RU_MONTH_GEN[int(from_mm)]}"
    return out


_RU_MONTH_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def normalize(payload: dict) -> dict:
    """xmlcalendar JSON → нормализованная структура для нашего endpoint.

    {
      "year": int,
      "holiday_days": {iso: {"name": str, "kind": "holiday"|"transition_off"}},
      "short_days": {iso: {"name": str|None}},
      "working_weekends": [iso, ...]
    }
    """
    year = int(payload.get("year") or 0)
    transitions = _parse_transitions(payload.get("transitions") or [])
    holiday_days: dict[str, dict] = {}
    short_days: dict[str, dict] = {}
    listed_dates: set[str] = set()

    for m in payload.get("months") or []:
        month_num = int(m.get("month") or 0)
        days_str = m.get("days") or ""
        for token in days_str.split(","):
            day_num, kind = _parse_day_token(token)
            if day_num <= 0:
                continue
            try:
                d = date(year, month_num, day_num)
            except ValueError:
                continue
            iso = d.isoformat()
            md = f"{month_num:02d}-{day_num:02d}"
            listed_dates.add(iso)
            if kind == "short":
                short_days[iso] = {"name": _short_day_name(year, month_num, day_num)}
                continue
            # off / transition_off
            if md in _HOLIDAY_NAMES:
                holiday_days[iso] = {
                    "name": _HOLIDAY_NAMES[md],
                    "kind": "holiday",
                }
            elif kind == "transition_off" or md in transitions:
                holiday_days[iso] = {
                    "name": transitions.get(md, "Перенос выходного"),
                    "kind": "transition_off",
                }
            else:
                # обычный календарный выходной (сб/вс) — НЕ кладём в holiday_days
                # (фронт решает по dayofweek). Иначе раскрасим все sat/sun золотом.
                if d.weekday() < 5:
                    # Будний день в days без модификатора и не известный праздник —
                    # значит это всё-таки нерабочий из-за регионального праздника
                    # или перенос без записи в transitions. Подсвечиваем как holiday.
                    holiday_days[iso] = {"name": "Нерабочий день", "kind": "holiday"}

    # Рабочие выходные: считаем все Sat/Sun года, если их нет в days и не в short_days.
    working_weekends: list[str] = []
    if year > 0:
        for m in payload.get("months") or []:
            month_num = int(m.get("month") or 0)
            if month_num <= 0:
                continue
            days_in_month = (date(year, month_num + 1, 1) - timedelta(days=1)).day if month_num < 12 \
                else 31
            for day_num in range(1, days_in_month + 1):
                d = date(year, month_num, day_num)
                if d.weekday() >= 5 and d.isoformat() not in listed_dates:
                    working_weekends.append(d.isoformat())

    return {
        "year": year,
        "holiday_days": holiday_days,
        "short_days": short_days,
        "working_weekends": working_weekends,
    }


def _short_day_name(year: int, month_num: int, day_num: int) -> Optional[str]:
    """Имя сокращённого дня — «канун Х» если завтра праздник."""
    try:
        tomorrow = date(year, month_num, day_num) + timedelta(days=1)
    except ValueError:
        return None
    md = f"{tomorrow.month:02d}-{tomorrow.day:02d}"
    name = _HOLIDAY_NAMES.get(md)
    return f"Канун: {name}" if name else "Сокращённый рабочий день"


# ── Fallback ───────────────────────────────────────────────────────────────

def _fallback_base(year: int) -> dict:
    """Минимальный набор федеральных праздников без переносов и сокращений."""
    holiday_days: dict[str, dict] = {}
    for md, name in _HOLIDAY_NAMES.items():
        try:
            mm, dd = md.split("-")
            iso = f"{year}-{mm}-{dd}"
            date.fromisoformat(iso)  # валидация
            holiday_days[iso] = {"name": name, "kind": "holiday"}
        except ValueError:
            continue
    return {
        "year": year,
        "holiday_days": holiday_days,
        "short_days": {},
        "working_weekends": [],
    }


# ── Public API ─────────────────────────────────────────────────────────────

async def fetch_year(year: int) -> dict:
    """Нормализованный календарь года. Кэш + fallback."""
    cached = _cache_get(year)
    now = datetime.now(timezone.utc)
    if cached:
        payload, fetched_at = cached
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if now - fetched_at < timedelta(days=_CACHE_TTL_DAYS):
            return normalize(payload)

    remote = await _fetch_remote(year)
    if remote is not None:
        _cache_put(year, remote)
        return normalize(remote)

    # API недоступен. Если кэш был (просроченный) — отдаём его.
    if cached:
        logger.info("ru_calendar fallback to stale cache for %s", year)
        return normalize(cached[0])

    # Иначе — base.
    logger.info("ru_calendar fallback to base holidays for %s", year)
    return _fallback_base(year)


async def get_month_info(year: int, month_num: int) -> dict:
    """Подмножество fetch_year для одного месяца.

    Возвращает:
        {
          "holiday_days": [int],       # дни-числа
          "short_days": [int],
          "working_weekends": [int],
          "holidays_info": [{"day": int, "name": str, "kind": str}]
        }
    """
    full = await fetch_year(year)
    info: list[dict] = []

    for iso, meta in full["holiday_days"].items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if d.year == year and d.month == month_num:
            info.append({"day": d.day, "name": meta["name"], "kind": meta["kind"]})

    for iso, meta in full["short_days"].items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if d.year == year and d.month == month_num:
            info.append({"day": d.day, "name": meta.get("name") or "Сокращённый день",
                         "kind": "short"})

    info.sort(key=lambda x: (x["day"], x["kind"]))

    holiday_days = sorted({h["day"] for h in info if h["kind"] in ("holiday", "transition_off")})
    short_days = sorted({h["day"] for h in info if h["kind"] == "short"})
    working_weekends = sorted({
        date.fromisoformat(iso).day
        for iso in full["working_weekends"]
        if date.fromisoformat(iso).year == year
        and date.fromisoformat(iso).month == month_num
    })

    return {
        "holiday_days": holiday_days,
        "short_days": short_days,
        "working_weekends": working_weekends,
        "holidays_info": info,
    }
