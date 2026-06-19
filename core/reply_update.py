"""core/reply_update.py — парсеры reply-дополнений и обновление записей в PG.

Когда юзер делает reply на сообщение бота, мы достаём page_id (PG id) из
message_pages и применяем правки через per-domain PG-репозитории
(`set_props`). Парсер для каждого типа свой — возвращает набор
полей-обновлений (Haiku). Notion больше не используется (#156).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.claude_client import ask_claude

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
                           model="claude-haiku-4-5-20251001", temperature=0)
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
    """Применить reply-правки к записи через per-domain PG `set_props`.

    Диспатч по `page_type` (task/client/session/ritual/work). Notion не
    используется — каждый PG-репозиторий сам резолвит select/FK и сам
    дописывает append-поля (read-modify-write внутри `set_props`).
    `db_id` игнорируется (PG-репозитории не нуждаются в database_id).
    Возвращает dict {human_field_name: value} для подтверждения юзеру.
    """
    if not updates:
        return {}
    if page_type == "task":
        return await _apply_task(page_id, updates)
    if page_type == "client":
        return await _apply_client(page_id, updates)
    if page_type == "session":
        return await _apply_session(page_id, updates, user_notion_id)
    if page_type == "ritual":
        return await _apply_ritual(page_id, updates)
    if page_type == "work":
        return await _apply_work(page_id, updates)
    return {}


# ── inline prop-builders (Notion-format dict для PgTasksRepo.set_props) ────────

def _p_select(name: Any) -> Dict[str, Any]:
    return {"select": {"name": str(name)}}


def _p_date(iso: str) -> Dict[str, Any]:
    return {"date": {"start": iso}}


async def _apply_task(page_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """✅ Задачи Nexus → PgTasksRepo.set_props (Notion-format props, PG резолвит)."""
    props: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}
    if updates.get("deadline"):
        iso = _coerce_date(str(updates["deadline"]))
        if iso:
            props["Дедлайн"] = _p_date(iso)
            applied["Дедлайн"] = iso
    if updates.get("category"):
        props["Категория"] = _p_select(updates["category"])
        applied["Категория"] = updates["category"]
    if updates.get("priority"):
        pr = _PRIORITY_MAP.get(str(updates["priority"]).lower(), updates["priority"])
        props["Приоритет"] = _p_select(pr)
        applied["Приоритет"] = pr
    if props:
        from nexus.repos.pg_tasks_repo import PgTasksRepo
        await PgTasksRepo().set_props(page_id, props)
    return applied


async def _apply_client(page_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """👥 Клиенты → PgClientsRepo.update_profile. contact/request/notes —
    append (read-modify-write на PG-колонке)."""
    from arcana.repos.pg_clients_repo import PgClientsRepo
    repo = PgClientsRepo()
    applied: Dict[str, Any] = {}
    kw: Dict[str, Any] = {}

    nt = updates.get("new_type")
    if nt:
        t = str(nt).strip().lower()
        if "self" in t or "🌟" in t:
            code = "self"
        elif "беспл" in t or "🎁" in t or "free" in t:
            code = "free"
        else:
            code = "paid"
        kw["type_code"] = code
        applied["Тип клиента"] = nt
        # Self-кеш в notion_client (PG-shim) мог измениться — сбросить best-effort.
        try:
            from core.client_resolve import _SELF_CLIENT_CACHE
            _SELF_CLIENT_CACHE.clear()
        except Exception:
            pass

    if updates.get("new_name"):
        kw["name"] = str(updates["new_name"])
        applied["Имя"] = updates["new_name"]

    append_keys = [k for k in ("contact", "request", "notes") if updates.get(k)]
    if append_keys:
        try:
            cur = await repo.find_by_id(int(page_id))
        except (ValueError, TypeError):
            cur = None
        for k in append_keys:
            existing = (getattr(cur, k, "") if cur else "") or ""
            new_part = str(updates[k])
            kw[k] = (existing + "\n" + new_part).strip() if existing else new_part
            applied[k] = f"+ {updates[k]}"

    if kw:
        try:
            await repo.update_profile(int(page_id), **kw)
        except (ValueError, TypeError):
            pass
    return applied


async def _apply_session(
    page_id: str, updates: Dict[str, Any], user_notion_id: str,
) -> Dict[str, Any]:
    """🃏 Расклады → PgSessionsRepo.set_props. client_name → find_or_create_client
    (PG) → client_id + type='client'. notes → дописать Трактовку."""
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    fields: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}

    if updates.get("question"):
        fields["question"] = updates["question"]
        applied["Тема"] = updates["question"]
    if updates.get("area"):
        fields["area"] = updates["area"]
        applied["Область"] = updates["area"]
    if updates.get("notes"):
        fields["append_interpretation"] = updates["notes"]
        applied["Трактовка"] = f"+ {updates['notes']}"

    cn = updates.get("client_name")
    if cn:
        client_name = str(cn).strip()
        # fail-closed: привязка клиента требует юзера (find_or_create в его БД).
        if client_name and user_notion_id:
            try:
                from core.client_resolve import find_or_create_client
                client_id, _ = await find_or_create_client(
                    client_name, user_notion_id=user_notion_id,
                )
            except Exception as e:
                logger.warning("find_or_create_client in reply failed: %s", e)
                client_id = None
            if client_id:
                fields["client_id"] = client_id
                fields["type_code"] = "client"
                applied["Клиент"] = client_name
            else:
                applied["Клиент не найден"] = client_name

    if fields:
        await PgSessionsRepo().set_props(page_id, **fields)
    return applied


async def _apply_ritual(page_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """🕯 Ритуалы → PgRitualsRepo.set_props."""
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    fields: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}
    for key, label in (
        ("forces", "Силы"), ("structure", "Структура"),
        ("consumables", "Расходники"), ("offerings", "Подношения/Откуп"),
        ("notes", "Заметки"),
    ):
        if updates.get(key):
            fields[key] = updates[key]
            applied[label] = updates[key]
    if updates.get("duration_min") is not None:
        fields["duration_min"] = updates["duration_min"]
        applied["Время (мин)"] = updates["duration_min"]
    if fields:
        await PgRitualsRepo().set_props(page_id, **fields)
    return applied


async def _apply_work(page_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """🔮 Работы → PgWorksRepo.set_props."""
    from arcana.repos.pg_works_repo import PgWorksRepo
    fields: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}
    if updates.get("category"):
        cat = _WORK_CATEGORY_MAP.get(str(updates["category"]).lower(), updates["category"])
        fields["category"] = cat
        applied["Категория"] = cat
    if updates.get("priority"):
        fields["priority"] = updates["priority"]
        applied["Приоритет"] = _PRIORITY_MAP.get(
            str(updates["priority"]).lower(), updates["priority"],
        )
    if updates.get("deadline"):
        fields["deadline"] = _coerce_date(str(updates["deadline"]))
        applied["Дедлайн"] = fields["deadline"]
    if fields:
        await PgWorksRepo().set_props(page_id, **fields)
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
    """Deprecated (#156): db_id больше не нужен — PG-репозитории резолвят
    select/FK сами. Оставлен для совместимости сигнатуры хендлеров; всегда None.
    """
    return None
