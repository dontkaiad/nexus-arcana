"""people_alias_ok

«Тёзки, ок»: владелец пометил, что общее «другое имя» у двух людей — не ошибка. Пара (a < b) + ключ имени
(core.people.alias_key). Новое совпадение (другой человек или другое имя) в пару не входит и подсвечивается снова.

Revision ID: c5e8a2d71f94
Revises: 3a926d9ddaf7
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e8a2d71f94'
down_revision: Union[str, Sequence[str], None] = '3a926d9ddaf7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people_alias_ok",
        sa.Column("person_a", sa.BigInteger(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_b", sa.BigInteger(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("person_a", "person_b", "alias_key"),
        sa.CheckConstraint("person_a < person_b", name="ck_people_alias_ok_order"),
    )
    op.create_index("ix_people_alias_ok_b", "people_alias_ok", ["person_b"])


def downgrade() -> None:
    op.drop_index("ix_people_alias_ok_b", table_name="people_alias_ok")
    op.drop_table("people_alias_ok")
