"""works slice schema

Revision ID: b2f3e4d5c6a7
Revises: a1f2e3d4c5b6
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "b2f3e4d5c6a7"
down_revision = "a1f2e3d4c5b6"
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
    t_priority = _lookup_table("work_priority")
    op.bulk_insert(t_priority, [
        {"code": "urgent",    "emoji": "🔴", "label": "Срочно",      "sort": 1},
        {"code": "important", "emoji": "🟡", "label": "Важно",       "sort": 2},
        {"code": "later",     "emoji": "🟢", "label": "Можно потом", "sort": 3},
    ])

    t_status = _lookup_table("work_status")
    op.bulk_insert(t_status, [
        {"code": "open", "emoji": "🔵", "label": "Открыто", "sort": 1},
        {"code": "done", "emoji": "✅",  "label": "Готово",  "sort": 2},
    ])

    op.create_table(
        "works",
        sa.Column("id",          sa.BigInteger,  primary_key=True, autoincrement=True),
        sa.Column("title",       sa.Text,        nullable=False),
        sa.Column("deadline",    sa.TIMESTAMP(timezone=True)),
        sa.Column("category",    sa.Text),       # "🃏 Расклад" / "✨ Ритуал" / etc.

        sa.Column("priority_id", sa.SmallInteger, sa.ForeignKey("work_priority.id")),
        sa.Column("status_id",   sa.SmallInteger, sa.ForeignKey("work_status.id")),

        sa.Column("client_id",      sa.BigInteger, sa.ForeignKey("clients.id")),
        sa.Column("user_notion_id", sa.Text),

        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_works_status_id",  "works", ["status_id"])
    op.create_index("idx_works_deadline",   "works", ["deadline"])
    op.create_index("idx_works_client_id",  "works", ["client_id"])


def downgrade() -> None:
    op.drop_index("idx_works_client_id",  table_name="works")
    op.drop_index("idx_works_deadline",   table_name="works")
    op.drop_index("idx_works_status_id",  table_name="works")
    op.drop_table("works")
    op.drop_table("work_status")
    op.drop_table("work_priority")
