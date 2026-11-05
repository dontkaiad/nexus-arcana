"""task_overrides

Разовый перенос одного цикла расписания ситтера БЕЗ сдвига базы серии: запись (task_id, cycle_due) -> new_due.
Сама задача (tasks.reminder) и повторение не меняются; расписание отдаёт new_due как эффективный срок этого цикла.
Один перенос на цикл (PK). Откат: DROP TABLE (разовые переносы теряются, серии нет).

Revision ID: d2b8f4c0e315
Revises: c1a7e3b9d204
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2b8f4c0e315"
down_revision: Union[str, Sequence[str], None] = "c1a7e3b9d204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_overrides",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_task_overrides_task"), nullable=False),
        sa.Column("cycle_due", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("new_due", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("task_id", "cycle_due", name="pk_task_overrides"),
        sa.CheckConstraint("new_due > cycle_due", name="ck_task_overrides_later"),
    )


def downgrade() -> None:
    op.drop_table("task_overrides")
