"""core/repos/identity_repo.py — repository seam for 🪪 Пользователи (identity domain).

IdentityRepo is the public interface; backed by PgIdentityRepo.
Module-level singleton _repo used by core.user_manager.
"""
from __future__ import annotations

from typing import List, Optional

from core.repos.pg_identity_repo import IdentityUser, PgIdentityRepo

__all__ = ["IdentityUser", "IdentityRepo", "_repo"]


class IdentityRepo:
    def __init__(self) -> None:
        self._pg = PgIdentityRepo()

    async def get_by_tg_id(self, tg_id: int) -> Optional[IdentityUser]:
        return await self._pg.get_by_tg_id(tg_id)

    async def get_by_notion_id(self, notion_id: str) -> Optional[IdentityUser]:
        return await self._pg.get_by_notion_id(notion_id)

    async def get_all(self) -> List[IdentityUser]:
        return await self._pg.get_all()

    async def upsert(
        self,
        notion_id: str,
        tg_id: int,
        name: str,
        role: str = "Тест",
        perm_nexus: bool = False,
        perm_arcana: bool = False,
        perm_finance: bool = False,
    ) -> IdentityUser:
        return await self._pg.upsert(
            notion_id=notion_id, tg_id=tg_id, name=name, role=role,
            perm_nexus=perm_nexus, perm_arcana=perm_arcana, perm_finance=perm_finance,
        )


_repo = IdentityRepo()
