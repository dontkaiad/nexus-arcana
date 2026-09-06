"""memories: drop dead value_text column (#146)

``value_text`` was carried over from the ADR-0005 «strict key→value» idea but
no write path ever populated it — ``_add_sync`` / ``_upsert_sync`` /
``_update_fields_sync`` (core/repos/pg_memory_repo.py) never set it, so every
row had ``''``. Key lives in ``key_name``, value is parsed out of ``fact_text``
on read (limits regex, tz/city). The column carried zero information; dropping
it rather than back-filling a contract nobody uses.

Downgrade re-adds the column with the same NOT NULL + ``''`` default; data is
not recoverable (there was none).

Revision ID: bc23de45f012
Revises: ab12cd34ef56
Create Date: 2026-09-06
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "bc23de45f012"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("memories", "value_text")


def downgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "value_text", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
    )
