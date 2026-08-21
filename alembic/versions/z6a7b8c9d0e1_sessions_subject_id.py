"""sessions: subject_id — canonical person anchor via core.memory (#189)

Проблема: session_name — свободная строка, которую Haiku сочиняет заново на
каждое сообщение («Вадим», «Вадим — отношения», «Вадим — диагностика ситуации»
— один и тот же человек, три разные группы). canonical_session_name() ловит
только точные (ilike) повторы написания, не смысловые дубли.

Решение: subject_id — устойчивый якорь на конкретную запись в core.memory
(таблица memories, ADR-0005), НЕ завязанный на то, как в этот раз написали
session_name. Проставляется через диалог подтверждения с ботом (см.
arcana/handlers/sessions.py, _maybe_prompt_subject_match), NULLABLE — сессии
без распознанного субъекта работают как раньше, по (session_name, client_id).

FK на memories.id намеренно НЕ объявлен в arcana/repos/sessions_tables.py:
`memories` живёт в отдельной SQLAlchemy MetaData (core/repos/memories_table.py),
кросс-metadata ForeignKey там не резолвится без ручного связывания реестров.
Реальный constraint — здесь, на уровне DDL (независим от Python-метаданных).
ON DELETE SET NULL: удаление записи памяти не должно ронять расклады, просто
разрывает якорь — сессия откатывается к группировке по session_name.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "z6a7b8c9d0e1"
down_revision = "y5z6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "subject_id", sa.BigInteger,
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_sessions_subject_id", "sessions", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_subject_id", table_name="sessions")
    op.drop_column("sessions", "subject_id")
