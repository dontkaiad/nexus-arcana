# MEMORY — модель данных Памяти

> Источник истины — код, не Notion-спеки. Каждое утверждение проверяемо по
> файлам из раздела «Свериться с кодом» в конце. Где код расходится с
> ADR-0005 — задокументирован КОД, расхождение помечено явно.

## Назначение

Память — долгосрочное хранилище фактов про пользователя и его окружение,
общее для обоих ботов (Nexus + Arcana). Хранит короткие текстовые
утверждения с категорией, тегом-ключом и связью с человеком/объектом.

Что хранит (категории, `core/memory.py:CATEGORIES`, 15 шт.):
`🦋 СДВГ`, `👥 Люди`, `🏥 Здоровье`, `🛒 Предпочтения`, `💼 Работа`,
`🏠 Быт`, `🔄 Паттерн`, `💡 Инсайт`, `🔮 Практика`, `🐾 Коты`,
`💰 Лимит`, `🔒 Обязательные`, `📥 Доход`, `📋 Долги`, `🎯 Цели`.

Граница «память про юзера» vs «доменное знание»:
- Память — про пользователя и связанных людей/объектов (предпочтения,
  паттерны, СДВГ-адаптации, заметки про людей и котов).
- Доменное знание (гримуар Арканы, карты Таро, и т.п.) — НЕ память, живёт
  в своих доменных таблицах. В коде памяти доменных сущностей нет.
- Бюджетная конфигурация (лимиты/доход/обязательные/цели/долги) физически
  лежит в той же таблице `memories` под категорией `💰 Лимит` и
  ключами с префиксами `лимит_`/`обязательно_`/`цель_`/`долг_`/`income_`,
  читается отдельным путём (`core/budget.py`). ADR-0005 помечает это как
  «parked follow-up» — кандидат на вынос в finance-модуль; в коде пока НЕ
  вынесено.

## Схема (реально, из миграции)

Одна таблица `memories` (PostgreSQL). Миграция Alembic
`alembic/versions/j0c1d2e3f4g5_core_memories_pg.py`, revision `j0c1d2e3f4g5`,
down_revision `i9d0e1f2g3h4`. Зеркало в SQLAlchemy Core —
`core/repos/memories_table.py` (совпадает колонка-в-колонку).

