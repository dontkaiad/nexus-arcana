"""core/props.py — чистые конструкторы Notion-формата props + ритуальные
dict-маппинги. БЕЗ обращений к Notion API.

Релокейт из notion_client (Notion-removal). Эти dict-конструкторы живут потому,
что PG-пути принимают props в Notion-форме и сами парсят их в PG-колонки
(PgTasksRepo.set_props, miniapp writes, lists_repo). Это чистые словари —
не вызовы API.
"""
from __future__ import annotations

from typing import List


# ─── Prop helpers ─────────────────────────────────────────────────────────────

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text or ""}}]}


def _text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text or ""}}]}


def _number(value: float) -> dict:
    return {"number": value}


def _select(name: str) -> dict:
    return {"select": {"name": name}}


def _status(name: str) -> dict:
    """Для полей типа Status (не Select)."""
    return {"status": {"name": name}}


def _multi_select(names: List[str]) -> dict:
    return {"multi_select": [{"name": n} for n in names]}


def _date(iso: str) -> dict:
    return {"date": {"start": iso}}


def _relation(page_id: str) -> dict:
    return {"relation": [{"id": page_id}]}


# ─── Ritual display maps (goal/place code → label) ────────────────────────────

_RITUAL_GOAL_MAP = {
    "привлечение": "🧲 Привлечение",
    "защита": "🛡️ Защита",
    "очищение": "🌊 Очищение",
    "любовь": "💕 Любовь",
    "финансы": "💰 Финансы",
    "деструктив": "💀 Деструктив",
    "развязка": "⚔️ Развязка",
    "приворот": "💘 Приворот",
    "другое": "🔮 Другое",
}

_RITUAL_PLACE_MAP = {
    "дома": "🏠 Дома",
    "лес": "🌲 Лес",
    "погост": "✝️ Погост",
    "перекрёсток": "🛤️ Перекрёсток",
    "церковь": "⛪ Церковь",
    "водоём": "🌊 Водоём",
    "поле": "🌾 Поле",
    "другое": "📍 Другое",
}
