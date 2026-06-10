# CLAUDE.md — контекст для Claude Code

> ⚠️ Кай склонна к перфекционизм-петле: разработка как escape от использования продукта. Не предлагать большие реструктуризации (ARCHITECTURE.md, миграции БД, новые продукты, портфолио причёсывание) пока Mini App обследование не завершено. Видеть когда сессия превышает 6 часов — мягко возвращать к реальности.

## 🔄 Workflow: Issues-first

ВСЕГДА следовать этому циклу:

### Баги
1. Кай описывает баг (текстом или скрином)
2. **Первое действие Claude Code:** `gh issue create` — заводим issue с лейблами `bug` + area + priority
3. Получаем номер #N
4. Фиксим в отдельной ветке `claude/fix-N` или прямо в main для тривиальных
5. Коммит с `fixes #N` в сообщении
6. Push origin main → issue автозакрывается

### Идеи и фичи
1. Кай описывает идею (даже сырую)
2. **Первое действие:** `gh issue create` с лейблом `feature` + priority. Описание: контекст + acceptance criteria + объём в часах если очевидно
3. НЕ начинать реализацию пока Кай явно не подтвердит «делаем»
4. При реализации — тот же flow что для багов (`closes #N`)

### Исключения (issue НЕ нужен)
- Однострочные опечатки в комментариях/доках
- Cleanup промежуточных файлов
- Когда Кай явно говорит «без issue, просто фикс»

### Принцип
**1 коммит = 1 issue.** В сообщении `fixes #N` (баг) или `closes #N` (фича) —
push в main автозакрывает issue. Не смешивать несколько issue в один коммит.

## ✍️ Commit Authorship Rules

ВСЕ коммиты делаются от имени Кай как единственного автора.

ЗАПРЕЩЕНО в коммит-сообщениях:
- `Co-Authored-By: Claude` / `Co-Authored-By: Claude Code`
- `🤖 Generated with Claude Code` / `Generated with Claude`
- Любое упоминание Claude / AI / LLM как соавтора
- Эмодзи робота в качестве подписи

ОБЯЗАТЕЛЬНО:
- Author: Kai Lark (через `git config`)
- Сообщение в формате: `type(scope): subject (fixes #N)`
- Тело коммита если нужно — без упоминания инструментов

Это правило применяется ВСЕГДА:
- Локальный Claude Code на Mac
- Claude Code on the web
- Claude Code в Code tab mobile app
- Dispatch
- Любые Cloud sessions

Если инструмент пытается добавить подпись автоматически — переписать
сообщение перед push.

## 🔒 Privacy: репо публичный

НИКОГДА не писать в публичные файлы (README, CLAUDE.md, docs/, любые tracked .md, GitHub Issues, коммит-сообщения):

- Юридические процессы (банкротство, суды, заочные процессы)
- Реальные суммы (доход, расходы, долги, аренда, любые рубли)
- Реальные имена людей кроме публичного псевдонима «Кай» (включая родственников, друзей, кредиторов, клиентов Arcana и любых третьих лиц)
- Конкретные диагнозы или медицинские подробности
- Реальные tg_id, телефоны, email, адреса
- Бренды реальных покупок и магазинов
- Тексты реальных сообщений Кай или клиентов

МОЖНО упоминать публично:
- Имя «Кай», handle @hey_lark, @nexus_kailark_bot, @arcana_kailark_bot, @dontkaiad
- ADHD-friendly UX как принцип проектирования
- Архитектуру, технологии, паттерны
- Generic описания фич («учёт долгов», «бюджетные данные», «лимиты по категориям»)

ПРИВАТНОЕ хранится в:
- `CLAUDE.local.md` (gitignored)
- `.env` (gitignored)
- Notion (через API, токены в .env)
- Memory Кай в Claude.ai

При создании любого нового issue / документа / коммита — прогнать через эти правила ДО публикации.

## Статус (обновлено 11 мая 2026, после волны Arcana v8)

