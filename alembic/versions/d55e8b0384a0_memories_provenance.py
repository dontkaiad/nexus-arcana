"""memories_provenance

Where a person-fact came from (ADR-0027): chat id, date of the quoted message, who wrote
the quote (kai | partner | mixed | unknown) and platform (vk | tg). Shown under the quote in
the review UI; lets us audit attribution (47% of the first extraction quoted Kai herself).

Revision ID: d55e8b0384a0
Revises: 44b20ce4483d
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd55e8b0384a0'
down_revision: Union[str, Sequence[str], None] = '44b20ce4483d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("source_chat_id", sa.BigInteger()))
    op.add_column("memories", sa.Column("source_message_date", sa.Date()))
    op.add_column("memories", sa.Column("quote_author", sa.Text()))
    op.add_column("memories", sa.Column("source_platform", sa.Text()))
    op.create_check_constraint(
        "ck_memories_quote_author", "memories",
        "quote_author IS NULL OR quote_author IN ('kai','partner','mixed','unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_memories_quote_author", "memories", type_="check")
    for c in ("source_platform", "quote_author", "source_message_date", "source_chat_id"):
        op.drop_column("memories", c)
