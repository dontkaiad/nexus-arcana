"""arcana/repos/clients_tables.py — SQLAlchemy Core metadata for clients domain."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, Date, ForeignKey, MetaData, SmallInteger, Table, Text, text,
)

metadata = MetaData()


def _lookup(name: str) -> Table:
    return Table(
        name, metadata,
        Column("id",    SmallInteger, primary_key=True, autoincrement=True),
        Column("code",  Text, nullable=False, unique=True),
        Column("emoji", Text),
        Column("label", Text, nullable=False),
        Column("sort",  SmallInteger, server_default=text("0")),
    )


client_type   = _lookup("client_type")
client_status = _lookup("client_status")

clients = Table(
    "clients", metadata,
    Column("id",            BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id",     Text, unique=True),
    Column("name",          Text, nullable=False),
    Column("type_id",       SmallInteger, ForeignKey("client_type.id")),
    Column("status_id",     SmallInteger, ForeignKey("client_status.id")),
    Column("birthday",      Date),
    Column("notes",         Text),
    Column("request",       Text),
    Column("contact",       Text),
    Column("photo_url",     Text),
    Column("object_photos", Text),
)
