"""zarya_pending_purpose — незавершённая бронь друга (ждём «на что») в Postgres

Раньше жила в in-memory dict в zarya/handlers.py → терялась при рестарте бота.
Одна строка на tg_id; TTL проверяется в коде (core/booking/pending_purpose.py).

Revision ID: 7e3b9a41c5d2
Revises: a3c9e1d47b52
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "7e3b9a41c5d2"
down_revision = "a3c9e1d47b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zarya_pending_purpose",
        sa.Column("tg_id", sa.BigInteger, primary_key=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("zarya_pending_purpose")
