"""llm_call_log — журнал вызовов LLM (токены, кеш, латентность)

Одна строка на вызов: метка («модуль.функция»), модель, токены (вход / запись в кеш / чтение из кеша / выход),
латентность, успех, TTL кеша, режим мышления. Тексты не хранятся. Откат: DROP TABLE.

Revision ID: a9d3e5f1b672
Revises: f4c7a1d92e58
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a9d3e5f1b672"
down_revision: Union[str, Sequence[str], None] = "f4c7a1d92e58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_call_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("in_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("out_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("cache_ttl", sa.Text()),
        sa.Column("thinking", sa.Text()),
    )
    op.create_index("ix_llm_call_log_ts", "llm_call_log", ["ts"])
    op.create_index("ix_llm_call_log_label_ts", "llm_call_log", ["label", "ts"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_log_label_ts", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_ts", table_name="llm_call_log")
    op.drop_table("llm_call_log")
