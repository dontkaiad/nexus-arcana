"""
record_service.py — единственная точка входа для ботов.

КОНТРАКТ (не нарушать никогда):
- Не знает о конкретных полях (date, amount, category...)
- Не знает о Telegram, о текстах ответов, о Claude
- Единственная ответственность: schema → mapper → Notion

Боту достаточно знать:
    page_id = await create_record("nexus", "expense", raw_dict)
"""
from __future__ import annotations
import logging
import os
from .field_mapper import map_fields
from .notion_client import NotionClient

logger = logging.getLogger(__name__)

# DB_MAP: (bot, record_type) → имя переменной окружения с database_id
_DB_ENV: dict[tuple[str, str], str] = {
    ("nexus",  "expense"): "NOTION_DB_NEXUS_EXPENSES",
    ("nexus",  "income"):  "NOTION_DB_NEXUS_INCOME",
    ("nexus",  "task"):    "NOTION_DB_NEXUS_TASKS",
    ("nexus",  "note"):    "NOTION_DB_NEXUS_NOTES",
    ("arcana", "session"): "NOTION_DB_ARCANA_SESSIONS",
    ("arcana", "ritual"):  "NOTION_DB_ARCANA_RITUALS",
    ("arcana", "client"):  "NOTION_DB_ARCANA_CLIENTS",
}


def _get_db_id(bot: str, record_type: str) -> str:
    env_key = _DB_ENV.get((bot, record_type))
    if not env_key:
        raise ValueError(f"No DB mapping for ({bot!r}, {record_type!r})")
    db_id = os.environ.get(env_key)
    if not db_id:
        raise EnvironmentError(f"Env var not set: {env_key}")
    return db_id


def _make_client() -> NotionClient:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise EnvironmentError("Env var not set: NOTION_TOKEN")
    return NotionClient(token)


async def create_record(bot: str, record_type: str, raw: dict) -> str:
    """
    Создаёт запись в Notion.

    Args:
        bot:         "nexus" | "arcana"
        record_type: "expense" | "income" | "task" | "note" |
                     "session" | "ritual" | "client"
        raw:         dict от Claude с произвольными ключами

    Returns:
        Notion page_id
    """
    db_id = _get_db_id(bot, record_type)
    properties = map_fields(bot, record_type, raw)
    client = _make_client()
    return await client.create_page(db_id, properties)


async def update_record(page_id: str, bot: str, record_type: str, raw: dict) -> None:
    """Обновляет запись в Notion."""
    properties = map_fields(bot, record_type, raw)
    client = _make_client()
    await client.update_page(page_id, properties)


async def query_records(
    bot: str,
    record_type: str,
    filters: dict | None = None,
    sorts: list | None = None,
    page_size: int = 20,
) -> list[dict]:
    """Запрашивает записи из базы."""
    db_id = _get_db_id(bot, record_type)
    client = _make_client()
    return await client.query_database(db_id, filters, sorts, page_size)
