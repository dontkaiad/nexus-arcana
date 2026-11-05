"""care_foundations

Основы «ухода для ситтера»: tasks.for_sitter (задача входит в расписание ситтера), care_periods (период ухода, когда ситтер
активен; одна строка на владельца), care_alerts (одно предупреждение о просрочке на цикл), task_completions.cycle_due
(цикл, который закрыло выполнение; частичный unique (task_id, cycle_due) защищает от двойного выполнения одного цикла).
Всё аддитивно, существующие строки не меняются. Откат: DROP таблиц и колонок (данные ухода теряются, задачи нет).

Revision ID: c1a7e3b9d204
Revises: b9f2d6a1c850
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1a7e3b9d204"
down_revision: Union[str, Sequence[str], None] = "b9f2d6a1c850"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("for_sitter", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index("ix_tasks_for_sitter", "tasks", ["user_id"], postgresql_where=sa.text("for_sitter"))
    op.create_table(
        "care_periods",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_care_periods_order"),
    )
    op.create_table(
        "care_alerts",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_care_alerts_task"), nullable=False),
        sa.Column("cycle_due", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("task_id", "cycle_due", name="pk_care_alerts"),
    )
    op.add_column("task_completions", sa.Column("cycle_due", sa.TIMESTAMP(timezone=True)))
    op.create_index("ux_task_completions_cycle", "task_completions", ["task_id", "cycle_due"], unique=True,
                    postgresql_where=sa.text("cycle_due IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ux_task_completions_cycle", table_name="task_completions")
    op.drop_column("task_completions", "cycle_due")
    op.drop_table("care_alerts")
    op.drop_table("care_periods")
    op.drop_index("ix_tasks_for_sitter", table_name="tasks")
    op.drop_column("tasks", "for_sitter")
