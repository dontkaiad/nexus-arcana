"""tasks: note — свободная заметка к задаче (детали, которые не дедлайн/приоритет)

Мотивация: Haiku извлекал «expense» из ЛЮБОГО упоминания денег в тексте задачи,
даже когда оплата произойдёт в БУДУЩЕМ (в день дедлайна), а не сейчас
(«прийти к нотариусу в среду, оплата 1250» → лишний расход датой сегодня).
Теперь такое упоминание кладётся в tasks.note, а при выполнении задачи
превращается в реальную finance-транзакцию (см. nexus/handlers/tasks.py
task_done → expense_from_task_note).

NULLABLE Text: старые задачи и задачи без денежных деталей работают как раньше.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("note", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "note")
