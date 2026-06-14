"""nexus tasks — create task lookup tables and tasks table

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-06-14
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "h8c9d0e1f2a3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_status",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO task_status (code) VALUES "
        "('Not started'), ('In progress'), ('Done'), ('Archived')"
    )

    op.create_table(
        "task_repeat",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO task_repeat (code) VALUES "
        "('Нет'), ('Ежедневно'), ('Еженедельно'), ('Ежемесячно')"
    )

    op.create_table(
        "task_day_of_week",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO task_day_of_week (code) VALUES "
        "('Пн'), ('Вт'), ('Ср'), ('Чт'), ('Пт'), ('Сб'), ('Вс')"
    )

    op.create_table(
        "task_priority",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO task_priority (code) VALUES "
        "('⚪️ Можно потом'), "
        "('\U0001f7e1 Важно'), "
        "('\U0001f534 Срочно')"
    )

    op.create_table(
        "task_category",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
    )
    op.execute(
        "INSERT INTO task_category (code) VALUES "
        "('\U0001f465 Люди'), "
        "('\U0001f4b3 Прочее'), "
        "('\U0001f4b0 Зарплата'), "
        "('\U0001f4da Хобби/Учеба'), "
        "('\U0001f3e5 Здоровье'), "
        "('\U0001f4bb Подписки'), "
        "('\U0001f457 Гардероб'), "
        "('\U0001f485 Бьюти'), "
        "('\U0001f695 Транспорт'), "
        "('\U0001f371 Кафе/Доставка'), "
        "('\U0001f35c Продукты'), "
        "('\U0001f6ac Привычки'), "
        "('\U0001f3e0 Жилье'), "
        "('\U0001f43e Коты'), "
        "('\U0001f916 Боты')"
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("notion_id", sa.Text(), unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status_id", sa.SmallInteger(),
                  sa.ForeignKey("task_status.id"), nullable=False),
        sa.Column("repeat_id", sa.SmallInteger(),
                  sa.ForeignKey("task_repeat.id")),
        sa.Column("day_of_week_id", sa.SmallInteger(),
                  sa.ForeignKey("task_day_of_week.id")),
        sa.Column("priority_id", sa.SmallInteger(),
                  sa.ForeignKey("task_priority.id")),
        sa.Column("category_id", sa.SmallInteger(),
                  sa.ForeignKey("task_category.id")),
        sa.Column("deadline", sa.TIMESTAMP(timezone=True)),
        sa.Column("reminder", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("repeat_time", sa.Text()),
        sa.Column("parent_task_id", sa.BigInteger(),
                  sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("user_notion_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_tasks_user_notion_id", "tasks", ["user_notion_id"])
    op.create_index("idx_tasks_status_id", "tasks", ["status_id"])


def downgrade() -> None:
    op.drop_table("tasks")
    op.drop_table("task_category")
    op.drop_table("task_priority")
    op.drop_table("task_day_of_week")
    op.drop_table("task_repeat")
    op.drop_table("task_status")
