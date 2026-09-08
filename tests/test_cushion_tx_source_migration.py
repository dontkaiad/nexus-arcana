"""tests/test_cushion_tx_source_migration.py — #123/#208

CHECK на cushion_transactions.source изначально был IN ('manual','payday_auto'),
но код давно пишет 'debt_overpaid' (#123) и 'windfall_income' (#208) — на
Postgres такой INSERT падал. Миграция c3d4e5f6a7b8 расширяет список.
"""
import importlib.util
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIG = os.path.join(REPO, "alembic", "versions", "c3d4e5f6a7b8_cushion_tx_source_expand.py")


def _load_mig():
    spec = importlib.util.spec_from_file_location("_mig_cushion_src", MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_metadata():
    m = _load_mig()
    assert m.revision == "c3d4e5f6a7b8"
    assert m.down_revision == "b2c3d4e5f6a7"
    assert "debt_overpaid" in m._NEW and "windfall_income" in m._NEW


@pytest.mark.asyncio
async def test_add_to_balance_accepts_new_sources_with_check():
    """С реальным CHECK (как после миграции) — оба новых source проходят."""
    import core.repos.pg_cushion_repo as crepo

    eng = sa.create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE cushion (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id TEXT NOT NULL DEFAULT '', balance REAL NOT NULL DEFAULT 0, target REAL, "
            "planned_contribution REAL NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE cushion_transactions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT '', "
            "amount REAL NOT NULL, source TEXT NOT NULL DEFAULT 'manual', "
            "note TEXT NOT NULL DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "CHECK (source IN ('manual','payday_auto','debt_overpaid','windfall_income')))"
        ))

    repo = crepo.PgCushionRepo()
    from unittest.mock import patch
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        b1 = await repo.add_to_balance("u", 1500, source="debt_overpaid", note="x")
        b2 = await repo.add_to_balance("u", 2000, source="windfall_income", note="y")

    assert b1 == 1500 and b2 == 3500
    with eng.connect() as conn:
        srcs = {r[0] for r in conn.execute(sa.text("SELECT source FROM cushion_transactions"))}
    assert srcs == {"debt_overpaid", "windfall_income"}
