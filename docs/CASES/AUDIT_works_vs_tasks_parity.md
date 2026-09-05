# AUDIT: Работы (Arcana) vs Задачи (Nexus) — паритет

Read-only аудит. Код не менялся. Цель: проверить утверждение спеки
«Работы = Задачи Nexus с полями практики» против реального кода и
найти реальные пробелы.

Verify against code:
- `nexus/handlers/tasks.py`, `nexus/handlers/streaks.py`, `nexus/handlers/reply_update.py`
- `nexus/repos/tasks_tables.py`, `nexus/repos/tasks_repo.py`, `nexus/repos/pg_tasks_repo.py`
- `arcana/handlers/works.py`, `arcana/handlers/work_preview.py`, `arcana/handlers/work_reminder_kb.py`, `arcana/handlers/reply_update.py`
- `arcana/repos/works_tables.py`, `arcana/repos/works_repo.py`, `arcana/repos/pg_works_repo.py`
- `core/subtasks_handler.py`, `core/reminder_scheduler.py`, `core/task_streaks.py`, `core/reply_update.py`, `core/pagination.py`, `core/work_relation.py`, `core/list_manager.py`

## 1. Вывод коротко

Спека верна для **создания/CRUD/приоритетов/дедлайнов/reply-правки/подзадач**.
Спека **неверна** для **стриков и repeat** — этих механик в Работах нет ни в
коде, ни в схеме БД (`works` table просто не содержит колонок под них).
Общий модуль `core/reminder_scheduler.py` уже используется Арканой, но
**не** Nexus (Nexus держит собственную внутреннюю копию) — направление
реюза для дедлайнов/напоминаний фактически обратное тому, что говорит
спека.

## 2. Feature-матрица

