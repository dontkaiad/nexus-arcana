"""tasks_cat_id

Привязка задачи к коту: nullable `tasks.cat_id` (FK cats.id ON DELETE SET NULL), как `memories.cat_id`.
NULL = задача не про конкретного кота (или про всех). Существующие строки не меняются. Частичный индекс
только по привязанным задачам. Откат: DROP INDEX + DROP COLUMN (привязки теряются, сами задачи нет).

Revision ID: e6c9a2d4b831
Revises: d5b8f1c3a720
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e6c9a2d4b831"
down_revision: Union[str, Sequence[str], None] = "d5b8f1c3a720"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column(
        "cat_id", sa.BigInteger(), sa.ForeignKey("cats.id", ondelete="SET NULL", name="fk_tasks_cat")))
    op.create_index("ix_tasks_cat", "tasks", ["cat_id"], postgresql_where=sa.text("cat_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_tasks_cat", table_name="tasks")
    op.drop_column("tasks", "cat_id")
