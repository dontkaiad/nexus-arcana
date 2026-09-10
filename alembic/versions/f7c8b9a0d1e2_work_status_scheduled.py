"""work_status: add 'scheduled' (🗓️ Запланировано) for booking-linked Works.

#23 / ADR-0026 §3: a confirmed `arcana` booking creates a 🔮 Work with this
status + `scheduled_at`. Non-terminal, so it stays in open-work lists.

Revision ID: f7c8b9a0d1e2
Revises: d4e5f6a7b8c9
"""
from __future__ import annotations

from alembic import op

revision = "f7c8b9a0d1e2"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO work_status (code, emoji, label, sort) "
        "VALUES ('scheduled', '🗓️', 'Запланировано', 4) "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM work_status WHERE code = 'scheduled'")
