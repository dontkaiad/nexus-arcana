"""core memories PG table (ADR-0005: observations store)

Revision ID: j0c1d2e3f4g5
Revises: i9d0e1f2g3h4
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "j0c1d2e3f4g5"
down_revision = "i9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("notion_id",      sa.Text, unique=True),
        sa.Column("fact_text",      sa.Text, nullable=False),
        sa.Column("key_name",       sa.Text, nullable=False, server_default=""),
        sa.Column("value_text",     sa.Text, nullable=False, server_default=""),
        sa.Column("category",       sa.Text, nullable=False, server_default=""),
        sa.Column("scope",          sa.Text, nullable=False, server_default="global"),
        sa.Column("source",         sa.Text, nullable=False, server_default="manual"),
        sa.Column("related_to",     sa.Text, nullable=False, server_default=""),
        sa.Column("is_current",     sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_archived",    sa.Boolean, nullable=False, server_default="false"),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_memories_key_name",   "memories", ["key_name"])
    op.create_index("ix_memories_category",   "memories", ["category"])
    op.create_index("ix_memories_scope",      "memories", ["scope"])
    op.create_index("ix_memories_is_current", "memories", ["is_current"])
    op.create_index("ix_memories_user",       "memories", ["user_notion_id"])


def downgrade() -> None:
    op.drop_table("memories")
