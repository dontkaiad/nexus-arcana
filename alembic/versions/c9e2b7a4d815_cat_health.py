"""cat_health

Здоровье котов: вес, визиты к ветеринару / прививки, курсы лекарств. Деньги не хранятся (колонок цены нет).
Все три таблицы каскадно удаляются вместе с карточкой кота. Связь с задачами: `task_id` (FK tasks, ON DELETE SET NULL) —
напоминание о визите / приёме лежит в обычной задаче Nexus, а не в отдельном механизме.
Флаги видимости ситтеру: `sitter_visible` (вес, визиты; по умолчанию скрыто), `sitter_hidden` (лекарства; по умолчанию видны).
Миграция только добавляет таблицы; существующие строки не меняются. Откат: DROP TABLE (данные здоровья теряются, коты и задачи нет).

Revision ID: c9e2b7a4d815
Revises: b8d4f2a6c731
Create Date: 2026-11-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9e2b7a4d815"
down_revision: Union[str, Sequence[str], None] = "b8d4f2a6c731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cat_fk() -> sa.Column:
    return sa.Column("cat_id", sa.BigInteger(), sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False)


def _stamps() -> list:
    return [sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))]


def upgrade() -> None:
    op.create_table(
        "cat_weights",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        _cat_fk(),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("weighed_on", sa.Date(), nullable=False),
        sa.Column("grams", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("sitter_visible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_stamps(),
        sa.CheckConstraint("grams BETWEEN 100 AND 30000", name="ck_cat_weights_grams"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 300", name="ck_cat_weights_note"),
        sa.UniqueConstraint("cat_id", "weighed_on", name="uq_cat_weights_day"),
    )
    op.create_index("ix_cat_weights_cat_day", "cat_weights", ["cat_id", sa.text("weighed_on DESC")])

    op.create_table(
        "cat_vet_visits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        _cat_fk(),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'visit'")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("visit_on", sa.Date(), nullable=False),
        sa.Column("clinic", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("next_due", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'planned'")),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("sitter_visible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_stamps(),
        sa.CheckConstraint("kind IN ('visit','vaccine','deworm','flea','other')", name="ck_cat_vet_kind"),
        sa.CheckConstraint("status IN ('planned','done','cancelled')", name="ck_cat_vet_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_cat_vet_title"),
        sa.CheckConstraint("clinic IS NULL OR char_length(clinic) <= 120", name="ck_cat_vet_clinic"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 500", name="ck_cat_vet_note"),
        sa.CheckConstraint("next_due IS NULL OR next_due >= visit_on", name="ck_cat_vet_next"),
    )
    op.create_index("ix_cat_vet_cat_day", "cat_vet_visits", ["cat_id", sa.text("visit_on DESC")])
    op.create_index("ix_cat_vet_task", "cat_vet_visits", ["task_id"], postgresql_where=sa.text("task_id IS NOT NULL"))

    op.create_table(
        "cat_medications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        _cat_fk(),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dose", sa.Text()),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("times_per_day", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("note", sa.Text()),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("sitter_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_stamps(),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_cat_med_name"),
        sa.CheckConstraint("dose IS NULL OR char_length(dose) <= 80", name="ck_cat_med_dose"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 500", name="ck_cat_med_note"),
        sa.CheckConstraint("days IS NULL OR days BETWEEN 1 AND 365", name="ck_cat_med_days"),
        sa.CheckConstraint("times_per_day BETWEEN 1 AND 4", name="ck_cat_med_times"),
        sa.CheckConstraint("status IN ('active','done','cancelled')", name="ck_cat_med_status"),
        sa.CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_cat_med_dates"),
    )
    op.create_index("ix_cat_med_cat_status", "cat_medications", ["cat_id", "status"])
    op.create_index("ix_cat_med_task", "cat_medications", ["task_id"], postgresql_where=sa.text("task_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_table("cat_medications")
    op.drop_table("cat_vet_visits")
    op.drop_table("cat_weights")
