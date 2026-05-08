# CONTRIBUTING

Короткий гайд по работе с репо `nexus-arcana`. Подробный контекст и правила — в [CLAUDE.md](CLAUDE.md).

## Workflow

Проект ведётся в AI-augmented режиме: Кай (архитектор / PM) проектирует систему и пишет спеки, Claude Code исполняет правки под её ревью.

1. **Идея / баг** → [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues) с подходящим label (`bug`, `feature`, `mini-app`, `tech-debt`, `priority:*`, `wave`). Acceptance criteria формулирует Кай.
2. **Реализация** → Claude Code (вкладка Code) или Dispatch worktree исполняет правки по описанию issue. Архитектурные развилки — обратно к Кай через Claude.ai до правки кода.
3. **Коммит / PR** → коммит сразу в `main` (default) либо ветка + PR с `fixes #N`. Ревью на стороне Кай.
4. **CI / локальная проверка** → перед коммитом прогнать тесты и build (см. ниже).

## Стиль коммитов

`type: subject` (на русском, в нижнем регистре после префикса).

Префиксы:
- `feat:` — новая фича.
- `fix:` — баг-фикс.
- `docs:` — документация (README, CHANGELOG, docs/).
- `chore:` — рутина (deps, gitignore, конфиги).
- `refactor:` — реорганизация без смены поведения.
- `test:` — только тесты.
- `style:` — форматирование / UI-стили без логики.
- Опц. scope: `core/<file>:`, `arcana:`, `nexus:`, `miniapp:`.

В commit message **никогда** не упоминать Claude/Anthropic в авторстве и никогда не светить личные данные Кай (см. CLAUDE.md).

## Что трогать нельзя без обсуждения

- `CLAUDE.md` — правится только по явному запросу Кай.
- `.env` / `.env.*` — никогда не коммитить; добавлять новые ключи через `_env` шаблон.
- `docs/*_SPEC_v*.md`, `docs/NOTION_DATABASES_v*.md` — спеки правда о схеме; менять синхронно с кодом и через отдельный PR.
- `scripts/migrate_*.py --apply` — запускать только по явному «go apply» от Кай.
- Параллельная реализация уже существующего паттерна (см. CLAUDE.md → «Nexus и Arcana — СЁСТРЫ»). Сначала ищи аналог в `core/` или соседнем боте.

## Тесты

Перед коммитом:

```bash
cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS && python3 -m pytest tests/ -v
cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS/miniapp/frontend && npm run build
```

Все pytest должны быть зелёные. Если что-то падает на main до твоих изменений — подсветить это явно.

## Модели Claude

- **Haiku** — рутина (роутер, парсеры, spell, ADHD-tip).
- **Sonnet** — только бюджет, СДВГ long-form, Vision, трактовки таро, summary сессий.
- **Opus** — никогда без явного разрешения.

Регрессия защищена `tests/test_models_audit.py`.
