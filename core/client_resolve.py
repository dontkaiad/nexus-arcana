"""core/client_resolve.py — общий хелпер для всех handler'ов Аркана.

Объединяет find_or_create_client + announce-сообщение «🆕 Создала клиента»
с reply-mapping (чтобы Кай могла ответить «🌟»/«бесплатный» и сменить
тип уже созданного клиента через стандартный reply_update flow).

Используется в:
- arcana/handlers/sessions.py (single + multi flow)
- arcana/handlers/rituals.py
- arcana/handlers/work_preview.py
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram.types import Message

from core.message_pages import save_message_page

logger = logging.getLogger("core.client_resolve")

# ─── Client type labels + PG-backed client shims ──────────────────────────────
# Релокейт из notion_client (Notion-removal). Эти функции читают/пишут PG
# (PgClientsRepo) — чистые, не Notion. CLIENT_TYPE_* — display-метки типов.

CLIENT_TYPE_SELF = "🌟 Self"
CLIENT_TYPE_PAID = "🤝 Платный"
CLIENT_TYPE_FREE = "🎁 Бесплатный"

# In-memory cache for self-client pg_id per user (process-local, short-lived).
_SELF_CLIENT_CACHE: dict = {}


def should_skip_payment(client_type: Optional[str]) -> bool:
    """Self или Бесплатный → не показываем кнопки оплаты после расклада/ритуала."""
    return (client_type or "") in (CLIENT_TYPE_SELF, CLIENT_TYPE_FREE)


async def find_or_create_client(
    name: str,
    *,
    user_notion_id: str = "",
    default_type: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """Находит клиента по имени в PG; если нет — создаёт там же.

    Возвращает (str(pg_id), created). str(pg_id) используется как client_id
    во всех arcana-доменах. None означает ошибку создания.
    """
    from arcana.repos.pg_clients_repo import PgClientsRepo as _PGC
    _type_map = {
        CLIENT_TYPE_PAID: "paid",
        CLIENT_TYPE_FREE: "free",
        "🌟 Self":         "self",
        "Платный":         "paid",
        "Бесплатный":      "free",
    }
    type_code = _type_map.get(default_type or CLIENT_TYPE_PAID, "paid")
    try:
        repo = _PGC()
        existing = await repo.find(name)
        if existing:
            return existing.id, False
        pg_id = await repo.create(
            name=name,
            type_code=type_code,
            user_notion_id=user_notion_id or None,
        )
        if pg_id:
            try:
                from core.preprocess import invalidate_whitelist
                invalidate_whitelist(user_notion_id)
            except Exception:
                pass
            return str(pg_id), True
        return None, False
    except Exception as e:
        logger.warning("find_or_create_client: failed for %r: %s", name, e)
        return None, False


async def client_find(name: str, user_notion_id: str = "") -> Optional[dict]:
    """PG-backed поиск клиента по имени. Возвращает {'id': str, 'name': str} или None."""
    try:
        from arcana.repos.pg_clients_repo import PgClientsRepo
        client = await PgClientsRepo().find(name)
        if client is None:
            return None
        return {"id": client.id, "name": client.name}
    except Exception as e:
        logger.warning("client_find: PG lookup failed for %r: %s", name, e)
        return None


async def resolve_self_client(user_notion_id: str = "") -> Optional[str]:
    """Найти self-клиента в PG. Возвращает str(pg_id) или None."""
    cache_key = user_notion_id or "_default_"
    cached = _SELF_CLIENT_CACHE.get(cache_key)
    if cached:
        return cached
    from arcana.repos.pg_clients_repo import PgClientsRepo as _PGC
    try:
        c = await _PGC().find_self(user_notion_id=user_notion_id)
        if c:
            _SELF_CLIENT_CACHE[cache_key] = c.id
            return c.id
        logger.warning(
            "resolve_self_client: self-клиент не найден в PG — "
            "создай клиента с type_code=self"
        )
        return None
    except Exception as e:
        logger.warning("resolve_self_client failed: %s", e)
        return None


async def client_get_type(client_pg_id: str) -> Optional[str]:
    """Display-label типа клиента («🌟 Self»/«🎁 Бесплатный»/«🤝 Платный») по PG id.
    Используется в should_skip_payment."""
    from arcana.repos.clients_tables import clients as _t_clients, client_type as _t_ctype
    from core.db import get_engine as _engine
    from sqlalchemy import select as _select
    try:
        pg_id = int(client_pg_id)
    except (ValueError, TypeError):
        return None
    try:
        import asyncio

        def _sync():
            with _engine().connect() as conn:
                row = conn.execute(
                    _select(_t_ctype.c.emoji, _t_ctype.c.label)
                    .join(_t_clients, _t_clients.c.type_id == _t_ctype.c.id)
                    .where(_t_clients.c.id == pg_id)
                ).fetchone()
            if row is None:
                return None
            return f"{row.emoji} {row.label}"
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logger.warning("client_get_type failed for pg_id=%r: %s", client_pg_id, e)
        return None

_CTYPE_LABEL = {
    "🤝 Платный": "🤝 Платный",
    "🎁 Бесплатный": "🎁 Бесплатный",
    "🌟 Self": "🌟 Self",
}

# Подстроки (lower), сигнализирующие о рефузале LLM вместо реального имени.
REFUSAL_MARKERS = [
    "не могу", "не имею", "извлеч", "доступ", "предостав",
    "пожалуйста", "не указан", "не определ", "unknown", "n/a",
]


def is_valid_client_name(name: str) -> bool:
    """Возвращает True только если name похоже на реальное имя клиента.

    Отсекает рефузал-строки LLM, предложения и мусор:
    - пусто после strip
    - длина > 40 символов
    - больше 3 слов
    - содержит .!? (признак предложения, не имени)
    - нет ни одной буквы
    - содержит любой маркер из REFUSAL_MARKERS (поиск подстроки, lower)
    """
    if not name:
        return False
    s = name.strip()
    if not s:
        return False
    if len(s) > 40:
        return False
    if len(s.split()) > 3:
        return False
    if any(c in s for c in ".!?"):
        return False
    if not any(c.isalpha() for c in s):
        return False
    lower = s.lower()
    for marker in REFUSAL_MARKERS:
        if marker in lower:
            return False
    return True


async def resolve_or_create(
    message: Message,
    name: str,
    *,
    user_notion_id: str = "",
    default_type: str = "🤝 Платный",
    announce: bool = True,
) -> Optional[str]:
    """Находит клиента по имени; если нет — создаёт + анонсирует Кай.

    Возвращает client_id или None при ошибке создания (caller должен решить
    что делать — обычно «не падать, продолжить как is_personal=False
    с client_id=None»).

    announce=True (default): шлёт «🆕 Создала клиента {name} (🤝 Платный) ·
    реплай чтобы сменить тип» и регистрирует mapping для reply_update.
    """
    if not name:
        return None
    if not is_valid_client_name(name):
        logger.warning("invalid client name rejected: %r", name)
        return None
    cid, created = await find_or_create_client(
        name, user_notion_id=user_notion_id, default_type=default_type,
    )
    if not cid:
        return None
    if created and announce:
        label = _CTYPE_LABEL.get(default_type, default_type)
        try:
            sent = await message.answer(
                f"🆕 Создала клиента <b>{name}</b> ({label})\n"
                "<i>↩️ Реплай: «🌟», «🎁», «бесплатный» — сменить тип</i>",
                parse_mode="HTML",
            )
            await save_message_page(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                page_id=cid,
                page_type="client",
                bot="arcana",
            )
        except Exception as e:
            logger.warning("announce new client failed: %s", e)
    return cid
