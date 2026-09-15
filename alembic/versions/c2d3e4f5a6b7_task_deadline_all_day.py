"""tasks.deadline_all_day — a deadline given without a time is a whole day, not a point

Kai types "до пятницы" with no time → `nexus/handlers/tasks.py:_date_with_tz`
deliberately leaves the deadline as a bare date. `pg_tasks_repo._parse_iso`
then treats that naive date as UTC midnight, and the booking busy-calculator
(`core/booking/busy.py`) hangs its default 1h slot off that instant — which
renders as 03:00-04:00 MSK in the booking web UI / Zarya. This column lets
the write side flag "no time was given" so the busy-calculator can block the
whole local day instead of inventing a fake 1h meeting at a fake time.

Revision ID: c2d3e4f5a6b7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column(
        "deadline_all_day", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("tasks", "deadline_all_day")
