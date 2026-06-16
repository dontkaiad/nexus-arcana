"""works: add archived status code

Revision ID: o5h6i7j8k9l0
Revises: n4g5h6i7j8k9
Create Date: 2026-06-16
"""
from alembic import op

revision = "o5h6i7j8k9l0"
down_revision = "n4g5h6i7j8k9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO work_status (code, emoji, label, sort) "
        "VALUES ('archived', '🗄️', 'Архив', 3) "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM work_status WHERE code = 'archived'")
