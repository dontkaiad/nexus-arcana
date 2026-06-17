"""debts — personal debt ledger (#8 step 1).

Revision ID: q7r8s9t0u1v2
Revises: p6i7j8k9l0m1
Create Date: 2026-06-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "q7r8s9t0u1v2"
down_revision = "p6i7j8k9l0m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "debts",
        sa.Column("id",              sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_notion_id",  sa.Text,    nullable=False, server_default=""),
        sa.Column("name",            sa.Text,    nullable=False),
        # 'i_owe' — Кай должна кому-то (бюджет)
        # 'they_owe' — клиент/кто-то должен Кай
        sa.Column("kind",            sa.Text,    nullable=False, server_default="i_owe"),
        sa.Column("amount",          sa.Numeric, nullable=False),
        sa.Column("deadline",        sa.Text,    nullable=True),
        sa.Column("strategy",        sa.Text,    nullable=True),
        sa.Column("monthly_payment", sa.Numeric, nullable=False, server_default="0"),
        sa.Column("is_active",       sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('i_owe', 'they_owe')", name="ck_debts_kind"),
    )
    # Регистронезависимый уник: "Маша"/"маша" = один долг; ON CONFLICT в upsert
    # должен ссылаться на этот индекс (не constraint), т.к. lower() — expression index
    op.execute(
        "CREATE UNIQUE INDEX uq_debts_owner_kind_name "
        "ON debts (user_notion_id, kind, lower(name))"
    )
    op.create_index(
        "ix_debts_owner_kind_active",
        "debts",
        ["user_notion_id", "kind", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_debts_owner_kind_active", table_name="debts")
    # DROP CONSTRAINT first — handles case where PG created a constraint-backed index
    # (legacy UniqueConstraint path). If it's a plain expression index, ALTER is a no-op.
    op.execute("ALTER TABLE debts DROP CONSTRAINT IF EXISTS uq_debts_owner_kind_name")
    op.execute("DROP INDEX IF EXISTS uq_debts_owner_kind_name")
    op.drop_table("debts")
