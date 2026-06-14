"""nexus notes — create note_tags, notes, note_tag_map tables

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2a3
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "i9d0e1f2g3h4"
down_revision = "h8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_tags",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO note_tags (code) VALUES "
        "('\U0001f4cc Важное'), "
        "('\U0001f3d9️ Места'), "
        "('\U0001f6d2 Покупки'), "
        "('\U0001f3ac Посмотреть')"
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("notion_id", sa.Text(), unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("date", sa.Date()),
        sa.Column("user_notion_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("is_archived", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_notes_user_notion_id", "notes", ["user_notion_id"])
    op.create_index("idx_notes_date", "notes", ["date"])
    op.create_index("idx_notes_is_archived", "notes", ["is_archived"])

    op.create_table(
        "note_tag_map",
        sa.Column("note_id", sa.BigInteger(),
                  sa.ForeignKey("notes.id", ondelete="CASCADE"),
                  nullable=False, primary_key=True),
        sa.Column("tag_id", sa.SmallInteger(),
                  sa.ForeignKey("note_tags.id", ondelete="CASCADE"),
                  nullable=False, primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("note_tag_map")
    op.drop_table("notes")
    op.drop_table("note_tags")
