"""works_add_notes

Details a work-item carries beyond title/category/priority — free text the
Haiku parser could never place anywhere, so it was silently dropped on
create (Kai reported a detailed "работа" saving only title+date).

Revision ID: e1f2a3b4c5d6
Revises: d3e4f5a6b7c8
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("works", sa.Column("notes", sa.Text()))


def downgrade() -> None:
    op.drop_column("works", "notes")
