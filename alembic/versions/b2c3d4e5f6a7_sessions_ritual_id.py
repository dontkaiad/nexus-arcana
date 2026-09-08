"""sessions: ritual_id — прямая связь расклада с ритуалом (#84)

Мотивация: расклады часто идут «просмотром» до/после ритуала («заключительный
просмотр через 40 дней после приворота»). До сих пор связать сессию раскладов
с конкретной записью 🕯️ Ритуалы было нечем — `work_id` ведёт на плановую
Работу, которой у ритуала может не быть. Теперь у сессии есть nullable
`ritual_id`: парсер проставляет его, когда в тексте есть маркеры «после
ритуала», иначе NULL (расклад — самостоятельная сущность).

NULLABLE, без FK на уровне Python-таблицы (rituals в отдельной MetaData,
как и work_id). Реальный FK + индекс объявлены здесь.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("ritual_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_sessions_ritual_id", "sessions", ["ritual_id"])
    op.create_foreign_key(
        "fk_sessions_ritual_id", "sessions", "rituals",
        ["ritual_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_ritual_id", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_ritual_id", table_name="sessions")
    op.drop_column("sessions", "ritual_id")
