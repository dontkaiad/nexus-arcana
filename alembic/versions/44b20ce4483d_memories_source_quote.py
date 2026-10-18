"""memories_source_quote

ADR-0027 (PR-6): the quote a fact was extracted from, shown on tap in the review UI
("is it true or a joke?"). Nullable; manual and legacy facts have none.

Revision ID: 44b20ce4483d
Revises: 103b3b977d45
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44b20ce4483d'
down_revision: Union[str, Sequence[str], None] = '103b3b977d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("source_quote", sa.Text()))


def downgrade() -> None:
    op.drop_column("memories", "source_quote")
