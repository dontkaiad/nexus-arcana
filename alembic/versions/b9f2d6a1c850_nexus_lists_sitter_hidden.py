"""nexus_lists_sitter_hidden

Скрыть пункт инвентаря от ситтера: `nexus_lists.sitter_hidden` (bool, по умолчанию false). Инвентарь виден ситтеру по
умолчанию (list_type = 'инвентарь' AND NOT sitter_hidden), покупки и чеклисты только при for_sitter. Существующие
строки не меняются. Откат: DROP COLUMN (пометки скрытия теряются, пункты нет).

Revision ID: b9f2d6a1c850
Revises: a8c3e5f21b74
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b9f2d6a1c850"
down_revision: Union[str, Sequence[str], None] = "a8c3e5f21b74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nexus_lists", sa.Column("sitter_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("nexus_lists", "sitter_hidden")
