"""cats_subject

Коты как субъект Памяти (по образцу People, ADR-0027): таблица `cats` + `memories.cat_id`.
Карточка кота (имя, клички, фото) живёт в `cats`, факты о нём — в `memories` с `cat_id`.
`aliases_norm` — нормализованные формы имени и кличек для резолва упоминаний (GIN, тот же нормализатор,
что у people). Всё аддитивно: существующие записи не меняются, `cat_id` nullable, FK ON DELETE SET NULL.
Откат: DROP INDEX + DROP COLUMN memories.cat_id, затем DROP TABLE cats (данные таблицы cats теряются).

Revision ID: c4a7e2b95d13
Revises: b3e7c9a14d58
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "c4a7e2b95d13"
down_revision: Union[str, Sequence[str], None] = "b3e7c9a14d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cats",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("aliases_norm", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("photo_url", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'confirmed'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('pending','confirmed','rejected')", name="ck_cats_status"),
    )
    op.create_index("ix_cats_user", "cats", ["user_id"])
    op.create_index("ix_cats_aliases_norm", "cats", ["aliases_norm"], postgresql_using="gin")

    op.add_column("memories", sa.Column(
        "cat_id", sa.BigInteger(),
        sa.ForeignKey("cats.id", ondelete="SET NULL", name="fk_memories_cat"),
    ))
    op.create_index("ix_memories_cat", "memories", ["cat_id"])


def downgrade() -> None:
    op.drop_index("ix_memories_cat", table_name="memories")
    op.drop_constraint("fk_memories_cat", "memories", type_="foreignkey")
    op.drop_column("memories", "cat_id")
    op.drop_index("ix_cats_aliases_norm", table_name="cats")
    op.drop_index("ix_cats_user", table_name="cats")
    op.drop_table("cats")