- **Nexus v9.0 DONE**
- **Arcana v8.0 DONE**: касса P&L, выплата себе (через категорию 💰 Зарплата
  Бот=Nexus), бартер чеклисты с reply-парсингом («отдала / вместо / закинула 1500₽»),
  фото клиентов / ритуалов / объектов клиента (с заметками), ДР клиентов,
  парсинг скриншотов TG-профилей, self-client THE ONE дизайн (холо-фольга +
  живой глаз + сигил + Architect Badge), инвентарь в Mini App (segment-toggle
  на вкладке Ритуалы + локальный FAB), `ritual_writeoff` (списание расходников
  после ритуала), память — полный паритет с Nexus (router intents +
  auto-suggest на 3+ повторений + `get_memories_for_context` в rituals).
- **Mini App**: Nexus 6 табов / Arcana 6 табов. Обследование Mini App:
  пройдено 2 прохода, 21 issue закрыто, 6 design-issues открыто.
- **Тесты**: 936 passed, 0 skipped, 0 failed. Полный sweep ~3:21.

## Стиль Кай (КРИТИЧНО)

СДВГ. Никаких стен текста. Никаких опросников когда уже всё сказано.
Краткие реплики, мат, EN/RU mix. Решения по ощущению. Архитектуру
обсуждаем ДО промпта, не сразу промпт.

## Кто пишет промпты

Кай (она/её). PM с СДВГ. Все AI-промпты внутри ботов используют 
женский род и имя Кай. Sonnet и Haiku в коде должны обращаться 
к Кай в женском роде.

Timezone хранится в Памяти по ключу `tz_{tg_id}`.

**Nexus = ОН** (мужской род в текстах). **Arcana = ОНА** (женский род).

## КАЙ ЛЕНИВАЯ — это контекст для UX

Кай НЕ ХОЧЕТ:
- набирать длинные команды в терминале
- разбираться с твоими ошибками
- вчитываться в твой код
- делать что-то "руками" в репе
- кликать длинные чек-листы тестирования

### Команды для терминала

ВСЕГДА давай Кай ПОЛНЫЕ команды одной строкой, готовые к копи-пасту. 
ВСЕГДА с абсолютным путём или с `cd` в начале:

✅ `cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS && python3 -m pytest tests/ -v`
❌ "Запусти pytest tests/test_X.py"

Команда коммита всегда такая:
```
git add -A && git commit -m "..." && git push origin main && git log --oneline -3
```

`git log --oneline -3` обязательно — Кай должна видеть что коммит 
реально ушёл. Часто бывало что ты говорил "готово" и не коммитил.

### Чек-листы

Если задача требует ручной проверки от Кай — формулируй МИНИМАЛЬНЫЙ 
набор. Не "прокликай 15 пунктов", а 2-3 ключевых сценария.

## ПРОВЕРИТЬ 250 РАЗ ЧТО УЖЕ СДЕЛАНО — НЕ ЛОМАТЬ СТАРОЕ

ОБЯЗАТЕЛЬНЫЙ чек-лист перед тем как писать код:

1. **Прочитай актуальную версию файла** через view, а не по памяти.
2. **Прочитай ВСЕ связанные тесты** — `grep -rn "имя_функции\|имя_класса" tests/`
3. **Найди все вызовы** функции/класса который меняешь:
   `grep -rn "function_name\(" arcana/ nexus/ core/ miniapp/`
4. **Проверь что задача не сделана уже** — git log за последние 10 
   коммитов + текущий код.
5. Прочитай открытые [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues) для актуального бэклога.

После изменений:
- ВСЕ pytest зелёные. Не "новые зелёные, старые падали раньше".
- npm build чистый.

## КРИТИЧЕСКИЙ ПРИНЦИП: Nexus и Arcana — СЁСТРЫ

Nexus и Arcana разделяют БОЛЬШИНСТВО UX-паттернов и логических 
примитивов. Это сделано НАМЕРЕННО, годами наработки.

1. **ПЕРЕД написанием любого UX** (inline-кнопки, дедлайны, напоминания, 
   парсинг дат, pending state, reply-обработка, формат вывода 
   сообщений, конвертер раскладки, spell correction, preview-flow) — 
   найди аналог в соседнем боте.
2. **Если аналог есть → ПЕРЕИСПОЛЬЗУЙ.** Выноси общее в `core/`.
3. **Если в Аркане кажется что UX должен быть другим, чем в Nexus** 
   — НЕ ДЕЛАЙ САМ. Объясни в ответе Кай и спроси.
