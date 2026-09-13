"""cushion_transactions.source — добавить 'overflow' (#259)

Перерасход агрегатного лимита (🏠 Бюджет на жизнь / 🚬 Привычки) теперь
списывается прямо из подушки (nexus/handlers/finance.py:_check_budget_limit,
`add_to_balance(user_id, -over, source="overflow", ...)`) — это ЕДИНСТВЕННЫЙ
существующий источник, где add_to_balance получает ОТРИЦАТЕЛЬНУЮ сумму
(все остальные source — депозиты). Без этой миграции INSERT падал бы по
CHECK на Postgres (SQLite в тестах CHECK не форсит — расхождение было бы не
видно, тот же класс проблемы, что чинила c3d4e5f6a7b8).

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-09-14
"""
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None

_OLD = "source IN ('manual', 'payday_auto', 'debt_overpaid', 'windfall_income')"
_NEW = "source IN ('manual', 'payday_auto', 'debt_overpaid', 'windfall_income', 'overflow')"


def upgrade() -> None:
    with op.batch_alter_table("cushion_transactions") as batch:
        batch.drop_constraint("ck_cushion_tx_source", type_="check")
        batch.create_check_constraint("ck_cushion_tx_source", _NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE cushion_transactions SET source = 'manual' WHERE source = 'overflow'"
    )
    with op.batch_alter_table("cushion_transactions") as batch:
        batch.drop_constraint("ck_cushion_tx_source", type_="check")
        batch.create_check_constraint("ck_cushion_tx_source", _OLD)
