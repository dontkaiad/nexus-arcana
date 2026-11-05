"""core/memory_routing.py — «Запомни …»: чей это факт (я / известный человек / тёзки / неизвестный / Разное).

Чистая логика маршрута без записи; запись делает core/memory.py. LLM здесь нет: имя человека даёт парсер (`связь`),
«про себя» определяется по лицу текста (я/мне/у меня/глагол 1 л.), «мы/у нас» — Разное (это не про Кай одну).
  keep    — путь как раньше (бюджет, фигуранты, коты, один известный человек, «просто факт»);
  self    — факт про Кай → карточка «Я», категория из SELF_CATEGORIES;
  choose  — несколько человек с таким именем → кнопки (подпись «Имя — друг, пояснение»);
  ask     — имени нет в карточках → «Не знаю X. Создать карточку?» [Да/Нет];
  misc    — «Разное» (person_id пуст), сказали «мы/у нас».
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core.memory_subjects import (
    CATS_CATEGORY, FALLBACK_CATEGORY, SELF_CATEGORIES, SYSTEM_CATEGORIES, SYSTEM_KEY_PREFIXES, keyword_category,
)
from core.memory_subjects import _O1, _S1, _verb1       # лицо текста: тот же детектор, что в атрибуции по цитате
from core.people import RELATIONS, person_label

PEOPLE_CATEGORY = "👥 Люди"
_WE = re.compile(r"\b(?:мы|нас|нам|нами|наш[аеиу]?|наши[хм]?|у нас)\b", re.I)
_SELF_WORDS = {"я", "меня", "мне", "мной", "себя", "себе", "кай", "я сама"}
_ASK_KIND = "mem_route"


def first_person(text: str) -> bool:
    t = text or ""
    return bool(_S1.search(t) or _O1.search(t) or _verb1(t))


def self_category(category: str, fact: str, cat_names: List[str] = None) -> str:
    """Категория факта «Я»: своя из SELF_CATEGORIES остаётся (🦋 СДВГ читает /adhd), чужая (💡 Инсайт, 👥 Люди) — по словам или 🗂 Прочее."""
    if category in SELF_CATEGORIES and category != "❓ Проверить автора":
        return category
    return keyword_category(fact, cat_names or []) or FALLBACK_CATEGORY


def route_subject(text: str, category: str, link: str, key: str, user_id: str) -> Dict[str, Any]:
    """→ {"to": keep|self|choose|ask|misc, ...}. Читает только people (resolve), ничего не пишет."""
    if category in SYSTEM_CATEGORIES or (key or "").startswith(SYSTEM_KEY_PREFIXES) or category == CATS_CATEGORY:
        return {"to": "keep"}
    if _WE.search(text or ""):
        return {"to": "misc"}
    link = (link or "").strip()
    if category == PEOPLE_CATEGORY and link:
        if link.lower() in _SELF_WORDS:
            return {"to": "self"}
        from core.repos import pg_people_repo as prepo
        found = prepo.resolve(user_id, [link])
        if len(found) == 1:
            return {"to": "keep"}                                  # один человек — привязка как раньше (_single_person)
        if len(found) > 1:
            return {"to": "choose", "candidates": [
                {"id": p["id"], "label": person_label(p["name"], p.get("relation"), p.get("about"))} for p in found]}
        return {"to": "ask", "name": link}
    if not link and first_person(text):
        return {"to": "self"}
    return {"to": "keep"}


def ask_payload(kind: str, route: Dict[str, Any], fact: str, category: str, key: str, link: str, scope: str) -> Dict[str, Any]:
    """Состояние диалога «кого ты имела в виду / создать карточку»: кладётся в pending_kv (бот) или возвращается клиенту (Mini App)."""
    return {"kind": kind, "fact": fact, "category": category, "key": key, "link": link, "scope": scope,
            "candidates": route.get("candidates") or [], "name": route.get("name") or ""}


def clean_relation(rel: str):
    return rel if rel in RELATIONS else None
