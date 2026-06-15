"""nexus_lists + arcana_inventory PG tables (lists domain split by Бот)

Revision ID: k1d2e3f4g5h6
Revises: j0c1d2e3f4g5
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "k1d2e3f4g5h6"
down_revision = "j0c1d2e3f4g5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nexus_lists",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("notion_id",      sa.Text, unique=True, nullable=True),
        sa.Column("name",           sa.Text, nullable=False, server_default=""),
        sa.Column("list_type",      sa.Text, nullable=False, server_default="покупки"),
        sa.Column("status",         sa.Text, nullable=False, server_default="not_started"),
        sa.Column("category",       sa.Text, nullable=False, server_default=""),
        sa.Column("quantity",       sa.Numeric, nullable=True),
        sa.Column("note",           sa.Text, nullable=False, server_default=""),
        sa.Column("price_actual",   sa.Numeric, nullable=True),
        sa.Column("price_plan",     sa.Numeric, nullable=True),
        sa.Column("store",          sa.Text, nullable=False, server_default=""),
        sa.Column("priority",       sa.Text, nullable=False, server_default=""),
        sa.Column("group_name",     sa.Text, nullable=False, server_default=""),
        sa.Column("is_recurring",   sa.Boolean, nullable=False, server_default="false"),
        sa.Column("remind_days",    sa.BigInteger, nullable=True),
        sa.Column("expires_at",     sa.Date, nullable=True),
        sa.Column("stage",          sa.BigInteger, nullable=True),
        sa.Column("task_id",        sa.Text, nullable=False, server_default=""),
        sa.Column("works_id",       sa.Text, nullable=False, server_default=""),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nexus_lists_list_type",     "nexus_lists", ["list_type"])
    op.create_index("ix_nexus_lists_status",        "nexus_lists", ["status"])
    op.create_index("ix_nexus_lists_category",      "nexus_lists", ["category"])
    op.create_index("ix_nexus_lists_group_name",    "nexus_lists", ["group_name"])
    op.create_index("ix_nexus_lists_user",          "nexus_lists", ["user_notion_id"])
    op.create_index("ix_nexus_lists_is_recurring",  "nexus_lists", ["is_recurring"])
    op.create_index("ix_nexus_lists_expires_at",    "nexus_lists", ["expires_at"])

    op.create_table(
        "arcana_inventory",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("notion_id",      sa.Text, unique=True, nullable=True),
        sa.Column("name",           sa.Text, nullable=False, server_default=""),
        sa.Column("list_type",      sa.Text, nullable=False, server_default="инвентарь"),
        sa.Column("status",         sa.Text, nullable=False, server_default="not_started"),
        sa.Column("category",       sa.Text, nullable=False, server_default=""),
        sa.Column("quantity",       sa.Numeric, nullable=True),
        sa.Column("note",           sa.Text, nullable=False, server_default=""),
        sa.Column("group_name",     sa.Text, nullable=False, server_default=""),
        sa.Column("is_recurring",   sa.Boolean, nullable=False, server_default="false"),
        sa.Column("remind_days",    sa.BigInteger, nullable=True),
        sa.Column("expires_at",     sa.Date, nullable=True),
        sa.Column("works_id",       sa.Text, nullable=False, server_default=""),
        sa.Column("user_notion_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at",     sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_arcana_inventory_list_type",    "arcana_inventory", ["list_type"])
    op.create_index("ix_arcana_inventory_status",       "arcana_inventory", ["status"])
    op.create_index("ix_arcana_inventory_category",     "arcana_inventory", ["category"])
    op.create_index("ix_arcana_inventory_user",         "arcana_inventory", ["user_notion_id"])
    op.create_index("ix_arcana_inventory_expires_at",   "arcana_inventory", ["expires_at"])


def downgrade() -> None:
    op.drop_table("arcana_inventory")
    op.drop_table("nexus_lists")
