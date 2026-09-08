"""cushion_transactions.source — расширить CHECK (#123 + #208)

Изначальный CHECK: source IN ('manual', 'payday_auto'). Но код давно пишет
и другие значения:
  - 'debt_overpaid'  — переплата по закрытому долгу ушла в подушку (#123,
    и бот `on_overpaid_cushion`, и Mini App `POST /finance/cushion/deposit`)
  - 'windfall_income' — распределение непредвиденного дохода (#208)
На Postgres такой INSERT падал бы по CHECK. Расширяем список.

SQLite (тесты) CHECK не форсит по умолчанию — там расхождения не было видно.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-08
"""
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_OLD = "source IN ('manual', 'payday_auto')"
_NEW = "source IN ('manual', 'payday_auto', 'debt_overpaid', 'windfall_income')"


def upgrade() -> None:
    with op.batch_alter_table("cushion_transactions") as batch:
        batch.drop_constraint("ck_cushion_tx_source", type_="check")
        batch.create_check_constraint("ck_cushion_tx_source", _NEW)


def downgrade() -> None:
    # rows с новыми source не пройдут старый CHECK — сузим их до 'manual'
    op.execute(
        "UPDATE cushion_transactions SET source = 'manual' "
        "WHERE source NOT IN ('manual', 'payday_auto')"
    )
    with op.batch_alter_table("cushion_transactions") as batch:
        batch.drop_constraint("ck_cushion_tx_source", type_="check")
        batch.create_check_constraint("ck_cushion_tx_source", _OLD)