4. **Параллельная реализация = БАГ, не фича.**

### Примеры общих примитивов (всё уже есть в `core/` или `nexus/`)

- **дедлайны/напоминания через apscheduler** — `core/reminder_scheduler.py`
- **препроцессинг текста (раскладка + spell)** — `core/preprocess.py`:
  `normalize_text(text, *, user_notion_id)` → раскладка EN→RU
  (`core/layout.py:maybe_convert`) + Haiku spell-correction с whitelist
  guard (78 RU-карт Таро + ~30 эзо-терминов + имена клиентов из Notion).
  SQLite-кеш whitelist (TTL=1h). При создании нового клиента
  `find_or_create_client` сам дёргает `invalidate_whitelist`.
- **парсеры дат из текста** — Haiku-промпты в Nexus tasks
- **reply-обработка для дополнения** — `core/reply_update.py` +
  `core/message_pages.py` (page_id mapping в SQLite, TTL 30 дней)
- **inline-кнопки** — `core/utils.py:cancel_button/secondary_button` (Bot API 9.4)
- **pending state в SQLite** — `pending_tarot.db`, `pending_works.db`,
  `pending_lists.db`, `pending_clients.db`
- **message_collector** — `core/message_collector.py` (5-сек дебаунс)
- **work_relation** — `core/work_relation.py`, авто-relation
  Работа↔Ритуал/Расклад
- **payment** — `core/payment.py`, write_payment для sessions/rituals
- **preview-flow** — `arcana/handlers/work_preview.py`
  (паттерн от Nexus tasks: pending → превью → [✅ Сохранить] → запись)
- **find_or_create_client** — `core/client_resolve.py:resolve_or_create`:
  ищет клиента, при отсутствии создаёт (дефолт `🤝 Платный`) +
  отправляет «🆕 Создала клиента X» с message_pages mapping →
  reply «🌟»/«🎁» меняет тип. Используй ВЕЗДЕ где из текста
  Sonnet вытащил `client_name` (sessions, rituals, work_preview).
  **НЕЛЬЗЯ** оставлять `Тип сеанса=Клиентский` без relation
  на 👥 Клиенты (= сирота).
