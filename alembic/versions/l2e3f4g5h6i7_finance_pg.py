"""nexus_budget + arcana_pnl PG tables (finance domain split by Бот)

Revision ID: l2e3f4g5h6i7
Revises: k1d2e3f4g5h6
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "l2e3f4g5h6i7"
down_revision = "k1d2e3f4g5h6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nexus_budget",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("description",    sa.Text, nullable=False, server_default=""),
        sa.Column("amount",         sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("category",       sa.Text, nullable=False, server_default=""),
        sa.Column("type_",          sa.Text, nullable=False, server_default=""),
        sa.Column("source",         sa.Text, nullable=False, server_default=""),
        sa.Column("date",           sa.Date, nullable=True),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_nexus_budget_date", "nexus_budget", ["date"])
    op.create_index("ix_nexus_budget_type_", "nexus_budget", ["type_"])

    op.create_table(
        "arcana_pnl",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("description",    sa.Text, nullable=False, server_default=""),
        sa.Column("amount",         sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("category",       sa.Text, nullable=False, server_default=""),
        sa.Column("type_",          sa.Text, nullable=False, server_default=""),
        sa.Column("source",         sa.Text, nullable=False, server_default=""),
        sa.Column("date",           sa.Date, nullable=True),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_arcana_pnl_date", "arcana_pnl", ["date"])
    op.create_index("ix_arcana_pnl_type_", "arcana_pnl", ["type_"])


def downgrade() -> None:
    op.drop_table("arcana_pnl")
    op.drop_table("nexus_budget")
