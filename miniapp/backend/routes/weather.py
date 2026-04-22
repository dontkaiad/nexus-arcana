"""miniapp/backend/routes/weather.py — GET /api/weather."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, Depends

from core.config import config
from core.notion_client import memory_get, memory_set, query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend import cache as _cache
from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.weather")

router = APIRouter()


TZ_TO_CITY: dict[str, str] = {
    "Europe/Moscow": "Moscow",
    "Europe/Saint_Petersburg": "Saint Petersburg",
    "Europe/London": "London",
    "Asia/Istanbul": "Istanbul",
    "Asia/Tbilisi": "Tbilisi",
    "Asia/Yerevan": "Yerevan",
    "Asia/Bangkok": "Bangkok",
    "Asia/Dubai": "Dubai",
    "Europe/Berlin": "Berlin",
    "Europe/Paris": "Paris",
    "Europe/Amsterdam": "Amsterdam",
    "Europe/Rome": "Rome",
    "Europe/Madrid": "Madrid",
    "America/New_York": "New York",
    "America/Los_Angeles": "Los Angeles",
    "Asia/Tokyo": "Tokyo",
    "Asia/Shanghai": "Shanghai",
}


WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("clear", "Ясно"),
    1: ("clear", "В основном ясно"),
    2: ("cloudy", "Переменная облачность"),
    3: ("cloudy", "Пасмурно"),
    45: ("fog", "Туман"),
    48: ("fog", "Иней"),
    51: ("rain", "Морось"),
    53: ("rain", "Морось"),
    55: ("rain", "Сильная морось"),
    61: ("rain", "Дождь"),
    63: ("rain", "Дождь"),
    65: ("rain", "Сильный дождь"),
    71: ("snow", "Снег"),
    73: ("snow", "Снег"),
    75: ("snow", "Сильный снег"),
    77: ("snow", "Снежные зёрна"),
    80: ("rain", "Ливень"),
    81: ("rain", "Ливень"),
    82: ("rain", "Сильный ливень"),
    85: ("snow", "Снегопад"),
    86: ("snow", "Снегопад"),
    95: ("rain", "Гроза"),
    96: ("rain", "Гроза с градом"),
    99: ("rain", "Сильная гроза"),
}


_CACHE_TTL = 30 * 60  # 30 минут


def _init_weather_cache() -> None:
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS weather_cache ("
            "tg_id INTEGER PRIMARY KEY, "
            "city TEXT, temp INTEGER, code INTEGER, "
            "kind TEXT, description TEXT, "
            "updated_at INTEGER)"
        )
        con.commit()
    finally:
        con.close()


def _cached(tg_id: int) -> Optional[dict[str, Any]]:
    _init_weather_cache()
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        row = con.execute(
            "SELECT city, temp, code, kind, description, updated_at "
            "FROM weather_cache WHERE tg_id = ?",
            (tg_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    updated = row[5] or 0
    if time.time() - updated > _CACHE_TTL:
        return None
    return {
        "city": row[0], "temp": row[1], "code": row[2],
        "kind": row[3], "description": row[4],
    }


def _store(tg_id: int, data: dict) -> None:
    _init_weather_cache()
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO weather_cache "
            "(tg_id, city, temp, code, kind, description, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tg_id, data["city"], data["temp"], data["code"],
             data["kind"], data["description"], int(time.time())),
        )
        con.commit()
    finally:
        con.close()


async def _fetch_openmeteo(city: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            geo_r = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "ru"},
            )
            results = (geo_r.json() or {}).get("results") or []
            if not results:
                return None
            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]

            fc_r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code",
                },
            )
            cur = (fc_r.json() or {}).get("current") or {}
            code = int(cur.get("weather_code", 0))
            kind, desc = WMO_CODES.get(code, ("clear", "—"))
            return {
                "city": city,
                "temp": round(float(cur.get("temperature_2m", 0))),
                "code": code,
                "kind": kind,
                "description": desc,
            }
    except Exception as e:
        logger.warning("openmeteo fetch failed for %s: %s", city, e)
        return None


_CITY_ALIASES: dict[str, str] = {
    "спб": "Saint Petersburg",
    "питер": "Saint Petersburg",
    "петербург": "Saint Petersburg",
    "санкт-петербург": "Saint Petersburg",
    "санкт петербург": "Saint Petersburg",
    "мск": "Moscow",
    "москва": "Moscow",
}


def _normalize_city(raw: str) -> str:
    """СПб/Питер → Saint Petersburg для geocoding API."""
    if not raw:
        return raw
    key = raw.strip().lower().strip(".,!?;:")
    return _CITY_ALIASES.get(key, raw.strip())


async def _resolve_city_from_memory(tg_id: int) -> Optional[str]:
    """wave8.10/8.11: ищем город в Памяти Nexus по нескольким стратегиям.

    1) Явный ключ city_{tg_id} (из POST /weather/city).
    2) Общие ключи: 'город', 'city', 'Город'.
    3) Фьюзи-поиск: для user_notion_id берём записи где Ключ/Текст
       содержат 'город'/'city'/'живу' и вытаскиваем значение.
    """
    # 1) Явный ключ
    v = await memory_get(f"city_{tg_id}")
    if v and v.strip():
        logger.info("resolve_city[%s]: hit explicit key city_%s = %s", tg_id, tg_id, v)
        return _normalize_city(v)

    # 2) Распространённые ключи
    for key in ("город", "Город", "city", "City"):
        v = await memory_get(key)
        if v and v.strip():
            logger.info("resolve_city[%s]: hit common key %s = %s", tg_id, key, v)
            return _normalize_city(v)

    # 3) Фьюзи-поиск среди записей пользователя
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    db_id = config.nexus.db_memory
    if not db_id:
        logger.warning("resolve_city[%s]: no db_memory configured", tg_id)
        return None

    # Без фильтра по Актуально — старые записи тоже подходят.
    # Если user_notion_id есть — сузим; нет — обойдёмся без relation-фильтра.
    base_filters: list = []
    if user_notion_id:
        base_filters.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })

    or_clause = [
        {"property": "Ключ", "rich_text": {"contains": "город"}},
        {"property": "Ключ", "rich_text": {"contains": "city"}},
        {"property": "Текст", "title": {"contains": "город"}},
        {"property": "Текст", "title": {"contains": "живу"}},
        {"property": "Текст", "title": {"contains": "Питер"}},
        {"property": "Текст", "title": {"contains": "СПб"}},
    ]

    filters: dict
    if base_filters:
        filters = {"and": base_filters + [{"or": or_clause}]}
    else:
        filters = {"or": or_clause}

    try:
        pages = await query_pages(db_id, filters=filters, page_size=10)
    except Exception as e:
        logger.warning("resolve_city[%s] fuzzy search failed: %s", tg_id, e)
        return None

    logger.info("resolve_city[%s]: fuzzy found %d pages", tg_id, len(pages))
    for p in pages:
        props = p.get("properties", {}) or {}
        title_items = (props.get("Текст", {}) or {}).get("title") or []
        text = "".join(it.get("plain_text", "") for it in title_items).strip()
        key_items = (props.get("Ключ", {}) or {}).get("rich_text") or []
        key_text = "".join(it.get("plain_text", "") for it in key_items).strip()
        logger.info("resolve_city[%s]: candidate key=%r text=%r", tg_id, key_text, text)
        if not text:
            continue
        # «город: Санкт-Петербург» → «Санкт-Петербург»
        if ":" in text:
            tail = text.split(":", 1)[1].strip()
            if tail:
                return _normalize_city(tail)
        # «живу в СПб» → «СПб»
        words = text.split()
        if len(words) >= 2 and words[-2].lower() in {"в", "во", "из", "на"}:
            return _normalize_city(words[-1])
        return _normalize_city(text)
    return None


@router.get("/weather/debug")
async def weather_debug(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    """wave8.11: диагностика — показывает откуда резолвится город."""
    resolved = await _resolve_city_from_memory(tg_id)
    tz_raw = await memory_get(f"tz_{tg_id}")
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    return {
        "tg_id": tg_id,
        "user_notion_id": user_notion_id,
        "resolved_city": resolved,
        "tz_memory": tz_raw,
        "fallback_city": TZ_TO_CITY.get((tz_raw or "Europe/Moscow").strip(), "Moscow"),
    }


@router.get("/weather")
async def get_weather(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    cached = _cached(tg_id)
    if cached:
        return cached

    # wave8.10: мульти-стратегия поиска города в Памяти Nexus
    city = await _resolve_city_from_memory(tg_id)
    source = "memory"
    if not city:
        tz_raw = await memory_get(f"tz_{tg_id}")
        tz = (tz_raw or "Europe/Moscow").strip()
        city = TZ_TO_CITY.get(tz, "Moscow")
        source = f"tz_fallback({tz})"
    logger.info("weather[%s]: city=%s source=%s", tg_id, city, source)

    data = await _fetch_openmeteo(city)
    if not data:
        return {"city": city, "temp": 0, "code": 0, "kind": "clear",
                "description": "—", "error": "fetch_failed"}

    _store(tg_id, data)
    return data


@router.post("/weather/refresh")
async def refresh_weather(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    """wave8.11: сброс кэша погоды без указания города."""
    _init_weather_cache()
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute("DELETE FROM weather_cache WHERE tg_id = ?", (tg_id,))
        con.commit()
    finally:
        con.close()
    return {"ok": True}


@router.post("/weather/city")
async def set_weather_city(
    tg_id: int = Depends(current_user_id),
    payload: dict = Body(...),
) -> dict[str, Any]:
    """wave8.9: пользователь задаёт свой город. Сохраняем в Память + чистим кэш."""
    city = (payload.get("city") or "").strip()
    if not city:
        return {"ok": False, "error": "city_empty"}
    await memory_set(f"city_{tg_id}", city, category="⭐ Предпочтения")
    # чистим кэш погоды, чтобы следующий /api/weather сходил заново
    con = sqlite3.connect(_cache._DB_PATH)
    try:
        con.execute("DELETE FROM weather_cache WHERE tg_id = ?", (tg_id,))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "city": city}
