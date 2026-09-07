"""goals — накопительные цели как таблица (#205)

Раньше цель = факт Памяти `цель_*` под категорией «💰 Лимит», парсился регексом
GOAL_RE. Минусов: нет реального трекинга накоплений (`saved`), переименование
плодит осиротевшие ключи, чтение размазано по `core/budget.py` + `/budget`
Sonnet-flow + Mini App. По аналогии с `долг_ → debts` (миграция q7r8s9t0u1v2)
цели переезжают в свою таблицу.

Бэкфилл: активные и деактивированные `цель_*` факты (кроме `цель_подушка`)
переносятся в `goals`, исходные строки Памяти архивируются (`is_archived=true`)
чтобы не дублироваться в `/api/memory` и не парситься дважды.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-07
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

# «цель: X — 200000₽ · откладываю 15000₽/мес»
_GOAL_RE = re.compile(
    r"цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]"
    r"(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?",
    re.IGNORECASE,
)


def _num(s: str) -> float:
    return float((s or "0").replace(" ", "").replace(",", "."))


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("target", sa.Numeric, nullable=False),
        sa.Column("monthly", sa.Numeric, nullable=False, server_default="0"),
        sa.Column("saved", sa.Numeric, nullable=False, server_default="0"),
        # active — копим; achieved — накопили/купили; dropped — убрали
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','achieved','dropped')", name="ck_goals_status"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_goals_owner_name ON goals (user_id, lower(name))"
    )
    op.create_index("ix_goals_owner_status", "goals", ["user_id", "status"])

    # ── backfill from memories ────────────────────────────────────────────────
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, user_id, fact_text, is_current FROM memories "
        "WHERE lower(key_name) LIKE 'цель\\_%' AND lower(key_name) <> 'цель_подушка' "
        "AND is_archived = false"
    )).fetchall()
    seen: set = set()
    for mid, uid, fact, is_current in rows:
        m = _GOAL_RE.search(fact or "")
        if not m:
            continue
        name = m.group(1).strip()
        dedup = (uid or "", name.lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        status = "active" if is_current else "dropped"
        conn.execute(sa.text(
            "INSERT INTO goals (user_id, name, target, monthly, status, closed_at) "
            "VALUES (:u, :n, :t, :mo, :st, CASE WHEN :st <> 'active' THEN now() END) "
            "ON CONFLICT (user_id, lower(name)) DO NOTHING"
        ), {"u": uid or "", "n": name, "t": _num(m.group(2)),
            "mo": _num(m.group(3)) if m.group(3) else 0, "st": status})
    # архивируем исходные факты — больше не читаются
    conn.execute(sa.text(
        "UPDATE memories SET is_archived = true "
        "WHERE lower(key_name) LIKE 'цель\\_%' AND lower(key_name) <> 'цель_подушка'"
    ))


def downgrade() -> None:
    op.drop_index("ix_goals_owner_status", table_name="goals")
    op.execute("DROP INDEX IF EXISTS uq_goals_owner_name")
    op.drop_table("goals")
