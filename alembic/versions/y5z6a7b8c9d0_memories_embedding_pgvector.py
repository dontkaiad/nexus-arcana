"""memories: embedding — векторный поиск по 🧠 Память на pgvector (#184)

Вторая точка использования pgvector после arcana_triplets (v2w3x4y5z6a7):
эта миграция добавляет колонку embedding прямо в существующую таблицу
memories, а не заводит зеркальную таблицу — memory-запись уже атомарна
(одна строка = один факт), upsert/archive мутируют её на месте, дублировать
эту мутацию в отдельной embedding-таблице только вносит риск рассинхрона.

CREATE EXTENSION идемпотентен (IF NOT EXISTS) — безопасен, даже если
arcana_triplets уже создала расширение раньше. Downgrade этой миграции
расширение НЕ трогает: пока jc arcana_triplets тоже жива, дропать vector
нельзя. Если обе таблицы когда-нибудь снесены — отдельная миграция.

Требует образ pgvector/pgvector:pg16 (см. шапку v2w3x4y5z6a7) — на
ванильном postgres:16 CREATE EXTENSION упадёт. На прод применять вместе с
v2w3x4y5z6a7, после смены образа.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa


revision = "y5z6a7b8c9d0"
down_revision = "x4y5z6a7b8c9"
branch_labels = None
depends_on = None


class _Vector(sa.types.UserDefinedType):
    """Минимальный pgvector-тип ТОЛЬКО для DDL этой миграции (см. v2w3x4y5z6a7
    для того же паттерна) — без импорта пакета `pgvector`."""
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # nullable — существующие записи без эмбеддинга до бэкфилла
    # (scripts/migrate_memory_embeddings.py); save_memory() заполняет для
    # новых строк на запись.
    op.add_column("memories", sa.Column("embedding", _Vector(1024), nullable=True))

    op.execute(
        "CREATE INDEX idx_memories_embedding "
        "ON memories USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memories_embedding")
    op.drop_column("memories", "embedding")
