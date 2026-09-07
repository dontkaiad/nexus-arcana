"""rituals: consumables_written_off — момент списания расходников из инвентаря

Мотивация: после ритуала с непустым полем «Расходники» бот предлагает списать
их из 📦 Инвентаря (arcana/handlers/ritual_writeoff.py). Факт списания нигде
не сохранялся — Mini App не мог показать «расходники списаны». Теперь на
подтверждение списания ставится timestamp, а Mini App отдаёт булев флаг (#8).

NULLABLE TIMESTAMP: старые ритуалы и ритуалы без расходников — как раньше.

Revision ID: f1a2b3c4d5e6
Revises: e067a1b2c3d4
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e067a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rituals",
        sa.Column("consumables_written_off", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rituals", "consumables_written_off")
