"""clients_person_link

Link Arcana clients to People (ADR-0027, PR-8a): `clients.person_id` nullable FK → people,
ON DELETE SET NULL (deleting a person never deletes a client). One person = at most one client
per owner (partial unique). The link is made by the owner (candidates are only suggestions),
except the owner's own `self` client ↔ `people.is_self`, which is linked deterministically.

Revision ID: 15f7c25ec861
Revises: ffaff4f1d731
Create Date: 2026-10-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15f7c25ec861'
down_revision: Union[str, Sequence[str], None] = 'ffaff4f1d731'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clients", sa.Column(
        "person_id", sa.BigInteger(),
        sa.ForeignKey("people.id", ondelete="SET NULL", name="fk_clients_person"),
    ))
    op.create_index("uq_clients_person", "clients", ["user_id", "person_id"], unique=True,
                    postgresql_where=sa.text("person_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_clients_person", table_name="clients")
    op.drop_constraint("fk_clients_person", "clients", type_="foreignkey")
    op.drop_column("clients", "person_id")
