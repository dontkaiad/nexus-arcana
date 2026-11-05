"""task_completions

Журнал выполнений задач: одна строка на каждое выполнение (кто и когда). Раньше у повторяющейся задачи было
только `tasks.completed_at` (перезаписывается) и стрик в SQLite без истории. Всё аддитивно: существующие
таблицы не меняются. Бэкфилл: по одной записи (source='backfill') из текущего `tasks.completed_at`.
Откат: DROP TABLE task_completions (журнал теряется, `tasks` не затрагивается).

Revision ID: d5b8f1c3a720
Revises: c4a7e2b95d13
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5b8f1c3a720"
down_revision: Union[str, Sequence[str], None] = "c4a7e2b95d13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL_SQL = (
    "INSERT INTO task_completions (task_id, done_at, source) "
    "SELECT t.id, t.completed_at, 'backfill' FROM tasks t WHERE t.completed_at IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM task_completions c WHERE c.task_id = t.id)"
)


def upgrade() -> None:
    op.create_table(
        "task_completions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_task_completions_task"),
                  nullable=False),
        sa.Column("done_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("done_by", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'app'")),
    )
    op.create_index("ix_task_completions_task_done", "task_completions", ["task_id", sa.text("done_at DESC")])
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("ix_task_completions_task_done", table_name="task_completions")
    op.drop_table("task_completions")
