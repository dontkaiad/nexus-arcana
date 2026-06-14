"""clients pg native — drop notion_id bridge, add proper FKs

Revision ID: d4f5e6a7b8c9
Revises: c3f4e5d6a7b8
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "d4f5e6a7b8c9"
down_revision = "c3f4e5d6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop notion_id index before dropping column
    op.drop_index("idx_clients_notion_id", table_name="clients")
    op.drop_column("clients", "notion_id")

    # Add FK constraint on rituals.client_id → clients.id (was deferred)
    op.create_foreign_key(
        "fk_rituals_client_id",
        "rituals", "clients",
        ["client_id"], ["id"],
        ondelete="SET NULL",
    )

    # Add FK constraint on sessions.client_id → clients.id
    # (already added as inline FK in sessions migration, but ensure it exists)
    # Works and sessions have FK via DDL; rituals needed this separately.


def downgrade() -> None:
    op.drop_constraint("fk_rituals_client_id", "rituals", type_="foreignkey")
    op.add_column("clients", sa.Column("notion_id", sa.Text))
    op.create_index("idx_clients_notion_id", "clients", ["notion_id"], unique=True)
