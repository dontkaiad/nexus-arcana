"""rituals slice schema

Revision ID: 022e99f6431d
Revises:
Create Date: 2026-06-14 02:10:33.977289

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '022e99f6431d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Lookup-table helper (schema is identical for all 5) ───────────────────────

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
    # ── 1. Shared lookup tables ────────────────────────────────────────────────

    t_payment_source = _lookup_table("payment_source")
    op.bulk_insert(t_payment_source, [
        {"code": "barter", "emoji": "🔄", "label": "Бартер",   "sort": 1},
        {"code": "cash",   "emoji": "💵", "label": "Наличные", "sort": 2},
        {"code": "card",   "emoji": "💳", "label": "Карта",    "sort": 3},
    ])

    t_engagement_type = _lookup_table("engagement_type")
    op.bulk_insert(t_engagement_type, [
        {"code": "client",   "emoji": "🤝", "label": "Клиентский", "sort": 1},
        {"code": "personal", "emoji": "🌟", "label": "Личный",     "sort": 2},
    ])

    t_magical_purpose = _lookup_table("magical_purpose")
    op.bulk_insert(t_magical_purpose, [
        {"code": "love_bind",       "emoji": "🔗", "label": "Приворот/Присушка",  "sort": 1},
        {"code": "cut_off",         "emoji": "⚔️", "label": "Развязка/Отсечение", "sort": 2},
        {"code": "destruct_return", "emoji": "🖤", "label": "Деструктив/Возврат", "sort": 3},
        {"code": "finance",         "emoji": "💎", "label": "Финансы",            "sort": 4},
        {"code": "love",            "emoji": "💞", "label": "Любовь",             "sort": 5},
        {"code": "cleanse",         "emoji": "🧹", "label": "Очищение",           "sort": 6},
        {"code": "protect",         "emoji": "🛡️", "label": "Защита",             "sort": 7},
        {"code": "attract",         "emoji": "💫", "label": "Привлечение",        "sort": 8},
        {"code": "other",           "emoji": "🌀", "label": "Другое",             "sort": 9},
    ])

    t_outcome_status = _lookup_table("outcome_status")
    op.bulk_insert(t_outcome_status, [
        {"code": "unverified", "emoji": "⏳",  "label": "Не проверено", "sort": 1},
        {"code": "partial",    "emoji": "〰️", "label": "Частично",     "sort": 2},
        {"code": "negative",   "emoji": "❌",  "label": "Не сработал",  "sort": 3},
        {"code": "positive",   "emoji": "✅",  "label": "Сработал",     "sort": 4},
    ])

    t_ritual_place = _lookup_table("ritual_place")
    op.bulk_insert(t_ritual_place, [
        {"code": "home",      "emoji": "🏠",  "label": "Дома",        "sort": 1},
        {"code": "forest",    "emoji": "🌲",  "label": "Лес",         "sort": 2},
        {"code": "field",     "emoji": "🌾",  "label": "Поле",        "sort": 3},
        {"code": "water",     "emoji": "🌊",  "label": "Водоём",      "sort": 4},
        {"code": "crossroad", "emoji": "🛤️",  "label": "Перекрёсток", "sort": 5},
        {"code": "graveyard", "emoji": "✝️",  "label": "Погост",      "sort": 6},
        {"code": "church",    "emoji": "⛪",  "label": "Церковь",     "sort": 7},
        {"code": "other",     "emoji": "📍",  "label": "Другое",      "sort": 8},
    ])

    # ── 2. Main table ──────────────────────────────────────────────────────────

    op.create_table(
        "rituals",
        sa.Column("id",             sa.BigInteger,    primary_key=True, autoincrement=True),
        sa.Column("title",          sa.Text,          nullable=False),
        sa.Column("occurred_at",    sa.TIMESTAMP(timezone=True)),
        sa.Column("client_id",      sa.BigInteger),   # FK deferred (clients not in PG yet)

        sa.Column("payment_src_id", sa.SmallInteger,
                  sa.ForeignKey("payment_source.id")),
        sa.Column("type_id",        sa.SmallInteger,
                  sa.ForeignKey("engagement_type.id")),
        sa.Column("purpose_id",     sa.SmallInteger,
                  sa.ForeignKey("magical_purpose.id")),
        sa.Column("outcome_id",     sa.SmallInteger,
                  sa.ForeignKey("outcome_status.id")),
        sa.Column("place_id",       sa.SmallInteger,
                  sa.ForeignKey("ritual_place.id")),

        sa.Column("price",          sa.Numeric(10, 2)),
        sa.Column("paid",           sa.Numeric(10, 2), server_default="0"),
        sa.Column("offerings_sum",  sa.Numeric(10, 2)),
        sa.Column("duration_min",   sa.Integer),

        sa.Column("photo_url",   sa.Text),
        sa.Column("forces",      sa.Text),
        sa.Column("structure",   sa.Text),
        sa.Column("consumables", sa.Text),
        sa.Column("offerings",   sa.Text),
        sa.Column("barter_what", sa.Text),
        sa.Column("notes",       sa.Text),

        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )

    op.create_index("idx_rituals_client_id",   "rituals", ["client_id"])
    op.create_index("idx_rituals_occurred_at", "rituals", ["occurred_at"])

    # ── 3. Computed view (debt is always price - paid; never store it) ─────────
    op.execute(
        "CREATE VIEW ritual_debt AS "
        "SELECT id, COALESCE(price, 0) - COALESCE(paid, 0) AS debt FROM rituals"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ritual_debt")
    op.drop_table("rituals")
    op.drop_table("ritual_place")
    op.drop_table("outcome_status")
    op.drop_table("magical_purpose")
    op.drop_table("engagement_type")
    op.drop_table("payment_source")
