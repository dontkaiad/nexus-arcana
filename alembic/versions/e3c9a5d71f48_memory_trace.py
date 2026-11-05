"""memory_trace

Трассировка вопросов к памяти: одна строка на вопрос — исходный текст, разбор (субъект/типы/route), кандидаты FTS и
вектора с рангами, что выбрал реранк, итоговые id, что сделала генерация (или причина fallback), латентность по шагам.
Тексты ФАКТОВ не хранятся — только id (сам вопрос — исходный текст владельца). Читается scripts/trace_show.py.
Откат: DROP TABLE.

Revision ID: e3c9a5d71f48
Revises: d2b8f4c0e315
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "e3c9a5d71f48"
down_revision: Union[str, Sequence[str], None] = "d2b8f4c0e315"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_trace",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("bot", sa.Text(), nullable=False),                      # nexus | arcana | miniapp
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text()),                                  # self | person | cat | ambiguous | unknown_person | unclear
        sa.Column("types", ARRAY(sa.Text())),
        sa.Column("intent", sa.Text()),
        sa.Column("route", sa.Text()),                                    # direct | birthday | hybrid* | unknown_person | none
        sa.Column("kind", sa.Text()),                                     # facts | birthday | none | unknown_person
        sa.Column("parse", JSONB()),                                      # {people:[id], cats:[id], …}
        sa.Column("fts", JSONB()),                                        # [[id, rank]]
        sa.Column("vec", JSONB()),                                        # [[id, rank, cosine]]
        sa.Column("rrf_ids", ARRAY(sa.BigInteger())),
        sa.Column("rerank", JSONB()),                                     # {ran, ids, failed}
        sa.Column("final_ids", ARRAY(sa.BigInteger())),
        sa.Column("gen", JSONB()),                                        # {attempted, ok, reason, check, used_ids, bad_ids}
        sa.Column("latency", JSONB()),                                    # {шаг: мс от предыдущего шага}
        sa.Column("total_ms", sa.Integer()),
        sa.Column("error", sa.Text()),                                    # имя исключения (без текста)
    )
    op.create_index("ix_memory_trace_ts", "memory_trace", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_memory_trace_ts", table_name="memory_trace")
    op.drop_table("memory_trace")
