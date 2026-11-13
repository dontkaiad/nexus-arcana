"""task_category: 📣 Личный бренд

Новая категория задач для контент-плана личного бренда (посты в X и тгк, статьи, закрепы).
Миграция только добавляет строку в справочник; существующие задачи не меняются.
Откат: задачи этой категории переезжают в 💳 Прочее, строка удаляется.

Revision ID: e4a6b8c0d235
Revises: d3f5a7c9e124
Create Date: 2026-11-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e4a6b8c0d235"
down_revision: Union[str, Sequence[str], None] = "d3f5a7c9e124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODE = "\U0001f4e3 Личный бренд"
FALLBACK = "\U0001f4b3 Прочее"


def upgrade() -> None:
    op.execute(
        "INSERT INTO task_category (code) VALUES ('%s') ON CONFLICT (code) DO NOTHING" % CODE
    )


def downgrade() -> None:
    op.execute(
        "UPDATE tasks SET category_id = (SELECT id FROM task_category WHERE code = '%s') "
        "WHERE category_id = (SELECT id FROM task_category WHERE code = '%s')" % (FALLBACK, CODE)
    )
    op.execute("DELETE FROM task_category WHERE code = '%s'" % CODE)
