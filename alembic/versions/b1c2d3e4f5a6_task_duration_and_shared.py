"""tasks/works duration_min + shared flag on tasks/nexus_lists (#241, #242)

#241: "мне нужно иметь возможность выставлять длительность всех задач в
букинге или в нексусе/аркане" — busy-калькулятор букинга (core/booking/busy.py)
хардкодил 1ч на любую задачу/работу; теперь берёт из duration_min, если задан.

#242: "флаг shared на задачах/списках, чтобы бот мог сказать «Кай хочет в
кино на выходных»" — Заря подтягивает расшаренные активные задачи в контекст
чата с друзьями (core/shared_items.py).

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("duration_min", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column(
        "shared", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("works", sa.Column("duration_min", sa.Integer(), nullable=True))
    op.add_column("nexus_lists", sa.Column(
        "shared", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("nexus_lists", "shared")
    op.drop_column("works", "duration_min")
    op.drop_column("tasks", "shared")
    op.drop_column("tasks", "duration_min")
