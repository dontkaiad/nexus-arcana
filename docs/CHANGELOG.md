# Changelog

Все заметные изменения проекта `nexus-arcana`.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует semver-подобной нумерации волн (Nexus vN.x, Arcana vN.x,
Lists vN.x, Memory vN.x).

## [Unreleased]

### Added
- 🔌 MCP: создание и перенос задачи теперь предупреждают о пересечении с бронью друга: в ответе `conflicts` (только дата и время), в закрытый топик алерт с именем; длительность по правилу `busy.py` (60 минут или `duration_min`, без времени весь день), считаются только брони `pending` и `confirmed`, не прошедшие, владельца; задача создаётся и переносится в любом случае, брони и расчёт занятости не меняются; ADR-0039.
- 🔌 MCP: запись для kai-mcp по отдельному токену `MCP_WRITE_TOKEN`: `POST /api/mcp/tasks` (создать задачу) и `POST /api/mcp/tasks/{id}/reschedule` (перенести срок), только эти два маршрута, лимиты 20 и 30 в сутки, алерт в лог-группу; нельзя менять название, заметку, статус и удалять, нельзя переносить чужие, закрытые, повторяющиеся, ситтера, кошачьи и привязанные к брони задачи; ADR-0039 (`docs/CASES/0039-mcp-write-tasks.md`).
- 🔌 MCP: `GET /api/arcana/reminders` для kai-mcp (под тем же Bearer-шлюзом): открытые работы практики на сегодня и ближайшие `days` дней плюс просроченные, в каждом элементе только пять ключей (время, вид, категория, приоритет, просрочено), без клиентов, заголовков, заметок и id; просрочка по календарной дате как в списке работ; ADR-0038.
- 🔌 MCP: `GET /api/tasks` читается по Bearer-токену `MCP_READ_TOKEN` для своего MCP-сервера (kai-mcp); шлюз висит только на этом маршруте, чужая схема Authorization идёт обычным входом, задачи «Практика» и «Расходники» не отдаются, ADR-0038 (`docs/CASES/0038-mcp-read-tasks.md`).
- 🐾 Коты: владелец размечает «Неразобранное» из приложения cats: `GET /api/cat-health/facts/unsorted` и `PATCH /api/cat-health/facts/{id}/block` под токеном здоровья (токен владельца).
- 🐾 Коты: ситтер видит факты только открытых блоков (cat-care, /ask, читающий токен `/api/cats`); `blocks` в cat-care; закрыта дыра: /ask отдавал факты карточки «Я» про котов; лимиты 600 знаков / 40 общих фактов.
- 🐾 Коты: блок факта (profile | care | health | relations | owner | unsorted) определяет, кто его видит; ставится в том же вызове Haiku, NULL = закрыто, владелец размечает `PATCH /api/cats/facts/{id}/block`; миграция `d3f5a7c9e124`; кейс 0037, эвал `cat_block_eval`.
- 🧠 Память: кейс 0035 — «Я» разобрана по цитате (автор + лицо), а не по роду: категории (ключевые слова + Haiku), перевод текстов в ж.р. со строгим валидатором, дедуп без LLM; ссылки на `docs/CASES/0035-self-attribution.md`.
- 🔮 Работы-как-задачи: self-клиент «Кай» авто-создаётся при первом личном расклад/ритуале ([#154]); done-оркестратор — любое выполненное событие без открытой Работы заводит и закрывает её (`link_practice_record`) ([#154]); вопрос `[🌟 Себе] / [👤 Для клиента]` только при полной неоднозначности ([#154]); Работы клиента на карточке (бот + Mini App, табы Активные/Завершённые, `list_by_client`) ([#154]).
- 🃏 Расклады: `sessions.ritual_id` — связь расклада-просмотра с ритуалом (маркеры «после ритуала / спустя N дней»), обе стороны в Mini App (`from_ritual` / `linked_sessions`); миграция `b2c3d4e5f6a7` ([#84]).
- 🧠 Память: категория `🎭 Фигуранты` — словарь «кодовое слово → фигурант», парсер сессий Арканы тянет её в промпт (`get_figurant_facts`), `session_name` = `{Клиент} — {кодовое} ({фигурант})` ([#85]).
- 🗒️ Списки: Mini App — тапабельные карточки Покупок/Инвентаря → шит правки (магазин / заметка / категория), `PATCH /api/lists/{id}` ([#45]); Arcana 🛒 Покупки + 📋 Чеклисты по контексту ([#45]).
- 🎨 Единая типо-шкала: CSS-токены `--t-*` + JS-зеркало `T`, 3-уровневая иерархия во всём Mini App ([#60]).
- 🧠 Память Mini App: RAG semantic-fallback (Voyage + Haiku-реранк) в `GET /api/memory` при <3 ILIKE-хитах; создание записи из FAB через общее ядро бота (`parse_and_store`), уведомление в Nexus; обратимая «неактуально» (`PATCH /api/memory/{id}`, `include_inactive=1`) ([#6]).
- 🔮 Работы / 📖 Гримуар: создание из Mini App — структурные формы `POST /api/arcana/works` и `POST /api/arcana/grimoire` (без Haiku), `/api/categories?type=work|grimoire` ([#203]).
- 🃏 Расклады / 🕯 Ритуалы: финансы на карточках Mini App — `debt` / `source` / `barter_what`, общий `core.payment.source_label` ([#7], [#8]).
- 🕯 Ритуалы: `consumables_written_off` — факт списания расходников из инвентаря виден в Mini App (миграция `f1a2b3c4d5e6`) ([#8]).
- 🔮 Работы: `reminder` / `reminder_time` в `/api/arcana/works` (паритет с задачами Nexus), «🔵 запланирован» бейдж, «✅ выполнено» хвост из закрытых раскладов/ритуалов ([#10], [#203]).
- Связь Работа↔Расклад/Ритуал видна в Mini App (`from_work` reverse-link) ([#10]).
- `core/ru_morph.py` — падежный стеммер (вынесен из `core/memory`), общий с матчером имён долгов ([#136]).

### Changed
- 🚀 Деплой: `scripts/deploy.sh` лежит в репо, перед `alembic upgrade head` проверяемый `pg_dump` в `~/backups` (последние 7), при сбое деплой останавливается; ставится на VPS вручную; кейс 0014.
- 🧰 CI: ESLint (`no-undef`, `react/jsx-no-undef`) и job `frontend` (`npm run lint` + build) в `.github/workflows/test.yml`.
- 💰 Цели вынесены из `memories` в таблицу `goals` (миграция `a1b2c3d4e5f6`, backfill из `цель_*` фактов) — как долги в `debts`; `цель_` диверсится на записи ([#205]).
- 📅 Календарь Nexus: page-title, ровный ритм блоков, единый цветовой словарь сетки месяца, читаемая карточка «сегодня» в Неделе (плотный фон вместо стекла) ([#50]).
- 🔥 Карточка стрика в «Мой день» — fluid: тянется как соседние плитки, солнечный сигил фикс. размера и всегда внутри ([#207]).
- `docs/ARCHITECTURE.md`: таблица роутинга моделей, инвентарь SQLite-состояния вне Postgres, секции паттернов и анти-паттернов; README — mermaid-диаграмма + quick-start ([#17]).
- Owner-key колонка `user_notion_id` → `user_id` во всех 14 таблицах ([#144]); PK `core_identity.notion_id` имя сохраняет ([ADR-0024]).
- Два Telegram-аккаунта владельца сведены в один `user_id` — owner-scoped чтение памяти больше не фрагментируется по устройствам (миграция `e067a1b2c3d4`) ([#202]).
- `GET /api/memory`: любая строка категории «💰 Лимит» уходит в сгруппированный вид, не протекает карточкой во flat-список ([#6]).

### Fixed
- 🛠️ Nexus: правка последней задачи — reply на карточку всегда правит её задачу, окно `last_task` 10 минут (продлевается успешной правкой) с потолком 30 минут; даты из текста разбираются кодом (`parse_when`) и перекрывают ответ модели при создании и правке; вопрос о задачах — интент `question`; кейс 0041.
- 🛠️ Хранение: 15 SQLite-баз живут в `data/` на volume (`core/data_paths.py`), перенос из корня при старте без перезаписи, лог `persistent db …` и страж-тест против `.db` в корне; контейнер пересоздаётся на каждом деплое; кейс 0040.
- 🛠️ Mini App: `ErrorBoundary` на корне, в шитах и формах правки (ошибка формы больше не белый экран); ошибка `forSitter` в форме правки задачи; `fmtDate` вынесена на уровень модуля (карточка ритуала).
- 🛠️ Mini App: дедлайн «на весь день» не показывает `03:00` (`extract_time(..., all_day)` во всех местах чтения), правка только даты оставляет дедлайн all-day, время подставляется только при явном `deadline_time`.
- Напоминания Nexus: пропущенное одноразовое уведо больше не дублируется «⏰ Пропущено» на каждом рестарте — `reminder` обнуляется после отправки (`clear_reminder`) ([#206]).
- Гримуар: декартово произведение в `_list_by_category_sync` (SAWarning в тестах) ([#9]).
- Работы: архивные (soft-deleted) работы протекали в `/works` — унифицировано на `_TERMINAL_STATUS = ("done", "archived")` ([#95]).
- Долги: матч имени теперь падеже-независимый («долг Ивану» ↔ «вернула Ивана»), без фаззи-матча опечаток ([#136]).
- Клиенты: ДР из скриншота не перезаписывается молча — при расхождении спрашивает `[✅ Обновить] / [↩️ Оставить]` ([#102]); заметки клиента мёржатся на свежем pending-состоянии (гонка) ([#101]).
- `source="auto"` пишется только `core/location.py`; `save_memory`/`save_parsed` всегда `"manual"` ([#148]).

### Removed
- Мёртвые `notion_id`-колонки (5 таблиц, кроме `core_identity.notion_id`) + backfill-скрипты под них ([#149]).
- `memories.value_text` (никогда не заполнялась) ([#146]).
- `core/memory._save_debt_from_memory` (0 вызовов после `parse_and_store`) ([#6]); frontend `_SessionDetailLegacy` (мёртвый), финансовая строка перенесена в живой `SessionPagerOverview` ([#204]).

### В работе
- Ручное тестирование волны Arcana v8 (фото клиентов / объектов / ритуалов, ДР, парсинг скриншотов TG-профилей, бартер-флоу).
- Whisper для голосовых (ждёт пополнения OpenAI credits).
- Лендинг-букинг после покупки `kailark.com`.
- RPi3 деплой; долгосрочно — миграция в свою инфру (VPS, PostgreSQL).

---

### Known issues
- `tests/test_care_period_auto.py::test_get_is_not_allowed_on_the_auto_route` падает (404 вместо 401/405), не разобрано.

## [Lists v1.2.5 / Memory v1.2.4] — 2026-05-08

### Added
- 💻 Техника как категория списков (Haiku few-shot).
- Alias resolver памяти — канонизация связи через regex-маркеры (issue #2).

### Fixed
- Recurring tasks: остаются в `In progress` с completion timestamp; Mini App прячет today-completed повторяющиеся из расписания (issue #1).
- `core/memory`: алиасы сводят синонимы к существующим записям.
- `finance-cats`: убраны 🤖 Боты из classifier prompt (PR #20).

### Docs
- README обновлён: текущий статус, lists v1.2, Mini App, GitHub Issues.
- `BACKLOG.md` архивирован после миграции в Issues.
- `NOTION_DATABASES_v4.md` пересобран из live Notion API.
- Категории констант синхронизированы со схемой Notion (🔄 Бартер, 🎲 Импульсивные, 🔮 Практика, 🕯️ Расходники).

---

## [Lists v1.2] — 2026-05-06

### Added
- Планируемые цены, магазины-источники, группы, этапы, агрегатные суммы для 🗒️ Списков.
- Mini App checkout: чек на покупку → авто-расход в 💰 Финансах.
- Список покупок: regex `добавь в [группа]:` + Haiku few-shot для grouped lists с ценами.

### Changed
- Бюджетный intercept не срабатывает на list/task/note команды; кнопка закрытия + 15-мин TTL для `has_plan`.

### Fixed
- `core/layout`: smart guard для mixed-script и брендовых имён + structured logging.

---

## [Arcana v8.0] — 2026-05-03

### Added
- Касса P&L (`core/cash_register.py`): income / expense / salary / cash / barter; self-client исключён.
- Выплата себе через категорию 💰 Зарплата (Бот=Nexus).
- Бартер-чеклисты с reply-парсингом («отдала / вместо / закинула 1500₽»).
- Фото клиентов / ритуалов / объектов клиента (с заметками); парсинг скриншотов TG-профилей.
- День рождения клиентов.
- Self-client THE ONE дизайн (холо-фольга + живой глаз + сигил + Architect Badge).
- Инвентарь в Mini App (segment-toggle на вкладке Ритуалы + локальный FAB).
- `ritual_writeoff` — списание расходников после ритуала (Haiku-парсер + inline kb).
- Полный паритет памяти с Nexus (router intents + auto-suggest на 3+ повторений + `get_memories_for_context` в rituals).
- Preview-flow для работ (паритет с Nexus tasks).
- Intent split planned/done с auto-relation Работа↔Ритуал/Расклад; deadlines + reminders.

### Changed
- `core/cloudinary_client.py`: единый upload helper, разные folder'ы (sessions / rituals / clients / client-objects).
- Mini App Arcana: спинер с halo pulse, FAB на всех табах, объединённая касса.
- E2E тесты: cash / payself / barter / inventory.

### Fixed
- `analyze_image`: исправлен баг.
- Триплеты: edit/remove кнопки в multi-flow, session merge by name.

---

## [Wave 8.x — Mini App glass redesign] — 2026-04-24..2026-05-01

### Added
- Дизайн Nexus×Arcana: Newsreader, CSS glass, hero-карточки.
- Анимированный фон по погоде (дождь/снег/туман/облака/ясно).
- Стеклянные подложки у метрик в «Мой день».
- Календарь: задачи на дедлайне и напоминании, RU-холидеи, weekend tint, recurring expand из «Повтор» select.
- Drill-down финансов схлопывает синонимы из памяти; split-расход; долг-таб.
- СДВГ-профиль группами как в боте; «Мой день» всегда показывает Расписание.
- Категории списков/памяти из схемы Notion целиком.
- При создании чеклиста — родительская задача + relation.

### Fixed
- Чекбокс задачи пишет `Status=Done` в Notion; done/delete для чеклистов без relation на юзера.
- Контент bottom-sheet больше не уезжает под таб-бар.
- Дни в «Неделя» кликаются и меняют выбранный.
- Стекло не вытекает за карточки; чистый фон Arcana.

---

## [Nexus v9.x] — 2026-04..2026-05

### Added
- Per-task streaks для повторяющихся задач (sqlite + Mini App sheet).
- Recurring tasks через `every_Nd` interval trigger; auto-cancel напоминания при done.
- СДВГ-tip на главном Mini App (Haiku).
- Closed task read-only sheet + restore button; archive исключён из active.
- Календарь Mini App: full RU production calendar (xmlcalendar.ru — переходы, короткие дни, рабочие выходные).

### Changed
- Cloudinary: поддержка `CLOUDINARY_URL` и отдельных `CLOUD_NAME`/`API_KEY`/`API_SECRET`.
- Tabbar inactive icons: темнее (`sky.text`).

### Fixed
- Double 🔄 в schedule rows (адаптер уже добавляет префикс).
- Recurring task done — корректное снятие job из scheduler.

---

## [Бюджетная аналитика и финансы] — 2026-03..2026-04

### Added
- `/budget` бюджетная аналитика на Sonnet (`core/budget.py`).
- Payday review с дедупом через отдельную SQLite-таблицу (TTL 25h).
- Лимиты с дедупом по display name.
- Категория-коррекция: `измени категорию` после finance → обновляет финансы, не задачу.
- Перехват «это доход/расход» в Python до Claude API.

### Fixed
- Сохранение `last_payday_reminder` при reset бюджет-стейта.
- Без savings когда лимиты сильно урезаны.
- Deactivate removed fixed expenses в Notion при сохранении бюджета.

---

## [Arcana CRM фундамент] — 2026-03

### Added
- Полный flow создания клиента (multi-message: текст / фото / контакт / голос).
- `find_or_create_client` ищет в БД, предлагает обновить если найден.
- Аккумуляция инфы клиента из нескольких фото, merge с текстом при создании.
- Photo without caption → asks what it is (вместо auto-tarot).
- Интеграция Nexus ↔ Arcana через `/help` и `/fixstreak`.

### Fixed
- Frozen `Message` — текст через `_text` параметр, без `msg.text` assignments.
- Перехват любого `pending_client` стейта для фото/текста, не только `awaiting_info`.
- Restore streak из Notion-истории.

---

## [Initial wave] — 2026-03-18

### Added
- Двойная архитектура Telegram-ботов: ☀️ Nexus + 🌒 Arcana.
- Notion схема: 12 баз AI_Agents (Nexus tasks, Arcana clients/sessions/rituals/works, общие finance/memory/lists/users/errors).
- Claude API роутинг: Haiku для рутины, Sonnet для трактовок / бюджета / СДВГ-советов / Vision / Arcana session summary.
- `core/preprocess.py`: layout EN→RU + Haiku spell-correction с whitelist (78 RU-карт Таро + ~30 эзо-терминов + имена клиентов).
- `core/reply_update.py`: reply на сообщение = дополнение записи (TTL 30 дней).
- Reminder scheduler через apscheduler.
- Pending state в SQLite (`pending_tarot.db`, `pending_works.db`, `pending_lists.db`, `pending_clients.db`).
- Memory: save / search / deactivate / delete / `get_memories_for_context` / `auto_suggest_memory`.
- Vision: фото чеков (Sonnet).
- Mini App MVP: FastAPI backend + React+Vite frontend.

[Unreleased]: https://github.com/dontkaiad/nexus-arcana/compare/main...HEAD
[#6]: https://github.com/dontkaiad/nexus-arcana/issues/6
[#7]: https://github.com/dontkaiad/nexus-arcana/issues/7
[#8]: https://github.com/dontkaiad/nexus-arcana/issues/8
[#9]: https://github.com/dontkaiad/nexus-arcana/issues/9
[#10]: https://github.com/dontkaiad/nexus-arcana/issues/10
[#95]: https://github.com/dontkaiad/nexus-arcana/issues/95
[#101]: https://github.com/dontkaiad/nexus-arcana/issues/101
[#102]: https://github.com/dontkaiad/nexus-arcana/issues/102
[#136]: https://github.com/dontkaiad/nexus-arcana/issues/136
[#144]: https://github.com/dontkaiad/nexus-arcana/issues/144
[#146]: https://github.com/dontkaiad/nexus-arcana/issues/146
[#148]: https://github.com/dontkaiad/nexus-arcana/issues/148
[#149]: https://github.com/dontkaiad/nexus-arcana/issues/149
[#202]: https://github.com/dontkaiad/nexus-arcana/issues/202
[#203]: https://github.com/dontkaiad/nexus-arcana/issues/203
[#204]: https://github.com/dontkaiad/nexus-arcana/issues/204
[#45]: https://github.com/dontkaiad/nexus-arcana/issues/45
[#50]: https://github.com/dontkaiad/nexus-arcana/issues/50
[#60]: https://github.com/dontkaiad/nexus-arcana/issues/60
[#84]: https://github.com/dontkaiad/nexus-arcana/issues/84
[#85]: https://github.com/dontkaiad/nexus-arcana/issues/85
[#154]: https://github.com/dontkaiad/nexus-arcana/issues/154
[#205]: https://github.com/dontkaiad/nexus-arcana/issues/205
[#206]: https://github.com/dontkaiad/nexus-arcana/issues/206
[#207]: https://github.com/dontkaiad/nexus-arcana/issues/207
[#17]: https://github.com/dontkaiad/nexus-arcana/issues/17
[ADR-0024]: CASES/0024-identity-pk-keeps-notion-id-name.md
