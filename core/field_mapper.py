"""
FieldMapper: превращает dict от Claude → Notion properties payload.
Логика типов изолирована здесь. Бот и notion_client не знают о форматах Notion.
"""
from __future__ import annotations
from typing import Any
from .schema import FieldDef, SCHEMA_REGISTRY


# ──────────────────────────────────────────
# Форматтеры Notion-типов
# ──────────────────────────────────────────

def _fmt_title(v: Any) -> dict:
    return {"title": [{"text": {"content": str(v)}}]}

def _fmt_rich_text(v: Any) -> dict:
    return {"rich_text": [{"text": {"content": str(v)}}]}

def _fmt_number(v: Any) -> dict:
    return {"number": float(v)}

def _fmt_select(v: Any) -> dict:
    return {"select": {"name": str(v)}}

def _fmt_multi_select(v: Any) -> dict:
    items = v if isinstance(v, list) else [s.strip() for s in str(v).split(",")]
    return {"multi_select": [{"name": i} for i in items if i]}

def _fmt_date(v: Any) -> dict:
    return {"date": {"start": str(v)}}

def _fmt_checkbox(v: Any) -> dict:
    return {"checkbox": bool(v)}

def _fmt_url(v: Any) -> dict:
    return {"url": str(v)}

def _fmt_relation(v: Any) -> dict:
    # v — либо page_id строкой, либо список строк
    ids = v if isinstance(v, list) else [v]
    return {"relation": [{"id": i} for i in ids]}


_FORMATTERS = {
    "title":        _fmt_title,
    "rich_text":    _fmt_rich_text,
    "number":       _fmt_number,
    "select":       _fmt_select,
    "multi_select": _fmt_multi_select,
    "date":         _fmt_date,
    "checkbox":     _fmt_checkbox,
    "url":          _fmt_url,
    "relation":     _fmt_relation,
}


# ──────────────────────────────────────────
# Основная функция маппинга
# ──────────────────────────────────────────

def map_fields(
    bot: str,
    record_type: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """
    raw — dict от Claude с произвольными ключами.
    Возвращает Notion properties payload.

    Пример:
        raw = {"amount": 450, "category": "Транспорт", "desc": "такси"}
        → {"Описание": {...title...}, "Сумма": {...number...}, "Категория": {...select...}}
    """
    schema = SCHEMA_REGISTRY.get((bot, record_type))
    if schema is None:
        raise ValueError(f"Unknown schema: ({bot}, {record_type})")

    # Строим обратный индекс: alias → FieldDef
    alias_index: dict[str, FieldDef] = {}
    for fd in schema:
        for alias in fd.aliases:
            alias_index[alias] = fd
        # само notion_name тоже как alias
        alias_index[fd.notion_name.lower()] = fd

    properties: dict[str, Any] = {}
    matched_fields: set[str] = set()

    for raw_key, raw_val in raw.items():
        if raw_val is None:
            continue
        fd = alias_index.get(raw_key.lower())
        if fd is None:
            continue  # неизвестное поле — игнорируем
        if fd.notion_name in matched_fields:
            continue  # уже заполнено

        formatter = _FORMATTERS.get(fd.notion_type)
        if formatter is None:
            continue

        try:
            properties[fd.notion_name] = formatter(raw_val)
            matched_fields.add(fd.notion_name)
        except Exception:
            pass  # битое значение — пропускаем, не ломаем запись

    # Проверяем required
    for fd in schema:
        if fd.required and fd.notion_name not in matched_fields:
            raise ValueError(f"Required field missing: {fd.notion_name} (bot={bot}, type={record_type})")

    return properties
