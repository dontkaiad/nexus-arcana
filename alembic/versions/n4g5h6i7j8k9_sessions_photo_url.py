"""sessions: add photo_url column

Revision ID: n4g5h6i7j8k9
Revises: m3f4g5h6i7j8
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "n4g5h6i7j8k9"
down_revision = "m3f4g5h6i7j8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("photo_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "photo_url")
