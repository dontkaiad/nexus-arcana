"""people_birthday_year_known

ADR-0027 follow-up (PR-4): an explicit `birthday_year_known` flag instead of the
1904 sentinel being implicit knowledge; existing 1904 rows become unknown-year.
Also `rejected` joins the allowed `people.status` values (reject-all).

Revision ID: 103b3b977d45
Revises: b776c8f51671
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '103b3b977d45'
down_revision: Union[str, Sequence[str], None] = 'b776c8f51671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("people", sa.Column(
        "birthday_year_known", sa.Boolean(), nullable=False, server_default=sa.text("true"),
    ))
    op.execute(
        "UPDATE people SET birthday_year_known = false "
        "WHERE birthday IS NOT NULL AND EXTRACT(YEAR FROM birthday) = 1904"
    )
    op.drop_constraint("ck_people_status", "people", type_="check")
    op.create_check_constraint(
        "ck_people_status", "people", "status IN ('pending','confirmed','rejected')",
    )


def downgrade() -> None:
    op.execute("UPDATE people SET status = 'pending' WHERE status = 'rejected'")
    op.drop_constraint("ck_people_status", "people", type_="check")
    op.create_check_constraint("ck_people_status", "people", "status IN ('pending','confirmed')")
    op.drop_column("people", "birthday_year_known")
