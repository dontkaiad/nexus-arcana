"""memories_merged_into

Объединение похожих фактов «Я»: у скрытого дубля `memories.merged_into` = id оставшегося факта (сам дубль помечается
is_archived, поэтому все существующие выборки его не видят). Пары, которые Кай отметила «Это разное», лежат в
`memory_dedup_dismissed` (a < b) и больше не попадают в предложения. Откат объединения — UPDATE (is_archived=false,
merged_into=NULL), данные не теряются.

Revision ID: a8c3e5f21b74
Revises: f7d0b3e5c942
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8c3e5f21b74"
down_revision: Union[str, Sequence[str], None] = "f7d0b3e5c942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("merged_into", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_memories_merged_into", "memories", "memories", ["merged_into"], ["id"], ondelete="SET NULL")
    op.create_index("ix_memories_merged_into", "memories", ["merged_into"], postgresql_where=sa.text("merged_into IS NOT NULL"))
    op.create_table(
        "memory_dedup_dismissed",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("a", sa.BigInteger(), nullable=False),
        sa.Column("b", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("user_id", "a", "b"),
        sa.CheckConstraint("a < b", name="ck_memory_dedup_dismissed_order"),
    )


def downgrade() -> None:
    op.drop_table("memory_dedup_dismissed")
    op.drop_index("ix_memories_merged_into", table_name="memories")
    op.drop_constraint("fk_memories_merged_into", "memories", type_="foreignkey")
    op.drop_column("memories", "merged_into")
