"""people_self_mentions

The owner as a first-class person (ADR-0027): `people.is_self` (at most one per user) and
mention tracking on memories (`mention_count`, first/last mention date) for facts about the
owner that are deduplicated across chats (the same thing said in 20 chats = one fact x20).

Revision ID: 8e261c43803c
Revises: d55e8b0384a0
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e261c43803c'
down_revision: Union[str, Sequence[str], None] = 'd55e8b0384a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("people", sa.Column("is_self", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index("uq_people_self", "people", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_self"))
    op.add_column("memories", sa.Column("mention_count", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("memories", sa.Column("first_mention_date", sa.Date()))
    op.add_column("memories", sa.Column("last_mention_date", sa.Date()))


def downgrade() -> None:
    op.drop_column("memories", "last_mention_date")
    op.drop_column("memories", "first_mention_date")
    op.drop_column("memories", "mention_count")
    op.drop_index("uq_people_self", table_name="people")
    op.drop_column("people", "is_self")
