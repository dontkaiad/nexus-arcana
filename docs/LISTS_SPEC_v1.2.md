# 🗒️ Списки v1.2 — спека

## Контекст

В v1.1 БД 🗒️ Списки умела хранить только факт-цену (поле «Цена»),
заполняемую при закрытии пункта («купила X 89р»). Метаданные пунктов
(заметка, приоритет, срок годности, количество) **существовали в Notion,
но не извлекались парсером**. Цены до покупки не было совсем.

v1.2 добавляет долговременные планы покупок («Apple-стек», «Уход за
лицом»): план-цены, магазины, этапы, агрегации.

## Notion-схема

БД 🗒️ Списки (`NOTION_DB_LISTS`). Поля v1.2 выделены **жирным**.

| Поле          | Тип        | Назначение                                       |
|---------------|------------|--------------------------------------------------|
| Название      | title      | имя пункта                                        |
| Тип           | select     | 🛒 Покупки / 📋 Чеклист / 📦 Инвентарь            |
| Статус        | status     | Not started / In progress / Done / Archived     |
| Категория     | select     | 14 опций (см. `LIST_CATEGORIES`)                 |
| Группа        | rich_text  | подсписок («Apple-стек»), title задачи-родителя |
| Цена          | number     | факт-цена при покупке (v1.1)                      |
| **Цена план** | number     | планируемая цена при создании пункта             |
| **Магазин**   | rich_text  | где покупать                                      |
| **Этап**      | number     | приоритет покупки 1..5 в долгом плане            |
| Приоритет     | select     | 🔴 Срочно / 🟡 Важно / ⚪ Можно потом            |
| Заметка       | rich_text  | контекст / подробности (бренд, модель, цвет)     |
| Количество    | number     | qty (для инвентаря)                              |
| Срок годности | date       | для инвентаря                                     |
| Напомнить за  | number     | дней до срока (для cron `check_expiry`)          |
| Повторяющийся | checkbox   | для cron `clone_recurring`                        |
| Бот           | select     | ☀️ Nexus / 🌒 Arcana                              |
| 🪪 Пользователи | relation | owner                                            |
| ✅ Задачи     | relation   | для чеклиста                                      |
| 🔮 Работы     | relation   | для чеклиста (Arcana)                             |

## Парсинг (общий, в core/lists_parser.py)

`_PARSE_BUY_SYSTEM` извлекает **все 10 полей** одним вызовом Haiku.
Промпт собирается через `build_buy_system(bot_hint, memory_cats, price_hint, today_iso)`.

**Числовые форматы (price_plan):**
- `108к` / `108 тыс` / `108k` → ×1000 → 108 000
- `1.5к` → 1500, `0.5к` → 500
- `89р` / `2500₽` / `5000 руб` → ×1
- голое число `5000` в контексте цены → 5000

**Магазины:** «в iPiter» / «на WB» / «на Авито» / «у мастера на Авито» —
извлекается название без предлога.

**Этап:** «этап 1..5», «первая волна» → 1, «вторая волна» → 2.
Это порядок в долгосрочном плане, **не путать с Приоритетом**.

**Приоритет (select из 3 значений):**
| Триггер                                    | Значение         |
|--------------------------------------------|------------------|
| срочно / сейчас же / горит                 | 🔴 Срочно       |
| важно / не забыть                          | 🟡 Важно        |
| когда-нибудь / не срочно / потом / может быть | ⚪ Можно потом |

**Note:** остаточный смысловой текст после извлечения остального.
Обрезается до 100 символов с многоточием.

**Группы:** «в Apple-стек» / «в раздел X» — извлекается имя без предлога.

**Multi-line / делиметры (`,` `;` `-` `•` `\n`):**
```
добавь в Apple-стек:
- iPhone 17 Pro 108.6к в iPiter, важно
- AirPods Pro 3 25.5к в iPiter
- Apple Watch SE 30к, можно потом
```
→ 3 items, group="Apple-стек", source где явно указан, priority где явно указан.

## Команды бота (Nexus)

### `купи …` / `добавь в покупки …` (v1.1, расширено)
Создаёт пункты с полным набором полей.

Пример:
```
ты: добавь в Apple-стек:
    - iPhone 17 Pro 108.6к в iPiter
    - AirPods Pro 3 25.5к в iPiter
    - Apple Watch SE 30к
бот: 🛒 Добавлено в «Apple-стек»:
       ⬜ iPhone 17 Pro — 108 600₽ (iPiter) · 💳
       ⬜ AirPods Pro 3 — 25 500₽ (iPiter) · 💳
       ⬜ Apple Watch SE — 30 000₽ · 💳
     💰 План: 164 100₽
```

### `купила X 89р` / `чек …` (v1.1, без изменений)
Парсит закрытие пункта, пишет факт-цену в «Цена», создаёт расход в 💰 Финансы
через `check_items()` / `check_items_bulk()`.

### `сумма X` / `сколько по X` / `итого X` (v1.2 NEW)
Агрегация по группе или категории.

```
ты: сумма Apple-стек
бот: 📊 Apple-стек

     🛒 К покупке: 4 из 5
     💰 План: 175 500₽
     ✅ Куплено: 1 (8 000₽)
     📈 Осталось: 167 500₽

       ☐ iPhone 17 Pro — 108 600₽ (iPiter)
       ☐ AirPods Pro 3 — 25 500₽ (iPiter)
       ☐ Apple Watch SE — 30 000₽
       ☐ AirTag pack 4 — 12 000₽
       ✅ Anker MagGo — 8 000₽ (Озон)
```

