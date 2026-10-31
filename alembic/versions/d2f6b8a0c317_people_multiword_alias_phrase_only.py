"""people: multi-word aliases match only as a whole phrase

`people.aliases_norm` used to index every word of a multi-word «other name» separately, so a
descriptive alias like «муж Оли» made that person resolve on «Оля» (false homonyms). From now on a
multi-word alias contributes only its whole-phrase form (core.people.alias_forms); the person's own
name is still indexed word by word. Pure Python recompute (no LLM, no network), idempotent.

Downgrade recomputes with the previous word-by-word behaviour.

Revision ID: d2f6b8a0c317
Revises: c5e8a2d71f94
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2f6b8a0c317"
down_revision: Union[str, Sequence[str], None] = "c5e8a2d71f94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recompute(conn, phrase_only: bool) -> int:
    from core.people import aliases_norm_for
    rows = conn.execute(sa.text("SELECT id, name, aliases FROM people")).all()
    for r in rows:
        conn.execute(sa.text("UPDATE people SET aliases_norm = :n WHERE id = :i"),
                     {"n": aliases_norm_for(r.name, r.aliases or [], phrase_only), "i": r.id})
    return len(rows)


def upgrade() -> None:
    _recompute(op.get_bind(), True)


def downgrade() -> None:
    _recompute(op.get_bind(), False)
