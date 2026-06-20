# Changelog

Все заметные изменения проекта `nexus-arcana`.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует semver-подобной нумерации волн (Nexus vN.x, Arcana vN.x,
Lists vN.x, Memory vN.x).

## [Unreleased]

### В работе
- Mini App обследование 5 вкладок (issues [#6–#10](https://github.com/dontkaiad/nexus-arcana/issues?q=is%3Aissue+label%3Amini-app)).
- Ручное тестирование волны Arcana v8 (фото клиентов / объектов / ритуалов, ДР, парсинг скриншотов TG-профилей, бартер-флоу).
- Whisper для голосовых (ждёт пополнения OpenAI credits).
- Лендинг-букинг после покупки `kailark.com`.
- RPi3 деплой; долгосрочно — миграция в свою инфру (VPS, PostgreSQL).

---

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