Если аргумент совпадает с категорией (lowercased contains) — фильтруем
по «Категория», иначе — по «Группа».

## Mini App API

### GET /api/lists?type=buy|check|inv
Response (расширен в v1.2):
```json
{
  "type": "buy",
  "items": [
    {
      "id": "...",
      "name": "iPhone Pro",
      "cat": "💳",
      "done": false,
      "status": "Not started",
      "qty": 1,
      "price": null,
      "price_plan": 108600,
      "source": "iPiter",
      "stage": 2,
      "note": "Deep Blue 256GB",
      "priority": "🟡 Важно",
      "group": "Apple-стек",
      "expires": null
    }
  ],
  "summary": {
    "plan_total": 175500,
    "actual_total": 8000,
    "count_total": 5,
    "count_open": 4,
    "count_done": 1
  }
}
```

### POST /api/lists
Body (расширен):
```ts
{
  type: "buy" | "check" | "inv",
  name: string,
  cat?: string,
  qty?: number,
  note?: string,
  price?: number,           // факт-цена
  price_plan?: number,      // v1.2
  source?: string,          // v1.2
  stage?: number,           // v1.2
  group?: string,           // v1.2
  priority?: string,        // v1.2
  expires?: string,
  bot?: "nexus" | "arcana"
}
```

### POST /api/lists/{id}/done (v1.1, deprecated)
Просто помечает Done. **Не пишет в Финансы.** Оставлен для совместимости
с UI чеклистов.

### POST /api/lists/{id}/checkout (v1.2 NEW)
Body:
```ts
{ price?: number, note?: string }
```

Логика факт-цены:
1. `body.price` если передан;
2. `Цена план` из самой записи;
3. ничего → `finance_created=false`, расход не создаётся.

При `actual > 0`:
- Status → Done
- «Цена» = actual
- запись в 💰 Финансы (категория через `CATEGORY_TO_FINANCE`).

Response:
```json
{ "ok": true, "amount": 108600, "finance_created": true, "finance_id": "..." }
```

## Mini App UI

Экран Списки → вкладка 🛒 Покупки:
- Над списком — карточка `summary` с планом и фактом если есть.
- В карточке пункта: name + точка приоритета + cat-pill в верхней строке;
  в нижней — `pricePlan ₽ · source`, заметка курсивом отдельной строкой.
- Тап на чекбокс → POST /checkout с `price=pricePlan` → запись в Финансы.

## Регрессия

### Сохранены без изменений
- `core/list_manager.check_items()` / `check_items_bulk()` — флоу «купила X»
- `core/list_classifier._LIST_BUY_RE`, `_LIST_DONE_RE` — pre-filter regex
- `arcana/handlers/lists.py` — флоу /list, мультиселект, checkout
- POST /api/lists/{id}/done — старое поведение
- Связка Lists → Tasks через «Группа»

### Совместимость парсера
- Старый формат Haiku-ответа `[...]` (массив) поддерживается через
  `parse_buy_response()` наряду с новым `{"items":[...]}`.
- «молоко в покупки» работает: один item, все extra-поля = `None`.
- `add_items` принимает старые ключи (`quantity`, `expiry`) и новые (`qty`, `expires`).

## Тесты

`tests/test_lists_v1_2.py` — 29 кейсов:
- regex hint extract_price_inline (3 кейса)
- match_sum_command (1)
- normalize_buy_item приоритет / note-truncate / coerce (3)
- parse_buy_response items / legacy / fence / bad json (4)
- parse_buy_text full + legacy (2)
- format_rub (1)
- _LIST_SUM_RE (1) + classify routes (2)
- _extract_page_data new + existing + relation-trailing-space (3)
- add_items v1.2 + legacy (2)
- get_list_summary aggregates + empty (2)
- handle_list_buy + handle_list_sum + empty (3)
- miniapp _serialize + _summary (2)

Все Haiku-вызовы и Notion-операции замоканы (AsyncMock + patch).

## Changelog

### v1.2 (май 2026)
- **NEW** Notion-поля: Цена план (number), Магазин (rich_text), Этап (number)
- **NEW** core/lists_parser.py — общий Haiku-парсер для обоих ботов (Nexus + Arcana)
- **NEW** парсер извлекает 10 полей вместо 2 (name + category)
- **NEW** core.list_manager.get_list_summary() — агрегация план/факт
- **NEW** Bot-команда «сумма / сколько / итого / подсчёт [X]»
- **NEW** Mini App POST /api/lists/{id}/checkout — авто-расход
- **NEW** Mini App GET /api/lists возвращает summary
- **FIX** Mini App тап «куплено» теперь пишет в Финансы (раньше тихо терялось)
- **REFACTOR** Удалён дубль `_PARSE_BUY_SYSTEM` в arcana/handlers/lists.py

### v1.1 (март 2026)
- Базовая БД 🗒️ Списки с типами (Покупки/Чеклист/Инвентарь)
- /list команда + мультиселект checkout
- Чеклист ↔ Задача через «Группа» = title задачи
- Cron `check_expiry` + `clone_recurring`
