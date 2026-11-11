"""core/sitter_cat_care.py — паспорта котов для ситтера: GET /api/sitter/cat-care (читающий токен ситтера).

Что получает ситтер: карточки котов (имя, фото, актуальные факты четырёх типов), «Общее про котов» (список текстов), ДЕЙСТВУЮЩИЕ лекарства (без скрытых владельцем), а вес и визиты
ТОЛЬКО те, что владелец открыл флагом `sitter_visible`. Минимальный набор полей: без id фактов, кличек, заметок, клиник, следующих сроков, id задач,
статусов курса и любых денег (их в данных нет). Даты и «дней до» считает Nexus в часовом поясе владельца; приложение cats ничего не пересчитывает.
Окно доступа ситтера (даты stay) проверяет приложение cats, Nexus его не знает: токен читающий и узкий, как у /api/sitter/lists.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List

from core.cats import group_facts, safe_photo_url
from core.repos import pg_cat_health_repo as health
from core.repos import pg_cats_repo as cats_repo

FACT_LIMITS = {"important": 8, "other": 8, "likes": 6, "dislikes": 6}
GENERAL_LIMIT = 20
logger = logging.getLogger(__name__)
FACT_TEXT = 300


def _clip(text: str) -> str:
    return text if len(text) <= FACT_TEXT else text[: FACT_TEXT - 1].rstrip() + "…"


def _cats_payload(user_id: str) -> List[Dict[str, Any]]:
    rows = [c for c in cats_repo.list_cats(user_id) if c["status"] == "confirmed"]
    by_cat = cats_repo.list_facts_many(user_id, [c["id"] for c in rows])
    out = []
    for c in rows:
        lists: Dict[str, List[str]] = {k: [] for k in FACT_LIMITS}
        for g in group_facts(by_cat.get(c["id"], []), c["name"], c.get("aliases")):
            if g["type"] in lists:
                lists[g["type"]] = [_clip(i["text"]) for i in g["items"] if i.get("is_current", True) and i["text"]][: FACT_LIMITS[g["type"]]]
        out.append({"id": c["id"], "name": c["name"], "photo_url": safe_photo_url(c.get("photo_url")), "facts": lists})
    return out


def payload(user_id: str, today: date) -> Dict[str, Any]:
    cats = _cats_payload(user_id)
    shown = {c["id"] for c in cats}                              # only cats that have a card: a pending or removed cat has no health here either
    meds = [m for m in health.current_meds(user_id, today, for_sitter=True) if m["cat_id"] in shown]
    all_general = cats_repo.list_general_facts_for_sitter(user_id)
    general = [_clip(f["text"]) for f in all_general[:GENERAL_LIMIT]]
    if len(all_general) > GENERAL_LIMIT:                         # the sitter would silently miss the rest: say how many (a number only, never the texts)
        logger.warning("sitter cat-care: %d general fact(s) cut by the limit of %d", len(all_general) - GENERAL_LIMIT, GENERAL_LIMIT)
    return {
        "today": today.isoformat(),
        "cats": cats,
        "general": general,                                      # «Общее про котов»: только тексты, без id и привязок
        "meds": [{"cat_id": m["cat_id"], "name": m["name"], "dose": m["dose"], "times_per_day": m["times_per_day"],
                  "ends_on": m["ends_on"].isoformat() if m["ends_on"] else None,
                  "days_left": (m["ends_on"] - today).days if m["ends_on"] else None} for m in meds],
        "weights": [{"cat_id": w["cat_id"], "weighed_on": w["weighed_on"].isoformat(), "grams": w["grams"]} for w in health.sitter_weights(user_id)],
        "visits": [{"cat_id": v["cat_id"], "kind": v["kind"], "title": v["title"], "visit_on": v["visit_on"].isoformat(), "status": v["status"],
                    "days_until": (v["visit_on"] - today).days} for v in health.sitter_visits(user_id, today)],
    }


async def build(user_id: str, owner_tg: int) -> Dict[str, Any]:
    from core import cat_health as svc
    today, _tz = await svc._today_tz(owner_tg)
    return await asyncio.to_thread(payload, user_id, today)
