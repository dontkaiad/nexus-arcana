"""rename user_notion_id → user_id on all 14 owner columns (#144)

Notion is fully gone; the column never held anything but the internal user
id (a Notion-page-id-shaped string, same as ``core_identity.notion_id``).
The name was misleading. Pure rename — no type change, no data move, no
enforced FKs involved.

``core_identity.notion_id`` (PRIMARY KEY) is deliberately NOT renamed — see
``docs/CASES/0024-identity-pk-keeps-notion-id-name.md`` (kept per #149; it is
the live owner key, not a migration artifact).

Two indexes carry the old name in their identifier and are renamed
explicitly (Postgres keeps an index working across a column rename but does
not touch its name): ``idx_tasks_user_notion_id``,
``idx_notes_user_notion_id``. The other owner indexes
(``idx_sessions_user``, ``ix_memories_user``, …) don't name the column, so
they need nothing.

Revision ID: df56a1b2c3d4
Revises: cd34ef56a1b2
Create Date: 2026-09-07
"""
from __future__ import annotations
from alembic import op

revision = "df56a1b2c3d4"
down_revision = "cd34ef56a1b2"
branch_labels = None
depends_on = None

_TABLES = [
    "grimoire_entries", "clients", "cushion", "sessions", "works", "tasks",
    "nexus_budget", "nexus_lists", "arcana_inventory", "notes", "arcana_pnl",
    "debts", "cushion_transactions", "memories",
]


def upgrade() -> None:
    for t in _TABLES:
        op.alter_column(t, "user_notion_id", new_column_name="user_id")
    op.execute("ALTER INDEX idx_tasks_user_notion_id RENAME TO idx_tasks_user_id")
    op.execute("ALTER INDEX idx_notes_user_notion_id RENAME TO idx_notes_user_id")


def downgrade() -> None:
    op.execute("ALTER INDEX idx_notes_user_id RENAME TO idx_notes_user_notion_id")
    op.execute("ALTER INDEX idx_tasks_user_id RENAME TO idx_tasks_user_notion_id")
    for t in _TABLES:
        op.alter_column(t, "user_id", new_column_name="user_notion_id")
