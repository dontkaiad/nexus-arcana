"""nexus_lists_location

Место пункта инвентаря («верхний ящик», «шкаф в коридоре»): `nexus_lists.location` (text, NULL = не указано). Нужно ситтеру
для ответа на «где лежит …». Существующие строки не меняются (NULL). Откат: DROP COLUMN (указанные места теряются).

Revision ID: f4a8c2d6e913
Revises: e3c9a5d71f48
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4a8c2d6e913"
down_revision: Union[str, Sequence[str], None] = "e3c9a5d71f48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nexus_lists", sa.Column("location", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("nexus_lists", "location")