| Колонка | Тип | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `notion_id` | Text | UNIQUE (nullable) |
| `fact_text` | Text | NOT NULL |
| `key_name` | Text | NOT NULL, default `''` |
| `value_text` | Text | NOT NULL, default `''` |
| `category` | Text | NOT NULL, default `''` |
| `scope` | Text | NOT NULL, default `'global'` |
| `source` | Text | NOT NULL, default `'manual'` |
| `related_to` | Text | NOT NULL, default `''` |
| `is_current` | Boolean | NOT NULL, default `true` |
| `is_archived` | Boolean | NOT NULL, default `false` |
| `user_notion_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | default `now()` |
| `updated_at` | TIMESTAMP(tz) | default `now()` |

Индексы (из миграции):
`ix_memories_key_name` (key_name), `ix_memories_category` (category),
`ix_memories_scope` (scope), `ix_memories_is_current` (is_current),
`ix_memories_user` (user_notion_id).

Доменный объект `Memory` (`core/repos/pg_memory_repo.py`,
`@dataclass`) маппит строку: `id` (str), `fact`←fact_text, `key`←key_name,
`value`←value_text, `category`, `scope`, `source`, `related_to`←related_to,
`is_current`, `is_archived`, `user_notion_id`, `date`←created_at[:10],
`updated_at`←ISO.

Значения полей по факту использования в коде:
- `scope` ∈ {`global`, `nexus`, `arcana`}. Маппинг bot_label→scope:
  `☀️ Nexus`→`nexus`, `🌒 Arcana`→`arcana`, иначе `global`
  (`pg_memory_repo.bot_to_scope`).
- `source` ∈ {`manual`, `auto`}. На практике все записи через `save_memory`
  пишутся с `"manual"` (см. ниже «Известные ограничения»).
- `notion_id` в обычной записи = `None`; параметр `notion_id` у `add`
  задействован только бэкфиллом `scripts/backfill_memories.py` (маппинг на
  старые Notion-записи).

## Как работает

### Слои
`handlers → core/memory.py → core/repos/memory_repo.py (_repo) →
core/repos/pg_memory_repo.py → memories_table (PG)`.
`memory_repo.py` — тонкий seam над `PgMemoryRepo`; синглтон `_repo`.
Все sync-SQL обёрнуты в `asyncio.to_thread`.

### Запись
`core/memory.py:save_memory(message, text, user_notion_id, bot_label)`:
1. `maybe_convert` (раскладка EN→RU).
2. `_parse_fact` — Haiku (`claude-haiku-4-5-20251001`, temperature=0,
   max_tokens=200) → `(fact, category, связь, ключ)`. Невалидная
   категория → `💡 Инсайт`; полный фейл парсинга → fallback
   `(текст, "💡 Инсайт", "", "факт")`.
3. `scope = bot_to_scope(bot_label)`.
4. Для не-лимитных фактов со `связь` — `_resolve_alias`: канонизация имени
   через уже сохранённые записи (regex-паттерны кличек/алиасов, глубина ≤3,
   защита от циклов).
5. Запись:
   - `category == "💰 Лимит"` и есть `ключ` → `_repo.upsert` (найти по
     `key_name`+`category` среди не-архивных, обновить; иначе создать).
     Возвращает `(id, was_updated)`.
   - иначе → `_repo.add` (всегда INSERT новой строки).
6. Side-effect: при категории `🦋 СДВГ` и новой записи — `_get_adhd_tip`
   (Sonnet, `config.model_sonnet`, temperature=0.7) шлёт совет.

`value_text` при записи НЕ задаётся (ни `add`, ни `upsert` его не пишут) —
всегда остаётся `''`. См. «Известные ограничения».

### Чтение
Два режима:

1. Точный ключ — `find_by_exact_key(key, user_notion_id, page_size)`:
   `key_name == key` (строгое равенство), `is_current=True`,
   `is_archived=False`, сортировка по `updated_at desc`. Реальные вызовы:
   `tz_{tg_id}` (таймзона — `core/shared_handlers.py`,
   `nexus/handlers/tasks.py`, `miniapp/.../weather.py`),
   `budget_payday` (`nexus/handlers/finance.py`).
2. Подстрочный поиск — `search(terms, scope, user_notion_id, page_size)`:
   `OR` из `ILIKE %term%` по `fact_text`, `key_name`, `related_to`;
   фильтр активности (`is_current=True`, `is_archived=False`); опционально
   `scope` (совпадение ИЛИ `global`) и `user_notion_id`; сорт
   `created_at desc`. Это НЕ семантика — только substring/contains.

Производные чтения:
- `find_by_category(category, is_current, scope, user_notion_id, page_size)`
  — точный матч категории (пустая `category` = без фильтра по категории).
- `find_by_key_prefixes(prefixes, user_notion_id)` — `key_name ILIKE p%`;
  используется бюджетом (`core/budget.py`, префиксы `income_`,
  `обязательно_`, `лимит_`, `цель_`).
- `find_recent(is_current, scope, user_notion_id, page_size)` — последние
  не-архивные.

`core/memory.py:_find_pages_by_hint` поверх `search`: шорткат по имени
категории (`сдвг`/`люди`/…→категория, через `find_by_category`), иначе
токенизация hint (стоп-слова + наивный стемминг `_normalize_word`) → `search`.

### Жизненный цикл записи (soft-delete, два флага)
- `is_current` — «актуальность». `deactivate_memory` → `set_active(ids, False)`
  (`_pg.set_current`), `is_current=False`. Запись остаётся в выдаче поиска,
  но помечается «(неактуально)»; можно восстановить (reactivate).
- `is_archived` — «удаление». `delete_memory` → `archive(id)`,
  `is_archived=True`. Архивные исключены из всех чтений
  (`_base_active_q` фильтрует `is_archived == False`). Хард-delete строки в
  коде нет.

### Кто вызывает
- Боты, хендлеры памяти: `nexus/handlers/memory.py`,
  `arcana/handlers/memory.py` — save / search / deactivate / delete /
  auto_suggest (inline да/нет).
- Контекст для промптов: `get_memories_for_context(user_notion_id,
  keywords, bot_label, max_results)` — фильтрует по scope (оставляет
  совпадение scope ИЛИ `global`), отдаёт текстовый блок «Контекст из
  памяти:». Вызывают `arcana/handlers/sessions.py`, `clients.py`,
  `rituals.py`.
- Авто-сохранение: `core/classifier.py` (kind `timezone_update` →
  `save_memory(..., "☀️ Nexus")`).
- Бюджет: `core/budget.py` через `find_by_key_prefixes`.
- Recall по слову: `recall_from_memory(keyword)` (finance/tasks Nexus).
- Mini App (PG-native, `PgMemoryRepo` напрямую):
  `miniapp/backend/routes/memory.py` — `GET /api/memory` (исключает
  бюджетные/ADHD категории) и `GET /api/memory/adhd` (группировка
  patterns/strategies/triggers/specifics + Sonnet-профиль);
  `miniapp/.../weather.py` (таймзона через `find_by_exact_key`).

### Роутинг моделей (из кода, не из памяти)
- Haiku `claude-haiku-4-5-20251001` — `_parse_fact` (разбор факта при save).
- Sonnet `claude-sonnet-4-6` (`config.model_sonnet`) —
  `core/memory.py:_get_adhd_tip` (совет при сохранении СДВГ-факта) и
  `miniapp/backend/routes/memory.py:_generate_adhd_profile` (СДВГ-профиль).
- Чтение/поиск/деактивация/архивация — без LLM (чистый SQL).

## Ключевые решения и trade-offs (ADR-0005)

1. **Хранилище: PG, не Notion.** Память переехала в PG (миграция
   `j0c1d2e3f4g5`). Плата: нужен живой PG-engine (берётся из
   `arcana.repos.pg_sessions_repo.get_engine`), теряется «человекочитаемость»
   Notion-таблицы.
   - Расхождение: `nexus/handlers/finance.py:_save_memory_entry` ВСЁ ЕЩЁ
     пишет бюджетную память в Notion (`NOTION_DB_MEMORY`, select-поля
     `Бот`/`Категория`/`Актуально`). Это параллельный путь записи мимо PG —
     не соответствует «storage = PG». См. техдолг.

2. **`scope` вместо поля `Бот`.** Один столбец `scope`
   (`global`/`nexus`/`arcana`) заменил Notion-select `Бот`. Почему:
   большинство фактов общие (`global`), а редкий бот-специфичный факт не
   требует разносить память по доменам/таблицам. Плата: фильтрация scope —
   прикладная логика в каждом чтении (`scope == X OR scope == global`), а не
   жёсткое разделение.

3. **Soft-delete вместо удаления.** Два флага `is_current` (актуальность,
   обратимо) и `is_archived` (скрытие из выдачи). Почему: история не
   теряется, «неактуальное» можно вернуть. Плата: строки копятся, каждое
   чтение тащит фильтр активности; реального освобождения места нет.

4. **facts/observations split — НЕ реализован (расхождение с ADR-0005).**
   ADR-0005 (Decision) предписывает ДВЕ таблицы: `facts` (точный
   key→value) и `observations` (свободный текст + категория + семантика).
   Реально создана ОДНА таблица `memories` с обоими наборами полей
   (`key_name`/`value_text` И `fact_text`/`category`) — это ровно тот
   «unified memory table», который ADR в разделе Alternatives ПОМЕТИЛ как
   rejected. Таблиц `facts`/`observations` в коде/миграциях НЕТ.
   Trade-off по факту: проще (одна таблица, один репозиторий), но смешаны
   два паттерна доступа (точный ключ vs contains-поиск) в одном месте —
   ровно тот минус, от которого ADR хотел уйти.

## Известные ограничения / техдолг

- **`value_text` — мёртвая колонка.** Есть в схеме и читается в
  `Memory.value`, но НИ ОДИН путь записи (`_add_sync`, `_upsert_sync`) его
  не заполняет — всегда `''`. Задуманная пара «точный key→value» (ADR
  `facts`) фактически вырождена: и ключ, и значение, и текст живут в
  `key_name`/`fact_text`.
- **Нет embedding/семантики.** ADR-0001/0005 называют `observations`
  источником для RAG, но в коде поиск только `ILIKE`-substring. Колонок
  под эмбеддинги, pgvector, индексов сходства — нет. Семантический слой
  НЕ реализован.
- **Параллельная запись в Notion.** `_save_memory_entry`
  (`nexus/handlers/finance.py`) пишет бюджетные записи в Notion-базу
  `NOTION_DB_MEMORY` мимо PG-репозитория. Источник истины для бюджета
  раздваивается (PG `memories` ⟷ Notion). Кандидат на унификацию.
- **`source` фактически всегда `manual`.** Значение `auto` предусмотрено
  схемой/дефолтом, но `save_memory`/`save_parsed` хардкодят `"manual"`;
  отдельного потока записи с `source="auto"` в коде не найдено
  (проверить, если появится авто-экстракция).
- **`_resolve_alias` зависит от качества записей.** Канонизация имён —
  regex по тексту существующих фактов; при «грязных» формулировках алиас
  не разрезолвится (тихий fallback на исходную связь).
- **Бюджетные категории внутри памяти.** `💰 Лимит`/`📥 Доход`/
  `🔒 Обязательные`/`📋 Долги`/`🎯 Цели` живут в `memories`, но
  концептуально это finance-конфиг (ADR-0005 «parked follow-up»). Вынос в
  finance-модуль не сделан.

---

Свериться с кодом:
- `alembic/versions/j0c1d2e3f4g5_core_memories_pg.py` — миграция таблицы
- `core/repos/memories_table.py` — SQLAlchemy Core определение `memories`
- `core/repos/pg_memory_repo.py` — `Memory` dataclass + sync SQL + async API
- `core/repos/memory_repo.py` — seam-репозиторий, синглтон `_repo`
- `core/memory.py` — save/search/deactivate/delete/recall/контекст,
  `_parse_fact` (Haiku), `_get_adhd_tip` (Sonnet), `CATEGORIES`
- `core/budget.py` — чтение бюджета через `find_by_key_prefixes`
- `core/classifier.py` — авто-save (timezone_update)
- `core/shared_handlers.py`, `nexus/handlers/tasks.py` — `find_by_exact_key("tz_…")`
- `nexus/handlers/finance.py` — `find_by_exact_key("budget_payday")`,
  `_save_memory_entry` (запись в Notion `NOTION_DB_MEMORY`)
- `nexus/handlers/memory.py`, `arcana/handlers/memory.py` — хендлеры памяти
- `arcana/handlers/sessions.py`, `clients.py`, `rituals.py` —
  `get_memories_for_context`
- `miniapp/backend/routes/memory.py` — `GET /api/memory`, `/api/memory/adhd`
- `miniapp/backend/routes/weather.py` — таймзона через `find_by_exact_key`
- `core/config.py` — `MODEL_HAIKU`, `MODEL_SONNET` (`claude-sonnet-4-6`)
- `docs/CASES/0005-memory-store.md` — ADR (код расходится: см. раздел выше)