| Механика | Nexus (Задачи) | Arcana (Работы) | Статус |
|---|---|---|---|
| Haiku-приоритеты 🔴🟡⚪ | `nexus/handlers/tasks.py:62,70-76` (`_PRIORITY_ICONS`, `_priority_display`), парсинг в Haiku-промпте `:1937-1999`, сортировка `:3226,3277` (`_priority_rank`) | `arcana/handlers/works.py:52-62,142-156`; `arcana/handlers/work_preview.py:46,165-180,233-238` | **Есть**, полный паритет — те же 3 уровня, те же эмодзи, тот же Haiku-driven парсинг паттерн |
| Стрики (per-task / global) + rest-day | Global: `nexus/handlers/streaks.py` (`update_streak`, `get_streak`, `request_rest_day`, `is_rest_day_available`). Per-task: `core/task_streaks.py` (`update_task_streak`, `reset_broken_streaks`), вызывается из `tasks.py:1415-1436` (recurring done) и `:2217-2227` (`_update_streak_line`, обычное done) | — | **Отсутствует полностью.** 0 упоминаний `streak`/`task_streaks` в `arcana/handlers/works*.py` или `work_reminder_kb.py`. `core/task_streaks.py` уже написан generic (per `user_id, task_id`, не завязан на Nexus) — технически подключаемо, но никто не вызывает |
| Repeat + repeat_time | Схема: `nexus/repos/tasks_tables.py:47,54` (`repeat_id` FK, `repeat_time` Text). Логика: `_parse_repeat_time` (`tasks.py:1186`), `_next_cycle_date` (`:1217`), recurring reset/reminder/deadline done (`:1279-1445`), oживление на старте (`restore_reminders_on_startup` проходы 2-3, `:286-469`) | — | **Отсутствует на уровне схемы.** `arcana/repos/works_tables.py:34-51` — таблица `works` НЕ содержит `repeat_id`/`repeat_time` вообще. Это не «функция не подключена», это «колонки физически нет» — портирование требует миграции Alembic, не только handler-кода |
| Дедлайн + напоминание (apscheduler) | Внутренняя реализация в самом `tasks.py`: `_schedule_reminder` (`:562`), `_schedule_deadline_check` (`:623`), `init_scheduler`/`_remove_task_jobs` (`:265-284`). **Не мигрировано** на `core/reminder_scheduler.py` — сам модуль это документирует (`core/reminder_scheduler.py:4-6`: «Nexus tasks.py пока сохраняет своё внутреннее использование... миграция — отдельная задача») | Использует общий `core.reminder_scheduler.ReminderScheduler(callback_prefix="work")`: инстанс `arcana_reminder_flow` вызывается из `arcana/handlers/work_reminder_kb.py:105-192` и `arcana/handlers/reply_update.py:89-104` (авто-напоминание при выставлении дедлайна reply'ем) | **Есть с обеих сторон, но НЕ общий код.** Аркана уже сидит на `core/reminder_scheduler.py`, Nexus — нет. Реюз-долг лежит на стороне Nexus, а не Арканы |
| /today и /tasks листинг с пагинацией | `_build_today_digest` (`tasks.py:3213-3408`) — группировка overdue/today, сортировка по времени+приоритету, стрик-строка, no-op пагинация (весь дайджест — одно сообщение, `core/pagination.py` не используется в `tasks.py` вообще — реально задействован в `finance.py`, `memory.py`, `notes.py`) | `handle_works_list` (`arcana/handlers/works.py:135-156`) — плоская группировка по приоритету, без overdue-разбивки, без сортировки по времени, без стрик-строки, без пагинации | **Частично.** Ни одна сторона не использует `core/pagination.py` для списка задач/работ (значит «пагинация» из спеки — фактическая неточность, её нет вообще нигде в этом слое). Работы уступают по фичам вывода: нет отдельного «сегодня vs просрочено», нет time-first сортировки |
| Подзадачи + авто-завершение | Кнопка «📋 Подзадачи» — общий `core/subtasks_handler.py:make_subtasks_router()`, `rel_type="task"`. Текстовый чек пункта в чате: `nexus/handlers/lists.py:1221-1231` (`handle_list_checklist_toggle` → `_repo.checklist_toggle` → `core/list_manager.py:521-554`) — при `group_complete=True` шлёт «🎉 Чеклист завершён!» | Кнопка — тот же общий `core/subtasks_handler.py`, `rel_type="work"` (см. модуль docstring: «Раньше в Arcana не работал. Теперь — общий handler»). **Нет** аналога `handle_list_checklist_toggle` в `arcana/handlers/lists.py` — там есть только inline-кнопочный `on_list_toggle` (`:370`), никакого текстового чек-флоу с `group_complete`-сообщением. **✅ FIXED (b28ab37)**: `on_checkout` (`arcana/handlers/lists.py`) теперь детектит `group_complete` и триггерит `on_complete_work` → `mark_work_done`, реально закрывая родительскую Работу — паритет с Nexus | **Частично.** Кнопка создания подзадач — полный паритет (общий код). Текстовый чек-флоу с «чеклист завершён» — Nexus-only. Важно: «авто-завершение» в обоих случаях — это ТОЛЬКО поздравительное сообщение, `group_complete` НИГДЕ не триггерит смену статуса родительской задачи/работы на Done — авто-завершения родителя как такового нет ни у кого (спека переоценивает эту механику) |
| Reply-правка карточки (B8) | `nexus/handlers/reply_update.py:90-178` → общий `core/reply_update.py` (`parse_reply`, `apply_updates`); mapping регистрируется при создании (`tasks.py:2697-2713`, `save_message_page`) | `arcana/handlers/reply_update.py:21-134` → тот же общий `core/reply_update.py`; mapping регистрируется в `arcana/handlers/work_preview.py:609-620` | **Есть с обеих сторон, паритет.** Оба хендлера — тонкие обёртки над одним `core/reply_update.py`, различаются только доменными спецкейсами (Nexus: перенос в заметки #188/#192; Arcana: авто-напоминание при выставлении дедлайна reply'ем, session-триплет correction). **B8 похоже устарел** — на момент аудита оба пути идут через общий модуль и оба регистрируют `message_pages`; специфического «Nexus reply не распознаётся» кода/бага в текущей версии не найдено. Рекомендация: если B8 всё ещё воспроизводится вручную — завести issue с repro (могла быть регрессия после того, как писался баг), в текущем коде причины не видно |

## 3. Что НЕ мигрировать в Работы бездумно (Arcana-специфика)

Эти вещи спека НЕ должна трактовать как «недостающий паритет» — они
намеренно другие:

- **Client-relation** (`works.client_id`, `has_client`) — у Задач Nexus
  этого поля нет и не должно быть; Работы CRM-привязаны к клиенту.
- **Категории практики** (`🃏 Расклад`/`✨ Ритуал`/`📱 Соцсети`/`🛒 Расходники`/
  `📚 Обучение`/`🗂️ Прочее`, `arcana/handlers/works.py:43-50`) — доменный
  список, не годится как generic task category.
- **Авто-relation Работа↔Ритуал/Расклад** (`core/work_relation.py`) —
  находит открытую Работу клиента нужной категории и закрывает её как
  Done при создании ритуала/расклада (`find_active_work_for_client`,
  `set_event_work_id`, `close_work_as_done`). У Задач Nexus нет понятия
  «событие, которое закрывает задачу» — это чисто CRM-паттерн практики.
- **Бартер/оплата на Работе** — вне периметра этого аудита (не относится
  к паритету с Задачами), но физически завязано на `works` через
  `work_id` на sessions/rituals — ещё одна причина не трогать схему
  `works` бездумно при портировании repeat/streak колонок.

## 4. Баги, найденные по пути (НЕ чинить, только список)

1. `core/task_streaks.py` — generic по сигнатуре (`user_id, task_id, ...`),
   но подключён только из `nexus/handlers/tasks.py`. Если появится
   репорт «стрик работы не считается» — это не баг, это отсутствие вызова.
2. `handle_works_list` (`arcana/handlers/works.py:135-156`) не различает
   overdue vs today (в отличие от `_build_today_digest` у Nexus) — при
   росте списка открытых Работ это может маскировать просроченные.
3. `core/pagination.py` не подключена ни к `handle_tasks_today`, ни к
   `handle_works_list` — у обоих список рискует стать одним гигантским
   сообщением при большом количестве открытых задач/работ (Telegram
   4096-символьный лимit на сообщение не проверялся в рамках аудита).
4. `core/reminder_scheduler.py` docstring прямо фиксирует технический
   долг: миграция Nexus `tasks.py` на общий модуль — открытая, никем не
   тронутая задача. Два независимых apscheduler-flow с одинаковой логикой
   (`schedule_reminder`/`schedule_deadline_check`) — риск разъехаться при
   будущих правках (уже видно лёгкое расхождение: Nexus использует
   отдельные `job_id` префиксы `reminder_`/`deadline_` с суффиксом
   task_id напрямую в `tasks.py`, тогда как `core/reminder_scheduler.py`
   делает то же самое — совпадение вручную поддерживается, не гарантировано).

## 5. Рекомендации: reuse (import) vs дублировать вручную

**Импортировать напрямую (без изменений):**
- `core/subtasks_handler.py` — уже общий, работает.
- `core/reply_update.py` — уже общий, работает.
- `core/task_streaks.py` — уже generic по сигнатуре; для Работ достаточно
  вызвать `update_task_streak(uid, work_id, title, repeat_kind, today)` из
  `arcana/handlers/work_reminder_kb.py:work_complete` **если** решите
  портировать стрики — код-реюз без изменений модуля.
- `core/reminder_scheduler.py` — уже используется Арканой; Nexus should
  migrate onto it (устранит дублирование, а не наоборот).
- `core/pagination.py` — подключить к `handle_works_list` и/или
  `handle_tasks_today` тем же паттерном, что в `finance.py`/`memory.py`/`notes.py`.

**Дублировать вручную (разная структура данных, не общий код):**
- Repeat/repeat_time — требует новых колонок в `works` (Alembic-миграция,
  `work_repeat` lookup table по аналогии с `task_repeat`) + собственная
  копия `_parse_repeat_time`/`_next_cycle_date`-подобной логики, адаптированной
  под категории практики (Ритуал раз в месяц ≠ Задача «Пить воду» ежедневно —
  семантика периода может отличаться и её стоит явно продумать, не копипастить).
- `_build_today_digest` vs `handle_works_list` — не объединять в один
  generic renderer «в лоб»: у Работ есть клиент/категория практики,
  у Задач — repeat/стрик-строка. Общий каркас (overdue/today split,
  сортировка time-first, priority rank) можно вынести в `core/`, но
  формат строки-элемента должен остаться per-domain callback (как уже
  сделано в `core/pagination.py:formatter`).

## 6. Не проверено в рамках этого аудита

- Mini App backend routes (`miniapp/backend/routes/*`) для Работ/Задач —
  вне периметра задачи (только Telegram-handler слой).
- Фактическое ручное воспроизведение B8 — вывод «похоже устарел» сделан
  по чтению кода, не по интерактивному тесту в боте.
