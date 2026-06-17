"""idempotency_keys table — dedup for financial POSTs (#7)

Revision ID: p6i7j8k9l0m1
Revises: o5h6i7j8k9l0
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "p6i7j8k9l0m1"
down_revision = "o5h6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id",          sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tg_id",       sa.BigInteger, nullable=False),
        sa.Column("key",         sa.Text,       nullable=False),
        sa.Column("result_json", JSONB,         nullable=True),
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tg_id", "key", name="uq_idempotency_tg_key"),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
