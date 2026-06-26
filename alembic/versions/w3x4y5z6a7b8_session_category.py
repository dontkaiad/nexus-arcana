"""sessions: session_category lookup + category_id FK (phase 1 of #174)

Аддитивная миграция — ничего существующего не трогает:
  - новая lookup-таблица session_category (паттерн из rituals_tables)
  - новая NULLABLE FK-колонка sessions.category_id
  - spread_type остаётся нетронутым (двойная запись начнётся в фазе 2)

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    t_session_category = op.create_table(
        "session_category",
        sa.Column("id",    sa.SmallInteger, primary_key=True, autoincrement=True),
        sa.Column("code",  sa.Text,         nullable=False, unique=True),
        sa.Column("emoji", sa.Text),
        sa.Column("label", sa.Text,         nullable=False),
        sa.Column("sort",  sa.SmallInteger, server_default="0"),
    )
    op.bulk_insert(t_session_category, [
        {"code": "sphere",       "emoji": "🌐", "label": "Сфера жизни",                 "sort": 1},
        {"code": "ancestral",    "emoji": "🌳", "label": "Родовой узел",                "sort": 2},
        {"code": "magical",      "emoji": "⚡", "label": "Магические воздействия",       "sort": 3},
        {"code": "diag_ritual",  "emoji": "🔍", "label": "Диагностика перед ритуалом",   "sort": 4},
        {"code": "diag_ability", "emoji": "✨", "label": "Диагностика способностей",     "sort": 5},
    ])

    op.add_column(
        "sessions",
        sa.Column("category_id", sa.SmallInteger,
                  sa.ForeignKey("session_category.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "category_id")
    op.drop_table("session_category")
