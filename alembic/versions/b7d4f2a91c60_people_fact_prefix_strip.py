"""people_fact_prefix_strip

Facts of people used to be stored with a «Name: » prefix (bot context needed the owner of the fact).
Now the text is stored bare and the name is drawn from the person (card, memory list, bot context
all render it at read time). This data migration strips the prefix from existing person facts
(core.people.strip_name_prefix: only when the word before the colon is the person's own name/alias,
so «Важно: …» stays). Meaning is not re-evaluated; no LLM, no network.

Changed rows that had an embedding get it cleared and are queued in `people_reembed_queue`;
`tools/people_import/reembed_stripped.py` re-embeds only those (estimate first, then --apply).
Downgrade drops the queue only: stripped text is still valid, the prefix is not restored.

Revision ID: b7d4f2a91c60
Revises: 7e3b9a41c5d2
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d4f2a91c60'
down_revision: Union[str, Sequence[str], None] = '7e3b9a41c5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from core.people import strip_name_prefix
    op.create_table("people_reembed_queue", sa.Column("memory_id", sa.Integer(), primary_key=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT m.id, m.fact_text, (m.embedding IS NOT NULL) AS has_emb, p.name, p.aliases "
        "FROM memories m JOIN people p ON p.id = m.person_id "
        "WHERE m.fact_text <> '' AND NOT p.is_self")).all()
    for r in rows:
        new = strip_name_prefix(r.fact_text, r.name, r.aliases or [])
        if new == r.fact_text or not new.strip():
            continue
        if r.has_emb:
            conn.execute(sa.text("UPDATE memories SET fact_text = :t, embedding = NULL WHERE id = :i"),
                         {"t": new, "i": r.id})
            conn.execute(sa.text("INSERT INTO people_reembed_queue (memory_id) VALUES (:i) ON CONFLICT DO NOTHING"),
                         {"i": r.id})
        else:
            conn.execute(sa.text("UPDATE memories SET fact_text = :t WHERE id = :i"), {"t": new, "i": r.id})


def downgrade() -> None:
    op.drop_table("people_reembed_queue")
