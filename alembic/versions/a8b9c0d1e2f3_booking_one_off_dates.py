"""booking_availability: one-off specific-date windows (#232)

Kai picked "среда" meaning "16 сентября" and got a recurring every-Wednesday
window instead — booking_availability was weekday-only. Adds a nullable
specific_date column; weekday becomes nullable too, exactly one of the two
must be set (CHECK).

Revision ID: a8b9c0d1e2f3
Revises: f7c8b9a0d1e2
Create Date: 2026-09-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "f7c8b9a0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("booking_availability", "weekday", nullable=True)
    op.add_column("booking_availability", sa.Column("specific_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        "booking_availability_day_xor",
        "booking_availability",
        "(weekday IS NOT NULL) <> (specific_date IS NOT NULL)",
    )


def downgrade():
    op.drop_constraint("booking_availability_day_xor", "booking_availability", type_="check")
    op.drop_column("booking_availability", "specific_date")
    op.alter_column("booking_availability", "weekday", nullable=False)
