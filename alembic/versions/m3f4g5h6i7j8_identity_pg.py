"""core_identity PG table (🪪 Пользователи domain)

Revision ID: m3f4g5h6i7j8
Revises: l2e3f4g5h6i7
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "m3f4g5h6i7j8"
down_revision = "l2e3f4g5h6i7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_identity",
        sa.Column("notion_id", sa.Text(), primary_key=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'Тест'")),
        sa.Column("perm_nexus", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("perm_arcana", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("perm_finance", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_core_identity_tg_id", "core_identity", ["tg_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_core_identity_tg_id", table_name="core_identity")
    op.drop_table("core_identity")
