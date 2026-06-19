"""sessions + rituals: work_id FK → works (#151)

Связь Работа↔Расклад/Ритуал на PG (кардинальность 1:1, FK не junction).
work_id NULL FK → works.id ON DELETE SET NULL, + индекс, на обеих таблицах.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("sessions", "rituals"):
        op.add_column(table, sa.Column("work_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_work_id",
            table, "works",
            ["work_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"idx_{table}_work_id", table, ["work_id"])


def downgrade() -> None:
    for table in ("sessions", "rituals"):
        op.drop_index(f"idx_{table}_work_id", table_name=table)
        op.drop_constraint(f"fk_{table}_work_id", table, type_="foreignkey")
        op.drop_column(table, "work_id")
