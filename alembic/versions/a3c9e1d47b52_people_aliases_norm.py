"""people_aliases_norm

Mention resolving (ADR-0027, PR-3): `people.aliases_norm text[]` — normalized forms of the name and
«other names» (case endings stripped; short names stored as exact forms, see core/people.py), GIN-indexed
for the `&&` lookup. Backfilled here in pure Python (no LLM, no network); kept in sync by
pg_people_repo on create / PATCH / merge / import.

Revision ID: a3c9e1d47b52
Revises: 15f7c25ec861
Create Date: 2026-10-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision: str = 'a3c9e1d47b52'
down_revision: Union[str, Sequence[str], None] = '15f7c25ec861'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("people", sa.Column("aliases_norm", ARRAY(sa.Text()), nullable=False,
                                      server_default=sa.text("'{}'::text[]")))
    op.create_index("ix_people_aliases_norm", "people", ["aliases_norm"], postgresql_using="gin")
    from core.people import aliases_norm_for
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, aliases FROM people")).all()
    for r in rows:
        conn.execute(sa.text("UPDATE people SET aliases_norm = :n WHERE id = :i"),
                     {"n": aliases_norm_for(r.name, r.aliases or []), "i": r.id})


def downgrade() -> None:
    op.drop_index("ix_people_aliases_norm", table_name="people")
    op.drop_column("people", "aliases_norm")
