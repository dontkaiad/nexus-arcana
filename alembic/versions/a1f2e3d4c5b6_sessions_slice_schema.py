"""sessions slice schema

Revision ID: a1f2e3d4c5b6
Revises: 0857b6b83518
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "a1f2e3d4c5b6"
down_revision = "0857b6b83518"
branch_labels = None
depends_on = None


def _lookup_table(name: str) -> sa.Table:
    return op.create_table(
        name,
        sa.Column("id",    sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("code",  sa.Text,         nullable=False, unique=True),
        sa.Column("emoji", sa.Text),
        sa.Column("label", sa.Text,         nullable=False),
        sa.Column("sort",  sa.SmallInteger, server_default="0"),
    )


def upgrade() -> None:
    t_outcome = _lookup_table("session_outcome")
    op.bulk_insert(t_outcome, [
        {"code": "unverified", "emoji": "⏳",  "label": "Не проверено", "sort": 1},
        {"code": "partial",    "emoji": "〰️", "label": "Частично",     "sort": 2},
        {"code": "no",         "emoji": "❌",  "label": "Не сбылось",   "sort": 3},
        {"code": "yes",        "emoji": "✅",  "label": "Сбылось",      "sort": 4},
    ])

    op.create_table(
        "sessions",
        sa.Column("id",              sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("title",           sa.Text,       nullable=False),
        sa.Column("occurred_at",     sa.Date),

        # Content
        sa.Column("question",        sa.Text),
        sa.Column("cards",           sa.Text),
        sa.Column("interpretation",  sa.Text),
        sa.Column("triplet_summary", sa.Text),
        sa.Column("bottom_card",     sa.Text),
        sa.Column("session_name",    sa.Text),
        sa.Column("spread_type",     sa.Text),
        sa.Column("area",            sa.Text),
        sa.Column("deck",            sa.Text),

        # Finance
        sa.Column("amount",          sa.Numeric(10, 2), server_default="0"),
        sa.Column("paid",            sa.Numeric(10, 2), server_default="0"),

        # Relations (FKs to lookup tables)
        sa.Column("type_id",         sa.SmallInteger,
                  sa.ForeignKey("engagement_type.id")),   # shared with rituals
        sa.Column("payment_src_id",  sa.SmallInteger,
                  sa.ForeignKey("payment_source.id")),    # shared with rituals
        sa.Column("outcome_id",      sa.SmallInteger,
                  sa.ForeignKey("session_outcome.id")),

        # Client FK
        sa.Column("client_id",       sa.BigInteger,
                  sa.ForeignKey("clients.id")),

        sa.Column("user_notion_id",  sa.Text),
        sa.Column("archived",        sa.Boolean, server_default="false"),

        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_sessions_client_id",   "sessions", ["client_id"])
    op.create_index("idx_sessions_occurred_at", "sessions", ["occurred_at"])
    op.create_index("idx_sessions_user",        "sessions", ["user_notion_id"])


def downgrade() -> None:
    op.drop_index("idx_sessions_user",        table_name="sessions")
    op.drop_index("idx_sessions_occurred_at", table_name="sessions")
    op.drop_index("idx_sessions_client_id",   table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("session_outcome")
