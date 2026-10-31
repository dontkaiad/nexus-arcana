"""people_domain

People as a first-class domain (ADR-0027): `people` table + `memories.person_id`
FK + `memories.review_status` (pending|confirmed|rejected). Existing memories
become `confirmed` via the column default, so what the bots see is unchanged.

Revision ID: b776c8f51671
Revises: e1f2a3b4c5d6
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b776c8f51671'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("birthday", sa.Date()),
        sa.Column("tg_id", sa.BigInteger()),
        sa.Column("tg_username", sa.Text()),
        sa.Column("vk_id", sa.BigInteger()),
        sa.Column("notes", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('pending','confirmed')", name="ck_people_status"),
    )
    # No unique on name (namesakes are legitimate); identity is tg_id / vk_id.
    op.create_index(
        "uq_people_user_tg", "people", ["user_id", "tg_id"], unique=True,
        postgresql_where=sa.text("tg_id IS NOT NULL"),
    )
    op.create_index(
        "uq_people_user_vk", "people", ["user_id", "vk_id"], unique=True,
        postgresql_where=sa.text("vk_id IS NOT NULL"),
    )
    op.create_index("ix_people_user", "people", ["user_id"])
    op.create_index("ix_people_aliases", "people", ["aliases"], postgresql_using="gin")

    op.add_column("memories", sa.Column(
        "person_id", sa.BigInteger(),
        sa.ForeignKey("people.id", ondelete="SET NULL", name="fk_memories_person"),
    ))
    op.add_column("memories", sa.Column(
        "review_status", sa.Text(), nullable=False, server_default=sa.text("'confirmed'"),
    ))
    op.create_check_constraint(
        "ck_memories_review_status", "memories",
        "review_status IN ('pending','confirmed','rejected')",
    )
    op.create_index("ix_memories_person", "memories", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_memories_person", table_name="memories")
    op.drop_constraint("ck_memories_review_status", "memories", type_="check")
    op.drop_column("memories", "review_status")
    op.drop_constraint("fk_memories_person", "memories", type_="foreignkey")
    op.drop_column("memories", "person_id")
    op.drop_index("ix_people_aliases", table_name="people")
    op.drop_index("ix_people_user", table_name="people")
    op.drop_index("uq_people_user_vk", table_name="people")
    op.drop_index("uq_people_user_tg", table_name="people")
    op.drop_table("people")
