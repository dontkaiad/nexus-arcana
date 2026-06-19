"""core/deleter.py — generic /delete: per-domain PG soft-archive with confirm.

Flow (domain-agnostic, in the bot handlers):
  text intent → Haiku target classifier (domain) → parse_delete_intent
  (scope/date/count) → select_records (PG, per domain) → preview + confirm
  → archive_records (PG soft-archive).

Storage is PostgreSQL. Deletion is always SOFT (archive flag / status), never
a hard DELETE. Two domains are gated OFF (no PG delete path):
  - finance — no archive method on the finance repos.
  - clients — FK parent of sessions/rituals/works; deleting risks orphaning.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from core.claude_client import ask_claude

logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3))

# Domains with a working PG soft-archive path.
SUPPORTED_DOMAINS = {"tasks", "notes", "sessions", "rituals", "works"}
# Domains intentionally NOT deletable via /delete (no safe PG path).
GATED_DOMAINS = {"finance", "clients"}

PARSE_DELETE_SYSTEM = """Определи параметры удаления из сообщения. Ответь ТОЛЬКО JSON без markdown:
{
  "scope": "today|last|date|month|all",
  "date": "YYYY-MM-DD или null",
  "month": "YYYY-MM или null",
  "count": число (для last N) или 1
}
Примеры:
"удали последнее" → {"scope": "last", "date": null, "month": null, "count": 1}
"удали последние 3" → {"scope": "last", "date": null, "month": null, "count": 3}
"удали все за сегодня" → {"scope": "today", "date": null, "month": null, "count": 1}
"удали все за март" → {"scope": "month", "date": null, "month": "2026-03", "count": 1}
"удали запись от 5 марта" → {"scope": "date", "date": "2026-03-05", "month": null, "count": 1}"""


async def parse_delete_intent(text: str) -> dict:
    import json
    raw = await ask_claude(text, system=PARSE_DELETE_SYSTEM, max_tokens=150,
                           model="claude-haiku-4-5-20251001", temperature=0)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {"scope": "last", "date": None, "month": None, "count": 1}


# ── helpers ──────────────────────────────────────────────────────────────────

def _date_str(val) -> str:
    """Normalize a domain date field (datetime | ISO str | None) → 'YYYY-MM-DD'."""
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val)[:10]


# ── SELECTION: pull records from the PG repo for a domain ─────────────────────
# date attribute preserved per domain (matches the legacy Notion date_field):
#   tasks → deadline, notes → date, sessions → occurred_at, rituals → occurred_at,
#   works → deadline.

async def _fetch_all(domain: str, user_notion_id: str) -> List[dict]:
    """Fetch all candidate records for a domain as [{id, title, date}]."""
    if domain == "tasks":
        from nexus.repos.pg_tasks_repo import PgTasksRepo
        rows = await PgTasksRepo().list_all(user_notion_id)
        return [{"id": r.id, "title": r.title, "date": _date_str(r.deadline)} for r in rows]
    if domain == "notes":
        from nexus.repos.pg_notes_repo import PgNotesRepo
        rows = await PgNotesRepo().list_recent(user_notion_id, limit=500)
        return [{"id": r.id, "title": r.title, "date": _date_str(r.date)} for r in rows]
    if domain == "sessions":
        from arcana.repos.pg_sessions_repo import PgSessionsRepo
        rows = await PgSessionsRepo().list_all(user_notion_id)
        return [{"id": r.id, "title": (r.session_name or r.question or "—"),
                 "date": _date_str(r.date)} for r in rows]
    if domain == "rituals":
        # NOTE: PgRitualsRepo.list_all is not user-filtered (Ritual has no user
        # field); the entry guard requires a user, but rituals isolation is
        # repo-wide. Acceptable for single-user; revisit if multi-user.
        from arcana.repos.pg_rituals_repo import PgRitualsRepo
        rows = await PgRitualsRepo().list_all(user_notion_id=user_notion_id)
        return [{"id": r.id, "title": r.name, "date": _date_str(r.date)} for r in rows]
    if domain == "works":
        from arcana.repos.pg_works_repo import PgWorksRepo
        rows = await PgWorksRepo().list_all(user_notion_id)
        return [{"id": r.id, "title": r.title,
                 "date": _date_str(getattr(r, "deadline_iso", "") or getattr(r, "deadline_dt", None))}
                for r in rows]
    return []


def _apply_scope(records: List[dict], scope: str, date: Optional[str],
                 month: Optional[str], count: int) -> List[dict]:
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    if scope == "today":
        return [r for r in records if r["date"] == today]
    if scope == "date" and date:
        return [r for r in records if r["date"] == date[:10]]
    if scope == "month" and month:
        return [r for r in records if r["date"][:7] == month]
    if scope == "last":
        return sorted(records, key=lambda r: r["date"], reverse=True)[:max(1, count)]
    if scope == "all":
        return list(records)
    return []


async def select_records(
    domain: str,
    scope: str,
    date: Optional[str] = None,
    month: Optional[str] = None,
    count: int = 1,
    user_notion_id: str = "",
) -> List[dict]:
    """Records selected for deletion: [{id, title, date}], scope-filtered."""
    if domain not in SUPPORTED_DOMAINS:
        return []
    try:
        records = await _fetch_all(domain, user_notion_id)
    except Exception as e:
        logger.error("select_records(%s): %s", domain, e)
        return []
    return _apply_scope(records, scope, date, month, int(count or 1))


def format_record(rec: dict) -> str:
    """One preview line from a domain record dict."""
    date = rec.get("date") or ""
    title = (rec.get("title") or "—")[:50]
    return f"{date} {title}".strip()


# ── DELETION: per-domain soft-archive dispatch ────────────────────────────────

async def _archive_one(domain: str, rec_id: str) -> bool:
    if domain == "tasks":
        from nexus.repos.pg_tasks_repo import PgTasksRepo
        await PgTasksRepo().set_archived(rec_id)  # returns None; status→archived
        return True
    if domain == "notes":
        from nexus.repos.pg_notes_repo import PgNotesRepo
        return bool(await PgNotesRepo().archive(rec_id))
    if domain == "sessions":
        from arcana.repos.pg_sessions_repo import PgSessionsRepo
        return bool(await PgSessionsRepo().archive(rec_id))
    if domain == "rituals":
        from arcana.repos.pg_rituals_repo import PgRitualsRepo
        return bool(await PgRitualsRepo().archive(rec_id))  # SOFT, not delete()
    if domain == "works":
        from arcana.repos.pg_works_repo import PgWorksRepo
        return bool(await PgWorksRepo().set_status(rec_id, "archived"))
    return False


async def archive_records(domain: str, ids: List[str]) -> int:
    """Soft-archive each id for a domain. Returns count archived."""
    if domain not in SUPPORTED_DOMAINS:
        return 0
    deleted = 0
    for rec_id in ids:
        try:
            if await _archive_one(domain, rec_id):
                deleted += 1
        except Exception as e:
            logger.error("archive_records(%s, %s): %s", domain, rec_id, e)
    return deleted
