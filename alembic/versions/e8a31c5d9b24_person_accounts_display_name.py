"""person_accounts.display_name

Display name аккаунта в чате (под ним подписывались факты при импорте: «Имя: …»). Нужен, чтобы срезать
такие префиксы и не путать их с чужими именами. Nullable, без бэкфилла (проставляется скриптом
scripts/strip_account_prefixes.py по явному подтверждению). Откат: DROP COLUMN.

Revision ID: e8a31c5d9b24
Revises: d2f6b8a0c317
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e8a31c5d9b24"
down_revision: Union[str, Sequence[str], None] = "d2f6b8a0c317"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("person_accounts", sa.Column("display_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("person_accounts", "display_name")
