"""sessions: session_summary column (#162)

Общее саммари сессии (по всем триплетам) теперь живёт в модели данных, а не
только в эфемерном session_cache.db. Пишется на якорный (первый) триплет
сессии; читается миниапом как источник истины. Кеш остаётся fast-path'ом.
Additive nullable Text — без потери данных, безопасно.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("session_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "session_summary")
