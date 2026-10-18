"""core/repos/pg_people_repo.py — People domain storage (ADR-0027). Sync SQLAlchemy Core;
async callers wrap in asyncio.to_thread. API/merge land in later PRs."""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import and_, func, insert, select, update

from core.db import get_engine
from core.repos.people_tables import people


def _row(r) -> Dict:
    return dict(r._mapping)


def find_by_tg(user_id: str, tg_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.user_id == user_id, people.c.tg_id == tg_id))).first()
    return _row(r) if r else None


def find_by_vk(user_id: str, vk_id: int) -> Optional[Dict]:
    with get_engine().connect() as c:
        r = c.execute(select(people).where(and_(people.c.user_id == user_id, people.c.vk_id == vk_id))).first()
    return _row(r) if r else None


def find_idless(user_id: str) -> List[Dict]:
    """Люди без tg_id и vk_id — кандидаты для повторного матча по имени."""
    with get_engine().connect() as c:
        rows = c.execute(select(people).where(and_(
            people.c.user_id == user_id, people.c.tg_id.is_(None), people.c.vk_id.is_(None),
        ))).all()
    return [_row(r) for r in rows]


def create(user_id: str, name: str, aliases: List[str], *, birthday: Optional[date] = None,
           tg_id: Optional[int] = None, tg_username: Optional[str] = None,
           vk_id: Optional[int] = None, status: str = "pending") -> int:
    with get_engine().begin() as c:
        return c.execute(insert(people).values(
            user_id=user_id, name=name, aliases=aliases, birthday=birthday, tg_id=tg_id,
            tg_username=tg_username, vk_id=vk_id, status=status,
        ).returning(people.c.id)).scalar_one()


def fill_missing(person: Dict, *, aliases: List[str], birthday: Optional[date],
                 tg_id: Optional[int], tg_username: Optional[str], vk_id: Optional[int]) -> bool:
    """Дополняет ТОЛЬКО пустые поля и объединяет алиасы; имя/статус не трогает."""
    vals: Dict = {}
    if birthday and not person.get("birthday"):
        vals["birthday"] = birthday
    for f, v in (("tg_id", tg_id), ("tg_username", tg_username), ("vk_id", vk_id)):
        if v and not person.get(f):
            vals[f] = v
    merged = list(person.get("aliases") or [])
    seen = {a.lower() for a in merged} | {person["name"].lower()}
    for a in aliases:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            merged.append(a)
    if merged != list(person.get("aliases") or []):
        vals["aliases"] = merged
    if not vals:
        return False
    vals["updated_at"] = func.now()
    with get_engine().begin() as c:
        c.execute(update(people).where(people.c.id == person["id"]).values(**vals))
    return True
