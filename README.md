# nexus-arcana

Двойная система Telegram-ботов:
- ☀️ **Nexus** — [@nexus_kailark_bot](https://t.me/nexus_kailark_bot) — личный ассистент с СДВГ-поддержкой.
- 🌒 **Arcana** — [@arcana_kailark_bot](https://t.me/arcana_kailark_bot) — CRM эзотерической практики.

Designed and architected by Кай Lark · [@hey_lark](https://t.me/hey_lark) · [github.com/dontkaiad](https://github.com/dontkaiad)

AI-augmented engineering workflow: архитектура, спеки, UX, приоритизация и ревью — на стороне Кай (IT PM, 6 лет в AI-продуктах). Реализация — направляемое исполнение через Claude.ai (стратегия / разбор архитектурных развилок) и Claude Code (правки в репо по конкретным спекам).

## Стек

Python 3.9 · aiogram 3.x · Notion API · Claude API (Haiku + Sonnet) · OpenAI Whisper · APScheduler · SQLite · FastAPI · React + Vite

## Структура

```
nexus/      Telegram-бот Nexus (personal assistant)
arcana/     Telegram-бот Arcana (esoteric CRM)
core/       Общая логика: classifier, Notion API, memory, lists, layout, vision
miniapp/    Mini App: FastAPI backend + React frontend
tests/      pytest (779 passed, 4 skipped)
docs/       Спецификации и схемы
run.sh      Auto-pull (30s) + watchfiles launcher
```

## Статус

- **Nexus v9.x** — DONE.
- **Arcana v8.0** — DONE (касса P&L, бартер, фото клиентов, инвентарь, ritual_writeoff, память паритет с Nexus).
- **Lists v1.2.5** — планируемые цены / магазины / группы / этапы / agg. суммы / Mini App checkout / 💻 Техника / повтор-задачи с Время завершения.
- **Memory v1.2.4** — alias resolver (канонизация связей через existing memories).
- **Mini App** — production. 6 табов Nexus + 6 табов Arcana, glass-cards дизайн. Обследование 5 вкладок незавершено ([issues #6-#10](https://github.com/dontkaiad/nexus-arcana/issues?q=is%3Aissue+label%3Amini-app)).

## Ключевые фичи

- ☀️ **Tasks** — natural language, deadlines, reminders, recurring c интервалами `every_Nd`, стрики, подзадачи через 🗒️ Списки.
- 💰 **Finance** — expense/income tracking, лимиты, бюджетная аналитика (Sonnet), VPS-плановый импорт банковских выписок (long-term).
- 🗒️ **Lists v1.2** — покупки / чеклисты / инвентарь с ценами план/факт, магазинами, группами, этапами; checkout создаёт расход в Финансах автоматом.
- 🧠 **Memory** — категории, связи, alias-резолвер (канонизирует имя по existing записям), auto-suggest на 3+ повторений.
- 📸 **Vision** — фото чеков (Sonnet) + screenshots TG-профилей клиентов.
- 🌒 **Arcana** — клиенты, расклады, ритуалы, гримуар; касса P&L; бартер reply-парсинг; фото клиентов / ритуалов / объектов.
- 📱 **Mini App** — Telegram WebApp с glass-cards; календарь, задачи, финансы drill-down, чеклисты, инвентарь, расклады, ритуалы.

## Workflow

Кай ведёт архитектуру, спеки и ревью; AI-инструменты — исполнители на конкретных уровнях:

1. **Архитектура и стратегия** — Кай в Claude.ai (проект `nexus-arcana`): декомпозиция волн, разбор развилок, sanity-check решений до того как они попадут в репо.
2. **Реализация по спекам** — Claude Code (Mac) под наблюдением Кай: правки кода, тесты, миграции. Каждое изменение проходит ревью перед merge.
3. **Бэклог** — [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues) (issues-first workflow, см. [CLAUDE.md](CLAUDE.md)).
4. **Деплой / merge** — GitHub Desktop или auto-pull через `run.sh`.
5. **Локально** — `./run.sh` (auto-pull `main` каждые 30с + watchfiles горячий reload).

## Issues

Баги, фичи, волны разработки — в [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues).

Полезные фильтры:
- `gh issue list --label priority:high` — что горит.
- `gh issue list --label mini-app` — Mini App работа.
- `gh issue list --label wave` — большие многофазные волны.

## Документация

- [`docs/LISTS_SPEC_v1.2.md`](docs/LISTS_SPEC_v1.2.md) — спека списков.
- [`docs/NOTION_DATABASES_v4.md`](docs/NOTION_DATABASES_v4.md) — реальные схемы 12 баз Notion (автогенерация из API).
- `BACKLOG.md` — архив до миграции в Issues (8 мая 2026).

## Setup

```bash
cp _env .env          # заполнить токены: Telegram, Anthropic, Notion, OpenAI
pip install -r requirements.txt
./run.sh              # запускает обоих ботов + auto-pull + watchfiles
```

## Mini App локально

```bash
./run.sh                                 # backend (FastAPI на :8000)
cd miniapp/frontend && npm install
cd miniapp/frontend && npm run dev       # → http://localhost:5173
```

Через Telegram-туннель:

```bash
cd miniapp/frontend && npm run dev:tunnel    # build --watch + preview
cloudflared tunnel --url http://localhost:5173 --protocol http2
```

URL туннеля настраивается в BotFather как menu button.

## Тесты

```bash
python3 -m pytest tests/ -v
```

779 passed · 4 skipped · 0 failed (8 мая 2026).
