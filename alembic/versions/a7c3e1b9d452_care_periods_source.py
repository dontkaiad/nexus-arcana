"""care_periods_source

Источник периода ухода: `care_periods.source` = 'manual' (владелец задал /care_period или Mini App — переопределение) или 'auto'
(вычислен из дат доступа ситтеров в cats и присылается отдельным токеном). Существующие строки — 'manual' (их задал владелец).
Автосинхронизация никогда не перезаписывает 'manual'. Откат: DROP COLUMN (пометка источника теряется, сами периоды нет).

Revision ID: a7c3e1b9d452
Revises: f4a8c2d6e913
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7c3e1b9d452"
down_revision: Union[str, Sequence[str], None] = "f4a8c2d6e913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("care_periods", sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'manual'")))
    op.create_check_constraint("care_periods_source_check", "care_periods", "source IN ('manual', 'auto')")


def downgrade() -> None:
    op.drop_constraint("care_periods_source_check", "care_periods", type_="check")
    op.drop_column("care_periods", "source")
