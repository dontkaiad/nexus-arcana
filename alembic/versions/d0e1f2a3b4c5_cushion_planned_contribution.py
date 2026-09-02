"""cushion.monthly_contribution → planned_contribution.

Механизм «пользователь задаёт месячный взнос» удалён. Теперь это взнос из
последнего принятого плана (20% дохода в комфортный месяц, 0 в тяжёлый).
Существующее значение сохраняем — просто переименовываем колонку.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "cushion", "monthly_contribution", new_column_name="planned_contribution",
    )


def downgrade() -> None:
    op.alter_column(
        "cushion", "planned_contribution", new_column_name="monthly_contribution",
    )
