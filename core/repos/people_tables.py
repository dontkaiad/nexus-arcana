"""core/repos/people_tables.py — SQLAlchemy Core table for the People domain (ADR-0027).

Mirrors Alembic migration b776c8f51671. NOT the heylark `people` of
core/auth_grants.py — that one lives in the separate `auth` database.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, MetaData, Table, Text,
    TIMESTAMP, text,
)
from sqlalchemy.dialects.postgresql import ARRAY

metadata = MetaData()

PEOPLE_STATUSES = ("pending", "confirmed", "rejected")

people = Table(
    "people", metadata,
    Column("id",          BigInteger, primary_key=True, autoincrement=True),
    Column("user_id",     Text,       nullable=False, server_default=text("''")),
    Column("name",        Text,       nullable=False),
    Column("aliases",     ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("birthday",    Date),
    # False → год неизвестен, в `birthday` стоит заглушка 1904 (ADR-0027).
    Column("birthday_year_known", Boolean, nullable=False, server_default=text("true")),
    Column("notes",       Text),
    Column("status",      Text,       nullable=False, server_default=text("'pending'")),
    # карточка владельца («Я»): не больше одной на user_id (частичный unique в миграции)
    Column("is_self",     Boolean,    nullable=False, server_default=text("false")),
    Column("created_at",  TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",  TIMESTAMP(timezone=True), server_default=text("now()")),
    CheckConstraint("status IN ('pending','confirmed','rejected')", name="ck_people_status"),
)


# Аккаунты человека (миграция *_person_accounts): несколько TG/VK на одного человека.
# is_primary — основной на платформу (не больше одного: частичный unique по (person_id, platform)).
person_accounts = Table(
    "person_accounts", metadata,
    Column("id",          BigInteger, primary_key=True, autoincrement=True),
    Column("user_id",     Text, nullable=False, server_default=text("''")),
    Column("person_id",   BigInteger, nullable=False),     # FK people.id ON DELETE CASCADE — в миграции
    Column("platform",    Text, nullable=False),           # tg | vk
    Column("external_id", BigInteger, nullable=False),
    Column("username",    Text),
    Column("is_primary",  Boolean, nullable=False, server_default=text("false")),
    Column("created_at",  TIMESTAMP(timezone=True), server_default=text("now()")),
    CheckConstraint("platform IN ('tg','vk')", name="ck_person_accounts_platform"),
)
