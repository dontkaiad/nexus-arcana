"""memory_answer_log — журнал генерации ответов по памяти (этап 4)

Одна строка на попытку сгенерировать ответ: успех или fallback на список с причиной (timeout / api_error /
check_N — номер проверки grounding) и id фактов. Нужен, чтобы по доле fallback за день ослаблять/ужесточать
проверки (scripts/memory_answer_stats.py). Тексты ответов и фактов НЕ хранятся — только id и числа.
Откат: DROP TABLE.

Revision ID: f4c7a1d92e58
Revises: e8a31c5d9b24
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "f4c7a1d92e58"
down_revision: Union[str, Sequence[str], None] = "e8a31c5d9b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_answer_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("bot", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),            # ok | fallback
        sa.Column("reason", sa.Text()),                             # timeout | api_error | check_N
        sa.Column("fact_ids", ARRAY(sa.Integer())),                 # нарушившие id (fallback) либо использованные (ok)
        sa.Column("n_facts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("out_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("out_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.Text()),
    )
    op.create_index("ix_memory_answer_log_ts", "memory_answer_log", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_memory_answer_log_ts", table_name="memory_answer_log")
    op.drop_table("memory_answer_log")
