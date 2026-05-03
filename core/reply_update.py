"""core/reply_update.py — парсеры reply-дополнений и обновление Notion.

Когда юзер делает reply на сообщение бота, мы достаём page_id из
message_pages и применяем правки к Notion-странице. Парсер для
каждого типа свой — возвращает набор полей-обновлений.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.claude_client import ask_claude
from core.notion_client import (
    _multi_select,
    _number,
    _relation,
    _select,
    _text,
    match_select,
    update_page,
)

logger = logging.getLogger("core.reply_update")


# ────────────────────────── Промпты парсинга по типу ─────────────────────────

_RITUAL_REPLY_SYSTEM = (
    "Ты парсишь дополнение к уже записанному ритуалу. Извлеки ТОЛЬКО то, "
    "что явно упомянуто в тексте. Ответь JSON без markdown:\n"
    '{"forces": "силы или null", '
    '"structure": "последовательность/структура или null", '
    '"consumables": "расходники или null", '
    '"offerings": "подношения или null", '
    '"notes": "заметки или null", '
    '"duration_min": число или null}\n'
    "Если поле не упоминается — ставь null. Не додумывай."
)

_SESSION_REPLY_SYSTEM = (
    "Ты парсишь дополнение к уже записанному раскладу таро. Извлеки ТОЛЬКО то, "
    "что явно упомянуто. Ответь JSON без markdown:\n"
    '{"client_name": "имя клиента если расклад привязывают к нему или null", '
    '"question": "новый/уточнённый вопрос или null", '
    '"area": "Отношения|Финансы|Работа|Здоровье|Род|Общая ситуация или null", '
    '"notes": "заметки или null"}\n\n'
    "Примеры client_name:\n"
    "- 'это для Маши' → client_name: 'Маша'\n"
    "- 'расклад Вадиму' → client_name: 'Вадим'\n"
    "- 'привязать к Ане' → client_name: 'Аня'\n"
    "- 'сделал личный' → client_name: null (это не имя)\n\n"
    "Если поле не упоминается — null."
)

_WORK_REPLY_SYSTEM = (
    "Ты парсишь дополнение к записанной работе/задаче. Ответь JSON без markdown:\n"
    '{"category": "расклад|ритуал|соцсети|расходники|обучение|прочее или null", '
    '"deadline": "YYYY-MM-DD HH:MM или null", '
    '"priority": "срочно|важно|можно потом или null", '
    '"notes": "заметки или null"}\n'
    "Если поле не упомянуто — null."
)

_TASK_REPLY_SYSTEM = (
    "Ты парсишь дополнение к записанной задаче Nexus. Ответь JSON без markdown:\n"
    '{"deadline": "YYYY-MM-DD HH:MM или null", '
    '"category": "строка или null", '
    '"priority": "срочно|важно|можно потом или null"}\n'
    "Если поле не упомянуто — null."
)

_CLIENT_REPLY_SYSTEM = (
    "Ты парсишь reply Кай на сообщение бота про клиента. Ответь JSON без markdown:\n"
    '{"contact": "контакт или null", '
    '"request": "запрос/тема или null", '
    '"notes": "заметки или null", '
    '"new_type": "Платный" | "Бесплатный" | "Self" | null, '
    '"new_name": "новое имя или null"}\n\n'
    "Правила:\n"
    "- '🤝', 'платный', 'платной', 'в платный', 'paid' → new_type='Платный'\n"
    "- '🎁', 'бесплатно', 'бесплатной/бесплатным', 'в подарок', 'без оплаты', 'free' → new_type='Бесплатный'\n"
    "- '🌟', 'self', 'это я', 'себе/себя/моё', 'личный', \"she's a self\" → new_type='Self'\n"
    "- 'переименуй в X', 'имя X', 'назови X' → new_name='X'\n"
    "- 'добавь заметку Y', 'заметка: Y' → notes='Y'\n"
    "Если поле не упомянуто — null."
)


_TYPE_TO_SYSTEM = {
    "ritual":  _RITUAL_REPLY_SYSTEM,
    "session": _SESSION_REPLY_SYSTEM,
    "work":    _WORK_REPLY_SYSTEM,
    "task":    _TASK_REPLY_SYSTEM,
    "client":  _CLIENT_REPLY_SYSTEM,
}


# ────────────────────────── Маппинги полей Notion ────────────────────────────

_RITUAL_FIELDS = {
    "forces":       ("Силы",             "text"),
    "structure":    ("Структура",        "text"),
    "consumables":  ("Расходники",       "text"),
    "offerings":    ("Подношения/Откуп", "text"),
    "notes":        ("Заметки",          "text"),
    "duration_min": ("Время (мин)",      "number"),
}

_SESSION_FIELDS = {
    "question": ("Тема",    "title"),
    "area":     ("Область", "multi_select"),
    "notes":    ("Трактовка_append", "append_text"),
}

_WORK_FIELDS = {
    "category": ("Категория", "select"),
    "deadline": ("Дедлайн",   "date"),
    "priority": ("Приоритет", "select"),
}

_TASK_FIELDS = {
    "deadline": ("Дедлайн",   "date"),
    "category": ("Категория", "select"),
    "priority": ("Приоритет", "select"),
}

_CLIENT_FIELDS = {
    "contact": ("Контакт_append", "append_text"),
    "request": ("Запрос_append",  "append_text"),
    "notes":   ("Заметки_append", "append_text"),
    "new_type": ("Тип клиента",   "client_type"),
    "new_name": ("Имя",           "title"),
}

_TYPE_TO_FIELDS = {
    "ritual":  _RITUAL_FIELDS,
    "session": _SESSION_FIELDS,
    "work":    _WORK_FIELDS,
    "task":    _TASK_FIELDS,
    "client":  _CLIENT_FIELDS,
}


_WORK_CATEGORY_MAP = {
    "расклад":    "🃏 Расклад",
    "ритуал":     "✨ Ритуал",
    "соцсети":    "📱 Соцсети",
    "расходники": "🛒 Расходники",
    "обучение":   "📚 Обучение",
    "прочее":     "🗂️ Прочее",
}

_PRIORITY_MAP = {
    "срочно":      "Срочно",
    "важно":       "Важно",
    "можно потом": "Можно потом",
}


# ────────────────────────── Основной API ─────────────────────────────────────


def _parse_json_safe(raw: str) -> Optional[dict]:
    try:
        clean = (
            raw.strip()
            .removeprefix("```json").removeprefix("```")
            .removesuffix("```").strip()
        )
        return json.loads(clean)
    except Exception:
        return None


def _coerce_date(val: str) -> str:
    """'YYYY-MM-DD HH:MM' → 'YYYY-MM-DDTHH:MM'."""
    if not val:
        return ""
    return val.replace(" ", "T") if " " in val else val


async def parse_reply(page_type: str, reply_text: str) -> Dict[str, Any]:
    """Спарсить reply-текст для данного типа. Вернёт dict с ненулевыми полями."""
    system = _TYPE_TO_SYSTEM.get(page_type)
    if not system:
        return {}
    raw = await ask_claude(reply_text, system=system, max_tokens=300,
                           model="claude-haiku-4-5-20251001")
    data = _parse_json_safe(raw) or {}
    # Убрать null/пустые
    return {k: v for k, v in data.items() if v not in (None, "", [])}


async def apply_updates(
    page_id: str,
    page_type: str,
    db_id: Optional[str],
    updates: Dict[str, Any],
    user_notion_id: str = "",
) -> Dict[str, Any]:
    """Сформировать Notion props и отправить update_page.

    Возвращает dict {human_field_name: value} для подтверждения юзеру.
    Для полей '*_append' — дописывает к существующему тексту через
    отдельный запрос.
    """
    from core.notion_client import _notion

    fields_map = _TYPE_TO_FIELDS.get(page_type, {})
    props: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}
    append_tasks: list = []  # [(notion_field, new_text)]

    # Особый случай: session + client_name → relation + Тип сеанса=Клиентский
    if page_type == "session" and updates.get("client_name"):
        client_name = str(updates.pop("client_name")).strip()
        if client_name:
            from core.notion_client import client_find
            try:
                client = await client_find(client_name, user_notion_id=user_notion_id)
            except Exception as e:
                logger.warning("client_find in reply failed: %s", e)
                client = None
            if client:
                props["👥 Клиенты"] = _relation(client["id"])
                applied["👥 Клиенты"] = client_name
                if db_id:
                    real_type = await match_select(db_id, "Тип сеанса", "🤝 Клиентский")
                    props["Тип сеанса"] = _select(real_type)
                    applied["Тип сеанса"] = real_type
                else:
                    props["Тип сеанса"] = _select("🤝 Клиентский")
                    applied["Тип сеанса"] = "🤝 Клиентский"
            else:
                applied["Клиент не найден"] = client_name

    for key, value in updates.items():
        mapping = fields_map.get(key)
        if not mapping:
            continue
        notion_field, field_type = mapping

        if field_type == "text":
            props[notion_field] = _text(str(value))
            applied[notion_field] = value

        elif field_type == "title":
            from core.notion_client import _title
            props[notion_field] = _title(str(value))
            applied[notion_field] = value

        elif field_type == "number":
            try:
                props[notion_field] = _number(float(value))
                applied[notion_field] = value
            except (TypeError, ValueError):
                pass

        elif field_type == "date":
            iso = _coerce_date(str(value))
            if iso:
                props[notion_field] = {"date": {"start": iso}}
                applied[notion_field] = iso

        elif field_type == "select":
            # Применить тип-специфичные маппинги
            mapped = value
            if page_type in ("work", "task") and key == "category":
                mapped = _WORK_CATEGORY_MAP.get(str(value).lower(), value)
            if key == "priority":
                mapped = _PRIORITY_MAP.get(str(value).lower(), value)
            real = mapped
            if db_id:
                real = await match_select(db_id, notion_field, str(mapped))
            props[notion_field] = _select(real)
            applied[notion_field] = real

        elif field_type == "multi_select":
            real = value
            if db_id:
                real = await match_select(db_id, notion_field, str(value))
            props[notion_field] = _multi_select([real])
            applied[notion_field] = real

        elif field_type == "client_type":
            from core.notion_client import (
                CLIENT_TYPE_PAID, CLIENT_TYPE_FREE, CLIENT_TYPE_SELF,
                _SELF_CLIENT_CACHE,
            )
            t = str(value).strip().lower()
            if "self" in t or "🌟" in t:
                canonical = CLIENT_TYPE_SELF
            elif "беспл" in t or "🎁" in t or "free" in t:
                canonical = CLIENT_TYPE_FREE
            else:
                canonical = CLIENT_TYPE_PAID
            props[notion_field] = _select(canonical)
            applied[notion_field] = canonical
            # Self-кеш мог измениться — сбросить.
            _SELF_CLIENT_CACHE.clear()

        elif field_type == "append_text":
            # Разбираем псевдо-имя 'Foo_append'
            real_field = notion_field.replace("_append", "")
            append_tasks.append((real_field, str(value)))
            applied[real_field] = f"+ {value}"

    # Первый батч — обычные set-props
    if props:
        await update_page(page_id, props)

    # Append-tasks: для каждого текстового поля читаем текущее содержимое
    # и дописываем через перевод строки
    if append_tasks:
        notion = _notion()
        page = await notion._client.pages.retrieve(page_id=page_id)
        existing_props = page.get("properties", {})
        append_props: Dict[str, Any] = {}
        for field, new_text in append_tasks:
            cur_prop = existing_props.get(field) or {}
            cur_items = cur_prop.get("rich_text") or cur_prop.get("title") or []
            cur_text = "".join(
                (it.get("text", {}) or {}).get("content", "")
                for it in cur_items
            )
            combined = (cur_text + "\n" + new_text).strip() if cur_text else new_text
            if cur_prop.get("type") == "title":
                from core.notion_client import _title
                append_props[field] = _title(combined[:2000])
            else:
                append_props[field] = _text(combined[:2000])
        if append_props:
            await update_page(page_id, append_props)

    return applied


async def format_applied(applied: Dict[str, Any]) -> str:
    """Сформировать человекочитаемую сводку обновлений."""
    if not applied:
        return "ничего не распознала"
    lines = []
    for field, value in applied.items():
        val_str = str(value)
        if len(val_str) > 60:
            val_str = val_str[:60] + "…"
        lines.append(f"  • {field}: {val_str}")
    return "\n".join(lines)


def get_db_id_for_type(page_type: str) -> Optional[str]:
    """Вернуть database_id для данного типа (для match_select)."""
    from core.config import config
    return {
        "ritual":  config.arcana.db_rituals,
        "session": config.arcana.db_sessions,
        "work":    getattr(config.arcana, "db_works", None),
        "client":  config.arcana.db_clients,
        "task":    getattr(config.nexus, "db_tasks", None),
    }.get(page_type)
