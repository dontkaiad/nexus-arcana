"""arcana: arcana_triplets — векторный индекс триплетов на pgvector (RAG-миграция #5)

Замена коллекции Qdrant `arcana_triplets` на таблицу pgvector. Самодостаточный
payload для поиска/инъекции голоса + поля под bulk-импорт истории: live-расклад
линкуется к sessions (`session_id`), импорт — `session_id=NULL`, `source='import'`,
`occurred_at` в прошлом. Эмбеддинг — Voyage voyage-4-lite (dim 1024).

ВАЖНО (порядок #5↔#7): upgrade зовёт `CREATE EXTENSION vector` — расширение есть
ТОЛЬКО в образе pgvector/pgvector:pg16 (#7). На ванильном postgres:16 миграция
УПАДЁТ. Поэтому файл готов сейчас, но `alembic upgrade` применять ПОСЛЕ смены
образа (#7). На прод не применять.

История миграций ЛИНЕЙНА: цепляемся на текущую голову u1v2w3x4y5z6
(sessions theme_summary). Таблица `sessions` создаётся выше по цепи
(a1f2e3d4c5b6) → FK на sessions(id) валиден.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa


revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"  # текущая голова (линейная история)
branch_labels = None
depends_on = None


class _Vector(sa.types.UserDefinedType):
    """Минимальный pgvector-тип ТОЛЬКО для DDL этой миграции — без импорта пакета
    `pgvector` (он ставится в #7; здесь нужен лишь `vector(N)` в CREATE TABLE)."""
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"


def upgrade() -> None:
    # pgvector extension (требует образ pgvector/pgvector:pg16 — см. шапку #5↔#7)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "arcana_triplets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # live-расклад → строка sessions (CASCADE чистит индекс при удалении);
        # импорт истории → NULL. UNIQUE = ключ upsert при переиндексации правки
        # (NULL'ы в Postgres не конфликтуют → много импортных строк без session_id).
        sa.Column("session_id", sa.BigInteger,
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                  unique=True, nullable=True),
        sa.Column("embedding", _Vector(1024), nullable=False),   # Voyage voyage-4-lite
        sa.Column("client_id", sa.BigInteger),                   # без FK: историч. клиент вне clients
        sa.Column("session_name", sa.Text),
        sa.Column("occurred_at", sa.Date),                       # РЕАЛЬНАЯ дата расклада (прошлое=импорт)
        sa.Column("question", sa.Text),
        sa.Column("cards", sa.Text),                             # canonical EN, 1-5+ карт (flat)
        sa.Column("bottom_card", sa.Text),
        sa.Column("deck", sa.Text),
        sa.Column("interpretation", sa.Text),                    # авторская трактовка (полная)
        sa.Column("interp_excerpt", sa.Text),                    # огрызок для инъекции тон/стиль
        sa.Column("triplet_summary", sa.Text),                   # Haiku-саммари (в т.ч. под импорт)
        sa.Column("source", sa.Text, nullable=False, server_default="live"),  # live | import
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),  # когда внесён в индекс
    )

    # ANN-индекс косинуса. hnsw + operator class — через raw DDL (op.create_index
    # не умеет operator-class). Cosine = метрика текущего Qdrant.
    op.execute(
        "CREATE INDEX idx_arcana_triplets_embedding "
        "ON arcana_triplets USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index("idx_arcana_triplets_client", "arcana_triplets", ["client_id"])
    op.create_index("idx_arcana_triplets_occurred", "arcana_triplets", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("arcana_triplets")          # сносит и свои индексы
    # В БД nexus_arcana расширение vector создаёт ТОЛЬКО эта миграция → на down
    # его убираем. IF EXISTS — безопасно. (Если позже vector понадобится памяти —
    # этот DROP здесь пересмотреть.)
    op.execute("DROP EXTENSION IF EXISTS vector")
