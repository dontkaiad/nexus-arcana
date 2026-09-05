"""works: repeat support (recurring rituals/works)

Mirrors the nexus tasks repeat schema (h8c9d0e1f2a3): a ``work_repeat`` lookup,
a ``work_day_of_week`` lookup, plus ``repeat_id`` / ``day_of_week_id`` /
``repeat_time`` columns on ``works``. Same column names/types as ``tasks`` for
schema consistency. Streaks stay out of scope — see ADR-0023.

Revision ID: ab12cd34ef56
Revises: d0e1f2a3b4c5
Create Date: 2026-09-05
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "ab12cd34ef56"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def _lookup_table(name: str) -> sa.Table:
    return op.create_table(
        name,
        sa.Column("id",    sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("code",  sa.Text,         nullable=False, unique=True),
        sa.Column("emoji", sa.Text),
        sa.Column("label", sa.Text,         nullable=False),
        sa.Column("sort",  sa.SmallInteger, server_default="0"),
    )


def upgrade() -> None:
    t_repeat = _lookup_table("work_repeat")
    op.bulk_insert(t_repeat, [
        {"code": "none",    "emoji": "",   "label": "Нет",         "sort": 0},
        {"code": "daily",   "emoji": "🔁", "label": "Ежедневно",   "sort": 1},
        {"code": "weekly",  "emoji": "🔁", "label": "Еженедельно", "sort": 2},
        {"code": "monthly", "emoji": "🔁", "label": "Ежемесячно",  "sort": 3},
    ])

    t_dow = _lookup_table("work_day_of_week")
    op.bulk_insert(t_dow, [
        {"code": "mon", "emoji": "", "label": "Пн", "sort": 1},
        {"code": "tue", "emoji": "", "label": "Вт", "sort": 2},
        {"code": "wed", "emoji": "", "label": "Ср", "sort": 3},
        {"code": "thu", "emoji": "", "label": "Чт", "sort": 4},
        {"code": "fri", "emoji": "", "label": "Пт", "sort": 5},
        {"code": "sat", "emoji": "", "label": "Сб", "sort": 6},
        {"code": "sun", "emoji": "", "label": "Вс", "sort": 7},
    ])

    op.add_column("works", sa.Column(
        "repeat_id", sa.SmallInteger, sa.ForeignKey("work_repeat.id")))
    op.add_column("works", sa.Column(
        "day_of_week_id", sa.SmallInteger, sa.ForeignKey("work_day_of_week.id")))
    op.add_column("works", sa.Column("repeat_time", sa.Text))


def downgrade() -> None:
    op.drop_column("works", "repeat_time")
    op.drop_column("works", "day_of_week_id")
    op.drop_column("works", "repeat_id")
    op.drop_table("work_day_of_week")
    op.drop_table("work_repeat")
