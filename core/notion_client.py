"""core/notion_client.py — Notion read/write adapter.

USED ONLY by one-off backfill/migration scripts in scripts/ (read Notion → write PG).
NOT imported by runtime (bot / miniapp / core handlers) — runtime is 100% Notion-free
(prop-builders → core/props.py, PG-shims → core/client_resolve.py + finance_repo,
log_error → core/error_log.py). Delete this file when all backfills/migrations are done.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from notion_client import AsyncClient

logger = logging.getLogger(__name__)


# ─── Низкоуровневый клиент ────────────────────────────────────────────────────

class NotionClient:
    def __init__(self, token: str) -> None:
        self._client = AsyncClient(auth=token)

    async def update_page(self, page_id: str, properties: dict) -> None:
        await self._client.pages.update(page_id=page_id, properties=properties)
        logger.info("notion.update_page %s", page_id[:8])

    async def query_database(
        self,
        database_id: str,
        filters: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 20,
    ) -> List[dict]:
        kwargs: dict = {"database_id": database_id, "page_size": page_size}
        if filters:
            kwargs["filter"] = filters
        if sorts:
            kwargs["sorts"] = sorts
        resp = await self._client.databases.query(**kwargs)
        return resp.get("results", [])


# ─── Синглтон ─────────────────────────────────────────────────────────────────

_instance: Optional[NotionClient] = None


def _notion() -> NotionClient:
    global _instance
    if _instance is None:
        from core.config import config
        _instance = NotionClient(config.notion_token)
    return _instance


# ─── Read/write helpers used by scripts ───────────────────────────────────────

async def query_pages(
    db_id: str,
    filters: Optional[dict] = None,
    sorts: Optional[list] = None,
    page_size: int = 20,
) -> List[dict]:
    try:
        return await _notion().query_database(db_id, filters, sorts, page_size)
    except Exception as e:
        logger.error("query_pages error: %s · db=%s", e, db_id[:8])
        return []


async def get_page(page_id: str) -> dict:
    """Получить страницу Notion по ID."""
    return await _notion()._client.pages.retrieve(page_id=page_id)


async def update_page(page_id: str, props: dict) -> None:
    await _notion().update_page(page_id, props)
