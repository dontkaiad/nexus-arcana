"""memories_cat_block

Блок факта про котов: profile | care | health | relations | owner | unsorted. Блок определяет аудиторию (ситтер видит profile, care,
relations; health только «критично» и действующие курсы; owner и unsorted никогда). NULL = блок не поставлен = закрыто.
Миграция только добавляет nullable-колонку и CHECK; существующие строки не меняются (они NULL, то есть закрыты, пока не разложены).
Откат: DROP COLUMN (метки блоков теряются, сами факты нет).

Revision ID: d3f5a7c9e124
Revises: c9e2b7a4d815
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d3f5a7c9e124"
down_revision: Union[str, Sequence[str], None] = "c9e2b7a4d815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BLOCKS = ("profile", "care", "health", "relations", "owner", "unsorted")


def upgrade() -> None:
    op.add_column("memories", sa.Column("cat_block", sa.Text(), nullable=True))
    op.create_check_constraint(
        "memories_cat_block_check", "memories",
        "cat_block IS NULL OR cat_block IN (%s)" % ", ".join("'%s'" % b for b in BLOCKS))


def downgrade() -> None:
    op.drop_constraint("memories_cat_block_check", "memories", type_="check")
    op.drop_column("memories", "cat_block")
