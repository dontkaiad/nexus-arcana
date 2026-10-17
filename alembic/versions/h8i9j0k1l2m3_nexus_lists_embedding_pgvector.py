"""nexus_lists: embedding — векторный поиск по 📦 Инвентарю Nexus на pgvector

Тот же паттерн, что у memories (y5z6a7b8c9d0): колонка прямо в существующей
таблице, без зеркальной embedding-таблицы. Индексируются только строки
list_type='инвентарь' (core/inventory_rag.py), но колонка общая для всей
таблицы — остальные типы списков остаются с NULL.

CREATE EXTENSION идемпотентен. Downgrade расширение НЕ трогает (оно
общее с arcana_triplets и memories). Требует образ pgvector/pgvector:pg16.

Revision ID: h8i9j0k1l2m3
Revises: e1f2a3b4c5d6
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = "h8i9j0k1l2m3"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


class _Vector(sa.types.UserDefinedType):
    """Минимальный pgvector-тип ТОЛЬКО для DDL (см. y5z6a7b8c9d0)."""
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # nullable — существующие позиции без эмбеддинга до бэкфилла
    # (scripts/migrate_inventory_embeddings.py).
    op.add_column("nexus_lists", sa.Column("embedding", _Vector(1024), nullable=True))
    op.execute(
        "CREATE INDEX idx_nexus_lists_embedding "
        "ON nexus_lists USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_nexus_lists_embedding")
    op.drop_column("nexus_lists", "embedding")
