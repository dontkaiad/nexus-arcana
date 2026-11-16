"""works.duration_source: заданная пользователем длительность vs авто-слот Зари

works.duration_min пишут два разных источника: пользователь (длительность дела, «на 2 часа») и авто-слот Зари
(_slot_fields, всегда 60 для ритуала/расклада с явным временем). По значению их не различить: пользовательский
«1 час» тоже даёт 60. Колонка отвечает на это: 'user' — задана пользователем, 'auto' — поставлена слотом.
Только пользовательская длительность сдвигает пинг дедлайна на конец дела (ADR-0043, п. 8).

Существующие строки получают 'auto': пользовательского писателя до этой миграции не было.
Откат: колонка и ограничение удаляются (duration_min остаётся как был).

Revision ID: f5b7c9d1e346
Revises: e4a6b8c0d235
Create Date: 2026-11-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5b7c9d1e346"
down_revision: Union[str, Sequence[str], None] = "e4a6b8c0d235"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "works",
        sa.Column("duration_source", sa.Text(), nullable=False, server_default="auto"),
    )
    op.create_check_constraint(
        "ck_works_duration_source", "works", "duration_source IN ('user', 'auto')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_works_duration_source", "works", type_="check")
    op.drop_column("works", "duration_source")
