"""core_identity.user_id — one owner key shared across a person's devices (#202)

`core_identity` has one row per Telegram account. Nexus/Arcana is
single-owner today (ADR-0007: "N=2 rows, single owner, two devices"), so
memory / prompt-context / destructive flows should treat both device rows as
one person — but every owner-scoped table keys on the per-device
`notion_id`, so a naive `WHERE user_id = …` would fragment the owner's own
memory across devices (#202, option C).

This migration adds `core_identity.user_id` — the shared owner key — and
folds every existing identity onto ONE canonical owner:

- canonical owner = the identity that already owns the most `memories` rows
  (tie → earliest `created_at`); on an empty DB, `user_id = notion_id` self.
- every `core_identity.user_id` is set to the canonical id.
- every `<table>.user_id` that currently holds a *non-canonical*
  `core_identity.notion_id` is repointed to the canonical id, across all 14
  owner-scoped tables (same list as `df56a1b2c3d4`).

New identity rows self-own (`user_id = notion_id`); adding a further device
to the same owner is a manual `UPDATE core_identity SET user_id = <canon>` +
repoint. The merge is not reversed on downgrade (the column is dropped;
repointed rows stay repointed — the ids were interchangeable owners).

Revision ID: e067a1b2c3d4
Revises: df56a1b2c3d4
Create Date: 2026-09-07
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "e067a1b2c3d4"
down_revision = "df56a1b2c3d4"
branch_labels = None
depends_on = None

_OWNER_TABLES = [
    "grimoire_entries", "clients", "cushion", "sessions", "works", "tasks",
    "nexus_budget", "nexus_lists", "arcana_inventory", "notes", "arcana_pnl",
    "debts", "cushion_transactions", "memories",
]


def upgrade() -> None:
    op.add_column("core_identity", sa.Column("user_id", sa.Text(), nullable=True))
    conn = op.get_bind()

    canon = conn.execute(sa.text("""
        SELECT ci.notion_id
        FROM core_identity ci
        LEFT JOIN memories m ON m.user_id = ci.notion_id
        GROUP BY ci.notion_id, ci.created_at
        ORDER BY count(m.id) DESC, ci.created_at ASC NULLS LAST
        LIMIT 1
    """)).scalar()

    if canon:
        conn.execute(sa.text("UPDATE core_identity SET user_id = :c"), {"c": canon})
        others = [r[0] for r in conn.execute(
            sa.text("SELECT notion_id FROM core_identity WHERE notion_id <> :c"),
            {"c": canon},
        )]
        if others:
            for t in _OWNER_TABLES:
                conn.execute(
                    sa.text(f"UPDATE {t} SET user_id = :c WHERE user_id = ANY(:o)"),
                    {"c": canon, "o": others},
                )
    else:
        conn.execute(sa.text("UPDATE core_identity SET user_id = notion_id"))

    op.alter_column("core_identity", "user_id", nullable=False)


def downgrade() -> None:
    op.drop_column("core_identity", "user_id")
