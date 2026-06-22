"""core/location.py — единый источник правды для локации пользователя (#170).

Локация = (tz offset, город). Раньше хранилась в ДВУХ несвязанных местах:
задачи читали `tz_{tg_id}` (писал только бот через `_update_user_tz`), погода —
`city_{tg_id}` / скан текста заметок (писал мини-апп `POST /weather/city`,
МИНУЯ tz). Общего writer'а не было → город и offset разъезжались (поставила
город через мини-апп — погода обновилась, tz задач остался старым).

Теперь:
- ОДИН writer  — `set_user_location()`: пишет ОБА поля синхронно + чистит кеш;
- ОДИН reader  — `get_user_tz()`: TTL-кеш + ключ `tz_{tg_id}`;
- ОДИН справочник город→offset — `CITY_TZ` (+ `resolve_offset`).

Все ЯВНЫЕ пути ввода локации (текст боту, /tz, мини-апп set city) идут через
них. Инцидентное упоминание города в заметке tz НЕ меняет — это остаётся
weather-only fallback (full-scan в routes/weather.py).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional, Tuple

from core.repos.pg_memory_repo import PgMemoryRepo

logger = logging.getLogger("core.location")

# ── Справочник город→offset ───────────────────────────────────────────────────
# Substring-матч по нижнему регистру (падежи РФ-городов ловятся префиксом).
# Порядок вставки = порядок матча (первый матч побеждает): РФ/СНГ блок идёт
# первым, англоканон-алиасы — в самом конце (срабатывают, только если ничего
# раньше не совпало, напр. мини-апп отдал нормализованное "Saint Petersburg").
CITY_TZ: Dict[str, int] = {
    # Россия
    "москва": 3, "мск": 3, "московск": 3,
    "спб": 3, "санкт-петербург": 3, "питер": 3, "петербург": 3,
    "калининград": 2,
    "самара": 4, "удмуртия": 5, "ижевск": 5,
    "екатеринбург": 5, "екб": 5, "ебург": 5, "свердловск": 5, "уфа": 5,
    "челябинск": 5, "тюмень": 5, "башкирия": 5, "пермь": 5,
    "омск": 6,
    "новосибирск": 7, "новосиб": 7, "красноярск": 7, "томск": 7, "барнаул": 7,
    "иркутск": 8, "улан-удэ": 8,
    "якутск": 9, "хабаровск": 10, "владивосток": 10, "магадан": 11,
    "сахалин": 11, "камчатка": 12,
    # Россия — европейская часть (UTC+3) и Урал, расширение #170
    "краснодар": 3, "сочи": 3, "ростов": 3, "воронеж": 3, "волгоград": 3,
    "казань": 3, "нижний новгород": 3, "новгород": 3, "ярославль": 3,
    "тула": 3, "мурманск": 3, "архангельск": 3, "сургут": 5, "ноябрьск": 5,
    # Другие
    "дубай": 4, "абу-даби": 4,
    "берлин": 1, "варшава": 1, "рим": 1, "париж": 1,
    "амстердам": 1, "мадрид": 1,
    "лондон": 0,
    "бангкок": 7, "токио": 9, "сеул": 9, "пекин": 8, "шанхай": 8,
    "нью-йорк": -5, "нью йорк": -5, "лос-анджелес": -8,
    # Турция (UTC+3 круглый год с 2016)
    "турци": 3, "стамбул": 3, "анкара": 3,
    "анталь": 3, "алани": 3, "аланьи": 3, "аланью": 3, "аланье": 3,
    "измир": 3, "бодрум": 3, "кемер": 3, "фетхие": 3, "мерсин": 3,
    # Грузия / Армения
    "тбилис": 4, "батум": 4, "ереван": 4,
    # Кипр / Израиль
    "кипр": 2, "ларнак": 2, "лимасол": 2, "никос": 2,
    "израил": 2, "тель-авив": 2, "иерусалим": 2,
    # Англоканон-алиасы (мини-апп может отдать нормализованное англ. имя из
    # weather: "Saint Petersburg", "Moscow", ...). Только в самом конце.
    "saint petersburg": 3, "st petersburg": 3, "moscow": 3,
    "kaliningrad": 2, "yekaterinburg": 5, "novosibirsk": 7,
    "krasnodar": 3, "sochi": 3, "kazan": 3,
    "london": 0, "istanbul": 3, "ankara": 3, "antalya": 3, "alanya": 3,
    "izmir": 3, "bodrum": 3, "kemer": 3, "fethiye": 3, "mersin": 3,
    "tbilisi": 4, "batumi": 4, "yerevan": 4,
    "larnaca": 2, "limassol": 2, "nicosia": 2, "tel aviv": 2, "jerusalem": 2,
    "dubai": 4, "abu dhabi": 4, "bangkok": 7, "tokyo": 9, "seoul": 9,
    "beijing": 8, "shanghai": 8, "berlin": 1, "paris": 1, "amsterdam": 1,
    "rome": 1, "madrid": 1, "warsaw": 1,
    "new york": -5, "los angeles": -8,
}

_UTC_RE = re.compile(r"utc\s*([+-]?\d+)", re.IGNORECASE)


def resolve_offset(text: str) -> Tuple[Optional[int], Optional[str]]:
    """text → (offset, matched_city). Справочник `CITY_TZ` (substring, первый
    матч) → паттерн `UTC±X`. (None, None) если не распознали — caller сам решает
    fallback (бот — Haiku; мини-апп — оставить tz прежним)."""
    if not text:
        return None, None
    low = text.lower()
    for city, tz in CITY_TZ.items():
        if city in low:
            return tz, city
    m = _UTC_RE.search(low)
    if m:
        try:
            return int(m.group(1)), None
        except ValueError:
            pass
    return None, None


# ── Кеш tz (общий для sync-читателей: tasks._now, time_manager) ───────────────
# TTL бьёт КРОСС-ПРОЦЕССНУЮ рассинхронизацию: мини-апп (uvicorn) пишет tz в PG в
# другом процессе, поэтому бот перечитывает PG после _TZ_TTL секунд. В своём
# процессе `set_user_location` обновляет кеш мгновенно.
_TZ_TTL = 60.0
_tz_offsets: Dict[int, int] = {}      # uid -> offset (int); читают и sync-хелперы
_tz_cached_at: Dict[int, float] = {}  # uid -> monotonic ts последнего чтения PG


def invalidate_tz_cache(uid: Optional[int] = None) -> None:
    """Сбросить кеш tz для uid (или весь, если uid=None)."""
    if uid is None:
        _tz_offsets.clear()
        _tz_cached_at.clear()
    else:
        _tz_offsets.pop(uid, None)
        _tz_cached_at.pop(uid, None)


def _cache_offset(uid: int, offset: int) -> None:
    _tz_offsets[uid] = offset
    _tz_cached_at[uid] = time.monotonic()


async def get_user_tz(tg_id: int) -> int:
    """ЕДИНСТВЕННЫЙ reader tz. TTL-кеш + чтение ключа `tz_{tg_id}` из памяти.
    Дефолт 3 (МСК), если ничего не сохранено / не парсится."""
    ts = _tz_cached_at.get(tg_id)
    if ts is not None and tg_id in _tz_offsets and (time.monotonic() - ts) < _TZ_TTL:
        return _tz_offsets[tg_id]
    mems = await PgMemoryRepo().find_by_exact_key(f"tz_{tg_id}")
    stored = mems[0].fact if mems else None
    if stored:
        try:
            offset = int(stored)
            _cache_offset(tg_id, offset)
            return offset
        except (ValueError, TypeError):
            pass
    return 3


async def set_user_location(
    tg_id: int,
    *,
    offset: Optional[int] = None,
    city: Optional[str] = None,
    user_notion_id: str = "",
) -> Optional[int]:
    """ЕДИНСТВЕННЫЙ writer локации. Пишет `tz_` и/или `city_` СИНХРОННО +
    обновляет кеш. `offset=None` → tz_ НЕ трогаем (город вне справочника —
    graceful, погода всё равно получит city_). Возвращает записанный offset."""
    repo = PgMemoryRepo()
    if offset is not None:
        await repo.upsert(
            fact=str(offset), key=f"tz_{tg_id}", category="Настройки",
            scope="global", source="auto", user_notion_id=user_notion_id,
        )
        _cache_offset(tg_id, offset)  # свой процесс — обновляем кеш сразу
    if city:
        await repo.upsert(
            fact=city, key=f"city_{tg_id}", category="⭐ Предпочтения",
            scope="nexus", source="auto", user_notion_id=user_notion_id,
        )
    return offset
