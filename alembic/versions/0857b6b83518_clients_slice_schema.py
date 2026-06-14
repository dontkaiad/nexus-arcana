"""clients slice schema

Revision ID: 0857b6b83518
Revises: 022e99f6431d
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0857b6b83518"
down_revision = "022e99f6431d"
branch_labels = None
depends_on = None


def _lookup_table(name: str) -> sa.Table:
    return op.create_table(
        name,
        sa.Column("id",    sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("code",  sa.Text, nullable=False, unique=True),
        sa.Column("emoji", sa.Text),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("sort",  sa.SmallInteger, server_default="0"),
    )


def upgrade() -> None:
    t_ctype = _lookup_table("client_type")
    op.bulk_insert(t_ctype, [
        {"code": "free",  "emoji": "🎁", "label": "Бесплатный", "sort": 0},
        {"code": "paid",  "emoji": "🤝", "label": "Платный",    "sort": 1},
        {"code": "self",  "emoji": "🌟", "label": "Self",        "sort": 2},
    ])

    t_cstatus = _lookup_table("client_status")
    op.bulk_insert(t_cstatus, [
        {"code": "closed",   "emoji": "⛔", "label": "Закрытый", "sort": 0},
        {"code": "one_time", "emoji": "🌙", "label": "Разовый",  "sort": 1},
        {"code": "active",   "emoji": "🟢", "label": "Активный", "sort": 2},
    ])

    op.create_table(
        "clients",
        sa.Column("id",            sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("notion_id",     sa.Text, unique=True),
        sa.Column("name",          sa.Text, nullable=False),
        sa.Column("type_id",       sa.SmallInteger, sa.ForeignKey("client_type.id")),
        sa.Column("status_id",     sa.SmallInteger, sa.ForeignKey("client_status.id")),
        sa.Column("birthday",      sa.Date),
        sa.Column("notes",         sa.Text),
        sa.Column("request",       sa.Text),
        sa.Column("contact",       sa.Text),
        sa.Column("photo_url",     sa.Text),
        sa.Column("object_photos", sa.Text),
    )
    op.create_index("idx_clients_name",      "clients", ["name"])
    op.create_index("idx_clients_notion_id", "clients", ["notion_id"])


def downgrade() -> None:
    op.drop_table("clients")
    op.drop_table("client_status")
    op.drop_table("client_type")
