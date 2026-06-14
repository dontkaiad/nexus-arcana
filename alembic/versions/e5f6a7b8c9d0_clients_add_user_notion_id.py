"""clients: add user_notion_id column

Revision ID: e5f6a7b8c9d0
Revises: d4f5e6a7b8c9
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4f5e6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("user_notion_id", sa.Text))
    op.create_index("idx_clients_user", "clients", ["user_notion_id"])


def downgrade() -> None:
    op.drop_index("idx_clients_user", table_name="clients")
    op.drop_column("clients", "user_notion_id")
