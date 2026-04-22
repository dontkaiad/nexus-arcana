"""miniapp/backend/routes/weather.py — GET /api/weather."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends

from core.notion_client import memory_get

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


@router.get("/weather")
async def get_weather(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    cached = _cached(tg_id)
    if cached:
        return cached

    # wave7.4: сначала ищем город в Памяти (ключ city_{tg_id}),
    # только потом падаем на TZ → город.
    city_from_memory = await memory_get(f"city_{tg_id}")
    if city_from_memory and city_from_memory.strip():
        city = city_from_memory.strip()
    else:
        tz_raw = await memory_get(f"tz_{tg_id}")
        tz = (tz_raw or "Europe/Moscow").strip()
        city = TZ_TO_CITY.get(tz, "Moscow")

    data = await _fetch_openmeteo(city)
    if not data:
        return {"city": city, "temp": 0, "code": 0, "kind": "clear",
                "description": "—", "error": "fetch_failed"}

    _store(tg_id, data)
    return data
