"""#202 — core_identity.user_id: two device rows of one owner share a user_id.

Covers the domain/dict layer (the merge itself is a raw-SQL Alembic migration,
verified against the dev DB)."""
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from core.repos import pg_identity_repo as pgi
from core.repos.pg_identity_repo import _row_to_user, _get_by_tg_id_sync
from core.repos.identity_table import core_identity


def _engine_with_rows(rows):
    eng = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE core_identity (notion_id TEXT PRIMARY KEY, user_id TEXT NOT NULL "
            "DEFAULT '', tg_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '', "
            "role TEXT NOT NULL DEFAULT 'Тест', perm_nexus INTEGER NOT NULL DEFAULT 0, "
            "perm_arcana INTEGER NOT NULL DEFAULT 0, perm_finance INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP)"
        ))
        for r in rows:
            c.execute(core_identity.insert().values(**r))
    return eng


def test_row_to_user_falls_back_to_notion_id_when_user_id_blank():
    row = type("R", (), dict(notion_id="nid-1", user_id="", tg_id=1, name="", role="",
                             perm_nexus=False, perm_arcana=False, perm_finance=False))()
    assert _row_to_user(row).user_id == "nid-1"


def test_two_devices_resolve_to_one_user_id():
    eng = _engine_with_rows([
        dict(notion_id="dev-personal", user_id="dev-personal", tg_id=111,
             name="Кай (личный)", role="Владелец",
             perm_nexus=True, perm_arcana=True, perm_finance=True),
        dict(notion_id="dev-work", user_id="dev-personal", tg_id=222,
             name="Кай (рабочий)", role="Владелец",
             perm_nexus=True, perm_arcana=True, perm_finance=True),
    ])
    with patch.object(pgi, "_get_engine", return_value=eng):
        u1 = _get_by_tg_id_sync(111)
        u2 = _get_by_tg_id_sync(222)
    assert u1.user_id == u2.user_id == "dev-personal"
    assert u1.notion_id != u2.notion_id  # per-device identity kept


def test_to_user_dict_uses_shared_owner_key():
    from core.user_manager import _to_user_dict
    from core.repos.pg_identity_repo import IdentityUser
    u = IdentityUser(notion_id="dev-work", user_id="dev-personal", tg_id=222,
                     name="Кай", role="Владелец")
    assert _to_user_dict(u)["user_id"] == "dev-personal"
