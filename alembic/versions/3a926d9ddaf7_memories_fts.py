"""memories: fts — Postgres full-text search по 🧠 Память (hybrid search, этап 1)

Сгенерированная колонка `fts` (tsvector, конфигурация 'russian'):
fact_text — вес A, related_to — вес C (имя человека/связь; ниже по приоритету).
key_name в индекс НЕ входит. STORED-генерация сама пересчитывается на
INSERT/UPDATE — пути записи и триггеры не нужны. Tombstone-строки
(fact_text = '') получают пустой вектор.

Откат: DROP INDEX + DROP COLUMN. Данные не теряются (колонка производная).

Revision ID: 3a926d9ddaf7
Revises: b7d4f2a91c60
Create Date: 2026-10-31
"""
from alembic import op

revision = "3a926d9ddaf7"
down_revision = "b7d4f2a91c60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memories ADD COLUMN fts tsvector GENERATED ALWAYS AS ("
        "setweight(to_tsvector('russian', coalesce(fact_text, '')), 'A') || "
        "setweight(to_tsvector('russian', coalesce(related_to, '')), 'C')"
        ") STORED"
    )
    op.execute("CREATE INDEX idx_memories_fts ON memories USING gin (fts)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memories_fts")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS fts")
