"""grimoire slice schema

Revision ID: c3f4e5d6a7b8
Revises: b2f3e4d5c6a7
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "c3f4e5d6a7b8"
down_revision = "b2f3e4d5c6a7"
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
    t_cat = _lookup_table("grimoire_category")
    op.bulk_insert(t_cat, [
        {"code": "spell",  "emoji": "📿", "label": "Заговор",    "sort": 1},
        {"code": "recipe", "emoji": "🧴", "label": "Рецепт",     "sort": 2},
        {"code": "combo",  "emoji": "✨", "label": "Комбинация", "sort": 3},
        {"code": "note",   "emoji": "📝", "label": "Заметка",    "sort": 4},
    ])

    op.create_table(
        "grimoire_entries",
        sa.Column("id",          sa.BigInteger,  primary_key=True, autoincrement=True),
        sa.Column("title",       sa.Text,        nullable=False),
        sa.Column("category_id", sa.SmallInteger, sa.ForeignKey("grimoire_category.id")),
        sa.Column("themes",      sa.Text),   # comma-separated display labels
        sa.Column("verified",    sa.Boolean,     server_default="false"),
        sa.Column("text",        sa.Text),
        sa.Column("source",      sa.Text),
        sa.Column("user_notion_id", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_grimoire_category_id",   "grimoire_entries", ["category_id"])
    op.create_index("idx_grimoire_user",          "grimoire_entries", ["user_notion_id"])


def downgrade() -> None:
    op.drop_index("idx_grimoire_user",        table_name="grimoire_entries")
    op.drop_index("idx_grimoire_category_id", table_name="grimoire_entries")
    op.drop_table("grimoire_entries")
    op.drop_table("grimoire_category")
