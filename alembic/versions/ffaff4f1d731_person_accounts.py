"""person_accounts

Several accounts per person (ADR-0027, PR-7): a table `person_accounts` replaces
`people.tg_id`, `people.tg_username`, `people.vk_id`. One person can own many TG/VK accounts
(second phone, old profile, VK + TG); `is_primary` marks the main one per platform.
Backfill copies every old id/username into accounts and the migration FAILS if the number of
accounts after does not equal the number of ids before. Downgrade restores the columns from the
PRIMARY accounts (extra accounts have nowhere to go and are dropped with the table).

Revision ID: ffaff4f1d731
Revises: 8e261c43803c
Create Date: 2026-10-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ffaff4f1d731'
down_revision: Union[str, Sequence[str], None] = '8e261c43803c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("person_id", sa.BigInteger(), sa.ForeignKey("people.id", ondelete="CASCADE", name="fk_person_accounts_person"),
                  nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text()),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("platform IN ('tg','vk')", name="ck_person_accounts_platform"),
    )
    op.create_index("uq_person_accounts_ext", "person_accounts", ["user_id", "platform", "external_id"], unique=True)
    op.create_index("uq_person_accounts_primary", "person_accounts", ["person_id", "platform"], unique=True,
                    postgresql_where=sa.text("is_primary"))
    op.create_index("ix_person_accounts_person", "person_accounts", ["person_id"])

    conn = op.get_bind()
    before = conn.execute(sa.text("SELECT count(tg_id) + count(vk_id) FROM people")).scalar_one()
    conn.execute(sa.text(
        "INSERT INTO person_accounts (user_id, person_id, platform, external_id, username, is_primary) "
        "SELECT user_id, id, 'tg', tg_id, tg_username, true FROM people WHERE tg_id IS NOT NULL"))
    conn.execute(sa.text(
        "INSERT INTO person_accounts (user_id, person_id, platform, external_id, username, is_primary) "
        "SELECT user_id, id, 'vk', vk_id, NULL, true FROM people WHERE vk_id IS NOT NULL"))
    after = conn.execute(sa.text("SELECT count(*) FROM person_accounts")).scalar_one()
    if before != after:
        raise RuntimeError("person_accounts backfill mismatch: %s ids before, %s accounts after" % (before, after))
    uname_before = conn.execute(sa.text("SELECT count(tg_username) FROM people")).scalar_one()
    uname_after = conn.execute(sa.text("SELECT count(username) FROM person_accounts")).scalar_one()
    if uname_before != uname_after:
        raise RuntimeError("person_accounts username mismatch: %s before, %s after" % (uname_before, uname_after))

    op.drop_index("uq_people_user_tg", table_name="people")
    op.drop_index("uq_people_user_vk", table_name="people")
    op.drop_column("people", "tg_username")
    op.drop_column("people", "tg_id")
    op.drop_column("people", "vk_id")


def downgrade() -> None:
    op.add_column("people", sa.Column("tg_id", sa.BigInteger()))
    op.add_column("people", sa.Column("tg_username", sa.Text()))
    op.add_column("people", sa.Column("vk_id", sa.BigInteger()))
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE people p SET tg_id = a.external_id, tg_username = a.username FROM ("
        "  SELECT DISTINCT ON (person_id) person_id, external_id, username FROM person_accounts "
        "  WHERE platform = 'tg' ORDER BY person_id, is_primary DESC, id) a WHERE a.person_id = p.id"))
    conn.execute(sa.text(
        "UPDATE people p SET vk_id = a.external_id FROM ("
        "  SELECT DISTINCT ON (person_id) person_id, external_id FROM person_accounts "
        "  WHERE platform = 'vk' ORDER BY person_id, is_primary DESC, id) a WHERE a.person_id = p.id"))
    op.create_index("uq_people_user_tg", "people", ["user_id", "tg_id"], unique=True,
                    postgresql_where=sa.text("tg_id IS NOT NULL"))
    op.create_index("uq_people_user_vk", "people", ["user_id", "vk_id"], unique=True,
                    postgresql_where=sa.text("vk_id IS NOT NULL"))
    op.drop_table("person_accounts")
