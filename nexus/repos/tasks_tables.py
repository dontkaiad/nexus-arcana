"""nexus/repos/tasks_tables.py — SQLAlchemy Core table definitions for nexus tasks."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, ForeignKey, MetaData, SmallInteger,
    Table, Text, TIMESTAMP, text,
)

metadata = MetaData()

task_status = Table(
    "task_status", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

task_repeat = Table(
    "task_repeat", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

task_day_of_week = Table(
    "task_day_of_week", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

task_priority = Table(
    "task_priority", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

task_category = Table(
    "task_category", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

tasks = Table(
    "tasks", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id", Text, unique=True),
    Column("title", Text, nullable=False),
    Column("status_id", SmallInteger, ForeignKey("task_status.id"), nullable=False),
    Column("repeat_id", SmallInteger, ForeignKey("task_repeat.id")),
    Column("day_of_week_id", SmallInteger, ForeignKey("task_day_of_week.id")),
    Column("priority_id", SmallInteger, ForeignKey("task_priority.id")),
    Column("category_id", SmallInteger, ForeignKey("task_category.id")),
    Column("deadline", TIMESTAMP(timezone=True)),
    Column("reminder", TIMESTAMP(timezone=True)),
    Column("completed_at", TIMESTAMP(timezone=True)),
    Column("repeat_time", Text),
    Column("parent_task_id", BigInteger, ForeignKey("tasks.id")),
    Column("user_notion_id", Text, nullable=False, server_default=text("''")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
)
