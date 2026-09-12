"""booking_block: nexus_task_id link (#249)

"я ставлю в букинг что я занята весь день ... у меня нет задачи в нексусе
на это — неудобно, я же на нексус ориентируюсь". Ручной блок (отпуск/"не
беспокоить") в букинге уже блокирует слоты (core/booking/busy.py), но был
невидим в Nexus. Теперь add_block создаёт линкованную задачу в tasks (тот же
паттерн, что confirmed booking → task, core/booking/linkage.py) и хранит её
id здесь, чтобы del_block мог архивировать задачу при снятии блока.

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("booking_block", sa.Column("nexus_task_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("booking_block", "nexus_task_id")
