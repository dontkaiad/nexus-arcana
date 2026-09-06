"""drop dead notion_id columns from memories/nexus_lists/arcana_inventory/notes/tasks (#149)

Notion→PG migration artifact. These ``notion_id`` columns (UNIQUE, nullable)
were the bridge key during the one-time backfill and nothing writes them now —
no domain object carries the field, no ``_row_to_*`` reads it, the only
callers were the backfill scripts (deleted in this change). ``core_identity``
keeps its ``notion_id`` — there it is the live owner PRIMARY KEY, not an
artifact.

Same shape as ``d4f5e6a7b8c9`` which already dropped ``clients.notion_id``.
Postgres drops the auto ``<table>_notion_id_key`` unique constraint together
with the column. Downgrade re-adds the column nullable (no data to restore).

Revision ID: cd34ef56a1b2
Revises: bc23de45f012
Create Date: 2026-09-06
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "cd34ef56a1b2"
down_revision = "bc23de45f012"
branch_labels = None
depends_on = None

_TABLES = ["memories", "nexus_lists", "arcana_inventory", "notes", "tasks"]


def upgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "notion_id")


def downgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("notion_id", sa.Text(), nullable=True, unique=True))
