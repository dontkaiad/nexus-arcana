"""people.relation / people.about

«Кто это»: тип связи (друг / семья / коллега / сосед / бывший / другое) и одна строка пояснения. Нужны, чтобы
различать тёзок («Миша Ари — друг, …») в поиске, ответах и контексте бота. Оба поля nullable, без бэкфилла.
Откат: DROP CONSTRAINT + DROP COLUMN.

Revision ID: b3e7c9a14d58
Revises: a9d3e5f1b672
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3e7c9a14d58"
down_revision: Union[str, Sequence[str], None] = "a9d3e5f1b672"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RELATIONS = ("friend", "family", "colleague", "neighbor", "ex", "other")


def upgrade() -> None:
    op.add_column("people", sa.Column("relation", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("about", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_people_relation", "people",
        "relation IS NULL OR relation IN (%s)" % ",".join("'%s'" % r for r in RELATIONS))
    op.create_check_constraint("ck_people_about_len", "people", "about IS NULL OR char_length(about) <= 120")


def downgrade() -> None:
    op.drop_constraint("ck_people_about_len", "people", type_="check")
    op.drop_constraint("ck_people_relation", "people", type_="check")
    op.drop_column("people", "about")
    op.drop_column("people", "relation")
