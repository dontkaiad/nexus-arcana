"""nexus_lists_for_sitter

Пометка «для ситтера» на пункте списка Nexus: `nexus_lists.for_sitter` (bool, по умолчанию false, то есть закрыто).
Отдельно от `shared` (то значит «видно Заре»). Существующие строки остаются закрытыми. Частичный индекс только
по помеченным пунктам. Откат: DROP INDEX + DROP COLUMN (пометки теряются, сами пункты нет).

Revision ID: f7d0b3e5c942
Revises: e6c9a2d4b831
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7d0b3e5c942"
down_revision: Union[str, Sequence[str], None] = "e6c9a2d4b831"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nexus_lists", sa.Column("for_sitter", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index("ix_nexus_lists_for_sitter", "nexus_lists", ["user_id"], postgresql_where=sa.text("for_sitter"))


def downgrade() -> None:
    op.drop_index("ix_nexus_lists_for_sitter", table_name="nexus_lists")
    op.drop_column("nexus_lists", "for_sitter")
