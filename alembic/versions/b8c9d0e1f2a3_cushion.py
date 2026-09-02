"""cushion — financial cushion tracker (подушка как отдельная сущность).

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cushion",
        sa.Column("id",                   sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_notion_id",       sa.Text, nullable=False, server_default=""),
        sa.Column("balance",              sa.Numeric, nullable=False, server_default="0"),
        sa.Column("target",               sa.Numeric, nullable=True),
        sa.Column("monthly_contribution", sa.Numeric, nullable=False, server_default="0"),
        sa.Column("created_at",           sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at",           sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    # Один ряд подушки на пользователя.
    op.execute(
        "CREATE UNIQUE INDEX uq_cushion_owner ON cushion (user_notion_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cushion_owner")
    op.drop_table("cushion")
