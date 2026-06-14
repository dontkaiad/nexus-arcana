"""nexus/repos/pg_notes_repo.py — PG implementation for nexus 💡 Заметки."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text, delete
from sqlalchemy.engine import Engine

from nexus.repos.notes_tables import notes, note_tags, note_tag_map

logger = logging.getLogger("nexus.pg_notes_repo")

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from arcana.repos.pg_sessions_repo import get_engine as _arc_engine
        _engine = _arc_engine()
    return _engine


# ── Domain object ──────────────────────────────────────────────────────────────

@dataclass
class Note:
    """Domain representation of one 💡 Заметки row."""
    id: str
    title: str
    tags: List[str] = field(default_factory=list)
    date: str = ""          # YYYY-MM-DD or ""
    user_notion_id: str = ""
    is_archived: bool = False


# ── Lookup caches ─────────────────────────────────────────────────────────────

_tag_id: Dict[str, int] = {}   # code → id
_tag_code: Dict[int, str] = {} # id → code
_lookups_loaded: bool = False


def _load_lookups_sync() -> None:
    global _lookups_loaded
    with get_engine().connect() as conn:
        for row in conn.execute(select(note_tags.c.id, note_tags.c.code)):
            _tag_id[row[1]] = row[0]
            _tag_code[row[0]] = row[1]
    _lookups_loaded = True


def _ensure_lookups() -> None:
    if not _lookups_loaded:
        _load_lookups_sync()


def _reload_lookups_sync() -> None:
    """Reload after adding new tags."""
    _tag_id.clear()
    _tag_code.clear()
    _load_lookups_sync()


def _fuzzy_match_tag(raw: str) -> Optional[str]:
    """Fuzzy match raw string against canonical tag codes. Returns canonical or None."""
    if not raw:
        return None
    if raw in _tag_id:
        return raw
    raw_low = raw.lower().strip()
    # exact stripped match
    for code in _tag_id:
        if raw_low == code.lower().strip():
            return code
    # substring
    for code in _tag_id:
        if raw_low in code.lower() or code.lower() in raw_low:
            return code
    return None


def _get_or_create_tag_sync(code: str) -> int:
    """Get tag id, creating the tag if needed. Reloads lookups cache."""
    _ensure_lookups()
    # exact match first
    if code in _tag_id:
        return _tag_id[code]
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(note_tags.c.id).where(note_tags.c.code == code)
        ).fetchone()
        if existing:
            tid = existing[0]
        else:
            result = conn.execute(
                note_tags.insert().values(code=code).returning(note_tags.c.id)
            )
            tid = result.fetchone()[0]
    _tag_id[code] = tid
    _tag_code[tid] = code
    return tid


# ── Row → domain object ───────────────────────────────────────────────────────

def _row_to_note(row, tag_codes: List[str]) -> Note:
    d = row.date
    date_str = d.isoformat() if d else ""
    return Note(
        id=str(row.id),
        title=row.title or "",
        tags=tag_codes,
        date=date_str,
        user_notion_id=row.user_notion_id or "",
        is_archived=bool(row.is_archived),
    )


def _fetch_tags_for_note_sync(conn, note_id: int) -> List[str]:
    """Fetch tag codes for one note."""
    rows = conn.execute(
        select(note_tag_map.c.tag_id)
        .where(note_tag_map.c.note_id == note_id)
    ).fetchall()
    return [_tag_code[r[0]] for r in rows if r[0] in _tag_code]


def _enrich_notes_sync(rows) -> List[Note]:
    """Add tags to each note row."""
    _ensure_lookups()
    if not rows:
        return []
    with get_engine().connect() as conn:
        result = []
        for row in rows:
            tag_codes = _fetch_tags_for_note_sync(conn, row.id)
            result.append(_row_to_note(row, tag_codes))
        return result


# ── Sync helpers ──────────────────────────────────────────────────────────────

def _add_sync(
    title: str,
    tags: List[str],
    date: Optional[str],
    user_notion_id: str,
    notion_id: Optional[str] = None,
) -> str:
    _ensure_lookups()
    parsed_date = None
    if date:
        try:
            parsed_date = datetime.strptime(date[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    with get_engine().begin() as conn:
        result = conn.execute(
            notes.insert().values(
                notion_id=notion_id,
                title=title,
                date=parsed_date,
                user_notion_id=user_notion_id or "",
            ).returning(notes.c.id)
        )
        note_id = result.fetchone()[0]

        for tag_code in tags:
            tag_id = _get_or_create_tag_sync(tag_code)
            conn.execute(
                note_tag_map.insert().values(note_id=note_id, tag_id=tag_id)
            )

    return str(note_id)


def _get_all_tags_sync() -> List[str]:
    _ensure_lookups()
    return list(_tag_id.keys())


def _find_or_prepare_tag_sync(raw: str) -> Tuple[str, bool]:
    """Returns (canonical_code, is_new). is_new=True means tag not in DB yet."""
    _ensure_lookups()
    canonical = _fuzzy_match_tag(raw)
    if canonical:
        return (canonical, False)
    # Not found — format and mark as new
    from core.option_helper import format_option
    formatted = format_option(raw)
    return (formatted, True)


def _list_active_sync(user_notion_id: str) -> List:
    q = select(notes).where(notes.c.is_archived == False)  # noqa: E712
    if user_notion_id:
        q = q.where(notes.c.user_notion_id == user_notion_id)
    q = q.order_by(notes.c.date.desc().nulls_last(), notes.c.created_at.desc())
    with get_engine().connect() as conn:
        return conn.execute(q).fetchall()


def _find_older_than_days_sync(user_notion_id: str, days: int) -> List[Note]:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    q = (
        select(notes)
        .where(notes.c.is_archived == False)  # noqa: E712
        .where(notes.c.date <= cutoff)
        .where(notes.c.date.isnot(None))
    )
    if user_notion_id:
        q = q.where(notes.c.user_notion_id == user_notion_id)
    q = q.order_by(notes.c.date.asc())
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return _enrich_notes_sync(rows)


def _list_recent_sync(user_notion_id: str, limit: int = 50) -> List[Note]:
    rows = _list_active_sync(user_notion_id)
    rows = rows[:limit]
    return _enrich_notes_sync(rows)


def _search_by_tag_sync(tag: str, user_notion_id: str) -> List[Note]:
    _ensure_lookups()
    tag_low = tag.lower()
    matching_ids = [tid for code, tid in _tag_id.items() if tag_low in code.lower()]
    if not matching_ids:
        return []
    q = (
        select(notes)
        .join(note_tag_map, note_tag_map.c.note_id == notes.c.id)
        .where(note_tag_map.c.tag_id.in_(matching_ids))
        .where(notes.c.is_archived == False)  # noqa: E712
    )
    if user_notion_id:
        q = q.where(notes.c.user_notion_id == user_notion_id)
    q = q.order_by(notes.c.date.desc().nulls_last())
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return _enrich_notes_sync(rows)


def _search_by_title_sync(hint: str, user_notion_id: str) -> List[Note]:
    q = (
        select(notes)
        .where(notes.c.is_archived == False)  # noqa: E712
        .where(notes.c.title.ilike(f"%{hint}%"))
    )
    if user_notion_id:
        q = q.where(notes.c.user_notion_id == user_notion_id)
    q = q.order_by(notes.c.date.desc().nulls_last())
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return _enrich_notes_sync(rows)


def _find_for_edit_sync(hint: str, user_notion_id: str) -> Optional[Note]:
    if hint == "последняя":
        q = (
            select(notes)
            .where(notes.c.is_archived == False)  # noqa: E712
            .order_by(notes.c.created_at.desc())
            .limit(1)
        )
    else:
        q = (
            select(notes)
            .where(notes.c.is_archived == False)  # noqa: E712
            .where(notes.c.title.ilike(f"%{hint}%"))
            .order_by(notes.c.created_at.desc())
            .limit(1)
        )
    if user_notion_id:
        q = q.where(notes.c.user_notion_id == user_notion_id)
    with get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    if not row:
        return None
    return _enrich_notes_sync([row])[0]


def _update_tags_sync(note_id: str, tags: List[str]) -> None:
    _ensure_lookups()
    nid = int(note_id)
    with get_engine().begin() as conn:
        conn.execute(
            delete(note_tag_map).where(note_tag_map.c.note_id == nid)
        )
        for tag_code in tags:
            tag_id = _get_or_create_tag_sync(tag_code)
            conn.execute(
                note_tag_map.insert().values(note_id=nid, tag_id=tag_id)
            )


def _archive_sync(note_id: str) -> bool:
    try:
        nid = int(note_id)
        with get_engine().begin() as conn:
            conn.execute(
                notes.update()
                .where(notes.c.id == nid)
                .values(is_archived=True, updated_at=text("now()"))
            )
        return True
    except Exception as e:
        logger.error("archive %s failed: %s", note_id, e)
        return False


# ── Public async API ───────────────────────────────────────────────────────────

class PgNotesRepo:
    async def add(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        date: Optional[str] = None,
        user_notion_id: str = "",
    ) -> Optional[str]:
        return await asyncio.to_thread(
            _add_sync, text, tags or [], date, user_notion_id
        )

    async def get_all_tags(self) -> List[str]:
        return await asyncio.to_thread(_get_all_tags_sync)

    async def find_or_prepare_tag(self, raw: str) -> Tuple[str, bool]:
        return await asyncio.to_thread(_find_or_prepare_tag_sync, raw)

    async def find_older_than_days(
        self, user_notion_id: str = "", days: int = 7
    ) -> List[Note]:
        return await asyncio.to_thread(_find_older_than_days_sync, user_notion_id, days)

    async def list_recent(
        self, user_notion_id: str = "", limit: int = 50
    ) -> List[Note]:
        return await asyncio.to_thread(_list_recent_sync, user_notion_id, limit)

    async def search_by_tag(
        self, tag: str, user_notion_id: str = ""
    ) -> List[Note]:
        return await asyncio.to_thread(_search_by_tag_sync, tag, user_notion_id)

    async def search_by_title(
        self, hint: str, user_notion_id: str = ""
    ) -> List[Note]:
        return await asyncio.to_thread(_search_by_title_sync, hint, user_notion_id)

    async def find_for_edit(
        self, hint: str, user_notion_id: str = ""
    ) -> Optional[Note]:
        return await asyncio.to_thread(_find_for_edit_sync, hint, user_notion_id)

    async def update_tags(self, note_id: str, tags: List[str]) -> None:
        await asyncio.to_thread(_update_tags_sync, note_id, tags)

    async def archive(self, note_id: str) -> bool:
        return await asyncio.to_thread(_archive_sync, note_id)
