"""cushion_transactions — лог пополнений подушки.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cushion_transactions",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("amount",         sa.Numeric, nullable=False),
        sa.Column("source",         sa.Text, nullable=False, server_default="manual"),
        sa.Column("note",           sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('manual', 'payday_auto')", name="ck_cushion_tx_source"),
    )
    op.create_index(
        "ix_cushion_tx_owner_created",
        "cushion_transactions",
        ["user_notion_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_cushion_tx_owner_created", table_name="cushion_transactions")
    op.drop_table("cushion_transactions")
