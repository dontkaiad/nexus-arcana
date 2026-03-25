# Nexus Arcana — CLAUDE.md

## Состояние репо

Репозиторий переименован: `dontkaiad/AI_AGENTS` → `dontkaiad/nexus-arcana`

### Если remote не работает (503/502):

```bash
# Проверить текущий remote
git remote -v

# Если указывает на AI_AGENTS — обновить:
git remote set-url origin https://github.com/dontkaiad/nexus-arcana.git

# Если через прокси и прокси не авторизован для nexus-arcana:
# Попросить Кай запушить локально с Mac:
# cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS && git push origin main
```

## Файлы в .gitignore (НЕ коммитить)

- `pending_tasks.db`, `pending_budget.db`, `jobs.sqlite`, `.DS_Store`
- `UPDATED_ROADMAP.md`, `SYSTEM_MAP.md`, `NOTION_DATABASES.md`, `NEXUS_CAPABILITIES.md`
- `fix_users.py`

## MCP GitHub tools

Ограничены к `dontkaiad/nexus-arcana` (было AI_AGENTS). Если MCP tools не работают — попросить обновить конфиг.

## Git workflow

- Коммитить сразу в `main`, без веток и PR.
- Hook может ложно срабатывать если локальный remote указывает на несуществующий AI_AGENTS — игнорировать.
