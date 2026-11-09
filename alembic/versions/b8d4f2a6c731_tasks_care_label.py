"""tasks_care_label

Короткая метка задачи для блока «последний раз» в гайде котов и Mini App: `tasks.care_label` («лотки», «вода»), NULL = нет.
Заполняется только у задач с for_sitter (код это держит: set_care_label не пишет в чужие задачи, снятие for_sitter метку очищает).
Длина ≤ 30 (CHECK). Существующие строки не меняются. Откат: DROP COLUMN (метки теряются, задачи нет).

Revision ID: b8d4f2a6c731
Revises: a7c3e1b9d452
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8d4f2a6c731"
down_revision: Union[str, Sequence[str], None] = "a7c3e1b9d452"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("care_label", sa.Text(), nullable=True))
    op.create_check_constraint("tasks_care_label_len", "tasks", "care_label IS NULL OR char_length(care_label) <= 30")


def downgrade() -> None:
    op.drop_constraint("tasks_care_label_len", "tasks", type_="check")
    op.drop_column("tasks", "care_label")