- **subtasks-кнопка «📋 Подзадачи»** — `core/subtasks_handler.py:make_subtasks_router()`.
  ОДИН handler для обоих ботов (factory — aiogram запрещает один
  Router в двух Dispatcher'ах). Чеклист пишется в 🗒️ Списки с
  relation на ✅ Задачи / 🔮 Работы.
- **intent ambiguity-диалоги** — `arcana/handlers/intent_resolve.py`:
  `ask_practice_or_nexus` (work без эзо-маркеров),
  `ask_clarify_or_new` (текст в pending не похож ни на дедлайн,
  ни на новое сообщение), `ask_ritual_disambiguation` (planned/done).

### Notion правила

- select-поля через `match_select`
- status через `_status()` (НЕ `_select()`)
- `data.get("key") or default` (не `data.get("key", default)`)
- emoji-канонизация для select опций (Бот=☀️ Nexus, не "Nexus")
- format_option — единый стандарт "Emoji Слово_с_заглавной"
- Поля с эмодзи в названии (`Бот`, `👥 Клиенты`, `🪪 Пользователи`) —
  С эмодзи в API
- **«Дно колоды» (🃏 Расклады) = `rich_text`**, НЕ `select`. Пишется
  через `_text(name)`. Канонические имена карт через
  `core.preprocess._tarot_card_names_ru()` (78 RU из `deck_cards.json`)
- 12 баз AI_Agents в Notion дедуплицированы (миграция выполнена
  скриптом `scripts/migrate_arcana_legacy.py`)

## ОПТИМИЗАЦИЯ ПО ДЕНЬГАМ — критично

Кай платит за каждый токен Claude API из своего кармана.

### Роутинг моделей

**Haiku (`claude-haiku-4-5-20251001`)** — для всего где не нужен
deep reasoning. Гарант — `tests/test_models_audit.py` (страж, упадёт
если Haiku пропадёт из ROUTER/parsers/spell). Используется в:
- ROUTER intent-классификация (8 few-shot, `arcana/handlers/base.py`)
- spell correction (`core/preprocess.py`)
- все JSON-парсеры (reply/delete/router/grimoire/rituals/clients/...)
- парсинг работ (`PARSE_WORK_SYSTEM`)
- ADHD-tip на главном экране Mini App (короткая фраза 15 слов)

**Sonnet (`claude-sonnet-4-x`)** — оставлен ТОЛЬКО в этих местах
(остальное — деньги Кай зря):
- `core/budget.py` — бюджетная аналитика (`/budget`)
- `core/memory.py:373` — long-form СДВГ-советы (категория 🦋 СДВГ)
- `core/vision.py` — Vision (фото чеков, требует Sonnet)
- `arcana/handlers/sessions.py` — трактовки таро (narrative + эмпатия)
- `miniapp/backend/routes/arcana_sessions.py` — саммари сессии

Регрессия защищена `tests/test_models_audit.py`.

**Opus** — никогда без явного разрешения Кай.

ПРАВИЛО: если используешь Sonnet — обоснуй почему не Haiku в комменте
к функции. Иначе тест-страж упадёт.

### Кеширование

- prompt_caching через Anthropic API (cache_control: ephemeral) для 
  системных промптов длиннее 1024 токенов.
- Не делай дубликатных запросов в Notion.

### Resilience LLM-вызовов (ОБЯЗАТЕЛЬНО)

Все вызовы Anthropic идут ТОЛЬКО через `core/claude_client.py` —
`_create_message` под декоратором `retry_transient`: до 3 попыток,
экспоненциальный backoff + jitter, для 429 уважается Retry-After,
таймаут 60с на запрос, встроенные ретраи SDK выключены (`max_retries=0`).
Ретраится только транзиентное: 429 / 5xx / timeout / connection.
Остальные 4xx — без ретрая, сразу graceful fallback (`""`/`{}`).
Whisper в `core/voice.py` — тот же паттерн.

Любой НОВЫЙ LLM-вызов (Anthropic, OpenAI, любой провайдер) обязан
идти через эту обёртку или повторять её паттерн. Прямой
`client.messages.create` вне `core/claude_client.py` = БАГ.
Регрессия защищена `tests/test_llm_retry.py`.

### Whisper — ждёт пополнения OpenAI credits

Голосовые сообщения сейчас отключены (нет credits).

### Что НЕ делать

- ❌ Sonnet для парсинга 1-2 полей из текста.
- ❌ Sonnet для intent-классификации.
- ❌ Claude API там где регекс справится.
- ❌ Длинные файлы целиком в контекст.

## 🌐 Multi-Provider AI Architecture

Этот репо использует Anthropic Claude (Haiku + Sonnet). Это by design для 
этого проекта — Nexus и Arcana содержат только compliance-чистые use cases.

Другие проекты Кай могут использовать других провайдеров под другие задачи 
в зависимости от compliance каждого провайдера.

ПРАВИЛО: при добавлении новой фичи в Nexus/Arcana проверить что use case 
не нарушает Anthropic Usage Policy.

Полная multi-provider стратегия в docs/CASES/MULTI_PROVIDER_ARCHITECTURE.md 
(будет создан в миграционной сессии).

## 🚀 Подготовка к миграции на VPS

Сейчас всё локально на Mac. Планируется миграция на VPS.

Compliance-правила:

1. На VPS можно: aiogram боты с ANTHROPIC_API_KEY, FastAPI Mini App backend,
   Postgres, Qdrant, скрипты через anthropic.messages.create() с API key.

2. На VPS НЕЛЬЗЯ:
   - Claude Code CLI под Max OAuth (ban риск — OAuth с серверных IP)
   - Sub-agents Claude Code (.claude/agents/) в headless под Max
   - Third-party harness'ы (OpenCode и т.п.)

3. Devops flow: Claude Code на Mac → SSH → VPS. Anthropic не видит SSH-трафик.

4. Подготовительные задачи:
   - Postgres schema из Notion модели (Alembic)
   - Qdrant + embeddings для RAG
   - Docker / docker-compose
   - systemd unit files
   - nginx + SSL (Let's Encrypt)
   - secrets management
   - dual-write период

Каждый шаг = кейс в docs/CASES/.

## Что это за репо

Двойная система Telegram-ботов:
- ☀️ **Nexus** (@nexus_kailark_bot, мужской род) — личный ассистент
- 🌒 **Arcana** (@arcana_kailark_bot, женский род) — CRM практики

Репо: **публичный** GitHub `dontkaiad/nexus-arcana`. 
Никогда не коммить токены, ключи, .env.

## Стек

- Python 3.9 (НЕ используй match/case, pipe-types `X | Y` → `Optional[X]`)
- aiogram 3.22
- Notion API
- Claude API: Haiku (рутина), Sonnet (трактовка/бюджет/СДВГ)
- OpenAI Whisper (голосовые, ждёт credits)
- APScheduler
- SQLite
- React + Vite (frontend) + FastAPI (backend) для Mini App
- pytest, **936 тестов зелёных** (0 skipped)

## Структура

```
/Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS/
├── arcana/handlers/
│   ├── base.py             # route_message, intent dispatch
│   ├── intent_resolve.py   # nexus_redirect, ambiguity-диалоги
│   ├── work_preview.py     # preview-flow для работ
│   ├── reply_update.py     # reply на сообщения = дополнение
│   ├── sessions.py         # расклады
│   ├── rituals.py          # ритуалы
│   ├── works.py            # 🔮 Работы
│   ├── clients.py          # клиенты CRM
│   └── payment.py          # inline-оплата
├── nexus/handlers/
│   ├── tasks.py            # ✅ Задачи, preview, scheduler
│   ├── finance.py          # 💰 Финансы
│   └── budget.py           # /budget (Sonnet)
├── core/
│   ├── notion_client.py        # find_or_create_client, _text/_select/...
│   ├── preprocess.py           # ОБЩИЙ: layout EN→RU + Haiku spell + whitelist
│   ├── client_resolve.py       # ОБЩИЙ: resolve_or_create + announce + reply
│   ├── subtasks_handler.py     # ОБЩИЙ: «📋 Подзадачи» (factory router)
│   ├── reply_update.py         # parse_reply + apply_updates
│   ├── message_pages.py        # mapping chat:msg_id → page_id (TTL 30д)
│   ├── reminder_scheduler.py   # ОБЩИЙ apscheduler-flow
│   ├── payment.py
│   ├── work_relation.py
│   ├── message_collector.py
│   ├── option_helper.py
│   ├── pagination.py
│   ├── layout.py               # maybe_convert (QWERTY→ЙЦУКЕН)
│   ├── html_sanitize.py        # sanitize_interpretation
│   └── utils.py
├── miniapp/
│   ├── backend/    # FastAPI; CORS env-driven (см. ниже)
│   └── frontend/   # React+Vite (один большой App.jsx)
├── scripts/
│   └── migrate_arcana_legacy.py # одноразовая миграция 🃏 Раскладов
├── tests/                       # 936 тестов pytest
├── run.sh                       # auto-pull 30с + watchfiles
└── CLAUDE.md
```

## Notion: схема

```
🫥 AI_Agents
├── ☀️ Nexus (✅ Задачи, 💡 Заметки)
├── 🌒 Arcana
│   ├── 👥 Клиенты — Тип клиента (select): 🌟 Self / 🤝 Платный / 🎁 Бесплатный
│   ├── 🃏 Расклады — Карты, Дно колоды, Трактовка, Сумма, Источник, 
│   │                 Оплачено, Долг (formula), Бартер · что, Сбылось, 
│   │                 relation 🔮 Работы (Сеансы)
│   ├── 🕯 Ритуалы — Силы, Структура, Цена за ритуал, Источник оплаты, 
│   │                Бартер · что, Результат, relation 🔮 Работы (Ритуалы)
│   └── 🔮 Работы — Status, Категория (🃏 Расклад / ✨ Ритуал / ...), 
│                   Дедлайн, Напоминание, Клиенты, Пользователи
└── 🔗 Общие
    ├── 💰 Финансы (Nexus + Arcana, фильтр по полю Бот)
    ├── 🧠 Память
    ├── 🗒️ Списки
    ├── ⚠️ Ошибки
    └── 🪪 Пользователи (права, tz_offset)
```

ВАЖНО: 
- Расклады/ритуалы пишут финансовые поля прямо в свою запись 
  (Сумма/Оплачено/Источник). В 💰 Финансы они НЕ пишут.

## Core модули (актуальный список после v8)

Общие (используются обоими ботами + mini app):
- `core/cash_register.py` — P&L расчёт Арканы (income / expense /
  salary / cash / barter), self-client исключён
- `core/cloudinary_client.py` — единый upload helper, разные folder'ы
  (arcana-sessions / arcana-rituals / arcana-clients / arcana-client-objects)
- `core/client_object_photos.py` — parse / serialize / append /
  edit_note / delete для поля «Фото объектов» клиента
  (формат строки `URL | заметка`)
- `core/preprocess.py` — spell-correction общий + whitelist (78 RU-карт +
  эзо-термины + клиенты)
- `core/reminder_scheduler.py` — apscheduler-flow (общий)
- `core/message_pages.py` — page_id mapping (chat:msg → page_id, TTL 30д)
- `core/reply_update.py` — reply на сообщения = дополнение
- `core/subtasks_handler.py` — общий factory router «📋 Подзадачи»
- `core/list_manager.py` — 🗒️ Списки CRUD + check_items + finance_add
- `core/client_resolve.py` — `find_or_create_client` + reply-смена типа
- `core/memory.py` — общая память для обоих ботов (save / search /
  deactivate / delete / get_memories_for_context / auto_suggest_memory)

Arcana-специфичные хендлеры (новые/допиленные в v8):
- `arcana/handlers/barter_prompt.py` — интерактивный prompt бартера
  после ритуала/расклада с Источник=🔄 Бартер + reply-парсинг
  («отдала X», «вместо X — Y», «закинула 1500₽»)
- `arcana/handlers/ritual_writeoff.py` — списание расходников из
  инвентаря после ритуала (Haiku-парсер + inline kb [✅ Списать]
  [✏️ Поправить] [❌ Не списывать] + pending state SQLite)
- `arcana/handlers/client_photo.py` — фото клиентов: аватар + фото
  объектов с заметками; `/client_photo` + reply-flow (60s окно
  без подтверждения после создания)
- `arcana/handlers/memory.py` — теперь **подключён в dispatch**
  (раньше dead code) + `maybe_auto_suggest` на 3+ повторений

## Notion поля добавлены в волне v8 (требуют ручного создания в Notion)

- **👥 Клиенты**: + «День рождения» (Date), «Фото» (URL),
  «Фото объектов» (rich_text — URL'ы построчно с `URL | заметка`)
- **🕯️ Ритуалы**: + «Фото» (URL)
- **🗒️ Списки**: + опция «🔄 Бартер» в select-поле «Категория»

## Инфра

- `*.db` (включая `.db-journal / -wal / -shm`) в `.gitignore`.
- Auto-init SQLite через `CREATE TABLE IF NOT EXISTS` в 8 модулях
  (`message_pages`, `preprocess`, `session_cache`, `list_manager`,
  `pending_clients`, `pending_tarot`, `pending_client_photo`,
  `arcana/handlers/work_preview`) — на свежем checkout бот стартует
  без существующих `.db`.
- Cloudinary credentials в `.env`: `CLOUDINARY_URL=cloudinary://<key>:<secret>@<cloud>`.
- `run.sh` — auto_pull каждые 30с + watchfiles (горячий reload).
- CORS Mini App env-driven, Cloudflare tunnel'ы через regex (см. ниже).

## Бэклог

Единственный источник: [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues).

## Mini App: CORS

`miniapp/backend/app.py` использует env-driven origin'ы. Дефолт
покрывает Telegram WebApp + локальный vite dev:

```
https://web.telegram.org, https://webk.telegram.org,
https://webz.telegram.org, https://t.me,
http://localhost:5173, http://localhost:5174
```

Эфемерные Cloudflare tunnel'ы разработки (`*.trycloudflare.com`)
разрешены через regex автоматом — менять env не нужно.

Свой prod-домен → `MINIAPP_CORS_ORIGINS=https://my.example,https://other.example`
в `.env` (CSV полностью перекрывает дефолт).

Настройки CORS защищены `tests/test_cors_config.py` — `*` запрещён.

## Реакции Telegram

Поддерживаются только: ⚡🔥👌🏆✍️💅🫡🌚❤️‍🔥🤓😈🤔🤡👀👂📸

Не используй другие — упадёт с ValidationError.

Привязка реакций (Arcana):
- ✍️ — расклад / гримуар (создание)
- 💅 — ритуал / память (save/search/deactivate) / фото клиента (success)
- 📸 — старт флоу /client_photo
- ⚡ — работа создана / 🔥 выполнена
- 👌 — расход / 🏆 доход
- 🫡 — списки / 🌚 инвентарь
- 🤓 — статистика
- 😈 — отмена / 🤔 unknown / 🤡 ошибка / 👀 обработка

## Часто встречающиеся ошибки

- ❌ `data.get("key", default)` для Notion API → возвращает None.
- ❌ `_select()` для Status-полей Notion → 400.
- ❌ Pending state в memory dict → теряется при рестарте.
- ❌ Регистрация хендлеров на router когда нужно на dp.
- ❌ Параллельная реализация существующего паттерна.
- ❌ Без `match_select()` перед записью в Notion.
- ❌ Sonnet там где справится Haiku.
- ❌ Python 3.10+ синтаксис.
- ❌ Поле `Бот` без эмодзи.
- ❌ `parse_error` в ⚠️ Ошибки на короткий пользовательский ввод — 
  нужно отправить уточнение через бота.
- ❌ Создание записи в Notion ДО подтверждения через preview-flow.
- ❌ `client_find()` без последующего `find_or_create_client()` —
  оставит «Тип сеанса=Клиентский без relation на 👥 Клиенты» сиротой.
- ❌ `_select()` для поля «Дно колоды» — оно `rich_text` (`_text(...)`).
- ❌ `allow_origins=["*"]` в CORS — есть env `MINIAPP_CORS_ORIGINS`.
- ❌ Подключать один и тот же `Router` в двух Dispatcher'ах —
  aiogram ругается. Делай factory `make_xxx_router()`.
- ❌ Запускать `scripts/migrate_*.py --apply` без явного «go apply»
  от Кай — это правит продовый Notion.

## Правила работы

### Перед изменением файла

1. Прочитай актуальную версию файла.
2. Прочитай все связанные тесты в `tests/`.
3. Если задача затрагивает Nexus И Arcana — прочитай ОБЕ стороны.
4. Если задача похожа на уже сделанное — найди и переиспользуй.

### Перед коммитом

1. `cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS && python3 -m pytest tests/ -v`
2. `cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS/miniapp/frontend && npm run build`
3. Если падают pre-existing тесты — подсвети это явно.

### Стиль ответа Кай

- Краткие конкретные ответы. Никакой воды.
- Все промпты на русском, женский род.
- В отчёте о коммите: что сделано (3-5 пунктов), номер коммита, 
  что отложено и почему.
- Если что-то не получается — ЯВНО спроси Кай.
- Команды — полные, с абсолютным путём.
- В конце отчёта — `git log --oneline -3`.

### AI-augmented engineering

Кай НИКОГДА не правит код руками. Все изменения — через Claude Code.
- Не оставляй TODO в коде "пусть Кай допишет"
- Не давай инструкций "Кай, измени строку X"
- Если что-то не получается — формулируй задачу обратно Кай как 
  вопрос со списком вариантов

## Спеки проекта Claude.ai

Канонические версии спек (живут в проекте Claude.ai; часть продублирована
в `docs/` репо). При расхождении версий источник истины — последняя версия
в проекте Claude.ai.

- `CLAUDE_PROJECT_INSTRUCTIONS_v8.md` — инструкции проекта
- `WAVE_CONTEXT_v6.md` — контекст текущей волны
- `NOTION_DATABASES_v4.md` — схема баз Notion (есть в `docs/`)
- `TASKS_SPEC_v2.md` — спека задач Nexus
- `аркана_спека_v8.md` — продуктовая спека Arcana
- `LISTS_SPEC_v1.2.md` — спека Списков (есть в `docs/`)
- остальные узкие спеки — по мере появления
