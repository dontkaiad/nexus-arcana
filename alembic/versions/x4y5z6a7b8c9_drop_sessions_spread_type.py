"""drop sessions.spread_type — column obsolete after phase 5 of #174.

Readers switched to category_id → session_category join (phase 4).
spread_type served as denormalised display label; category_id is
the canonical single source of truth.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("sessions", "spread_type")


def downgrade() -> None:
    op.add_column("sessions", sa.Column("spread_type", sa.Text(), nullable=True))
