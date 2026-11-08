"""nexus/repos/tasks_tables.py — SQLAlchemy Core table definitions for nexus tasks."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Integer, MetaData, SmallInteger,
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
    Column("title", Text, nullable=False),
    Column("status_id", SmallInteger, ForeignKey("task_status.id"), nullable=False),
    Column("repeat_id", SmallInteger, ForeignKey("task_repeat.id")),
    Column("day_of_week_id", SmallInteger, ForeignKey("task_day_of_week.id")),
    Column("priority_id", SmallInteger, ForeignKey("task_priority.id")),
    Column("category_id", SmallInteger, ForeignKey("task_category.id")),
    Column("deadline", TIMESTAMP(timezone=True)),
    # дедлайн задан без времени ("до пятницы") — весь день, не точка
    # в 00:00 (см. core/booking/busy.py:_busy_sync).
    Column("deadline_all_day", Boolean, nullable=False, server_default=text("false")),
    Column("reminder", TIMESTAMP(timezone=True)),
    Column("completed_at", TIMESTAMP(timezone=True)),
    Column("repeat_time", Text),
    Column("note", Text),
    Column("parent_task_id", BigInteger, ForeignKey("tasks.id")),
    # #241: минуты — сколько задача реально держит время (для booking busy-калькулятора,
    # core/booking/busy.py). None → дефолт 1ч (DEFAULT_BUSY_MINUTES).
    Column("duration_min", Integer),
    # #242: видно ли задачу Заре для упоминания друзьям ("Кай хочет в кино на выходных").
    Column("shared", Boolean, nullable=False, server_default=text("false")),
    # Задача про конкретного кота (миграция e6c9a2d4b831); NULL — не про кота / про всех.
    Column("cat_id", BigInteger, ForeignKey("cats.id", ondelete="SET NULL")),
    # Задача входит в расписание ситтера (GET /api/care/schedule); миграция c1a7e3b9d204.
    Column("for_sitter", Boolean, nullable=False, server_default=text("false")),
    Column("user_id", Text, nullable=False, server_default=text("''")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
)


# Журнал выполнений: одна строка на каждое выполнение задачи (миграция d5b8f1c3a720).
task_completions = Table(
    "task_completions", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("task_id", BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("done_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("done_by", Text),
    Column("source", Text, nullable=False, server_default=text("'app'")),
    # Цикл расписания, который закрыло это выполнение (NULL — выполнение вне расписания); unique (task_id, cycle_due).
    Column("cycle_due", TIMESTAMP(timezone=True)),
)

# Период ухода: когда ситтер активен (одна строка на владельца).
from sqlalchemy import Date, PrimaryKeyConstraint  # noqa: E402

care_periods = Table(
    "care_periods", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Text, nullable=False, unique=True),
    Column("starts_on", Date, nullable=False),
    Column("ends_on", Date, nullable=False),
    # 'manual' — задал владелец (/care_period, Mini App): переопределение; 'auto' — из дат доступа ситтеров (миграция a7c3e1b9d452).
    Column("source", Text, nullable=False, server_default=text("'manual'")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)

# Одно предупреждение владельцу о просрочке на цикл.
care_alerts = Table(
    "care_alerts", metadata,
    Column("task_id", BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("cycle_due", TIMESTAMP(timezone=True), nullable=False),
    Column("sent_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("task_id", "cycle_due"),
)


# Разовый перенос одного цикла (миграция d2b8f4c0e315): база серии (tasks.reminder) не меняется.
task_overrides = Table(
    "task_overrides", metadata,
    Column("task_id", BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("cycle_due", TIMESTAMP(timezone=True), nullable=False),
    Column("new_due", TIMESTAMP(timezone=True), nullable=False),
    Column("created_by", Text),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("task_id", "cycle_due"),
)
