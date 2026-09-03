# BUDGET — data-model contract (бюджет / day limit)

Code conforms to: 98d8aec. This spec describes the budget data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the derived model and the guarantees of
> each operation. Enumerations point at the owning code constant rather than
> restating it.

> **Отступление от конвенции specs (English-only):** раздел «Человеческая
> шпаргалка» ниже — по-русски, простыми словами, специально для Кай (СДВГ,
> явный запрос — спека должна быть понятна человеку, не только Claude-сессии
> в будущем). Технический контракт (Schema/Operations/Invariants и далее) —
> как обычно, на английском.

## Человеческая шпаргалка (для Кай)

### Четыре вида фактов — и в чём разница

1. **Постоянные** (ключ `постоянно_`; старое слово «обязательные» тоже
   работает — оставлено для привычки).
   Расход, который повторяется КАЖДЫЙ месяц: квартира, коммуналка,
   подписки, коты. Пишешь **один раз** — «постоянный расход квартира
   25000» — и он живёт вечно, повторять каждый месяц не нужно.
   Убрать: «убери постоянный расход интернет». Изменить: «измени
   постоянный квартира на 26000».

2. **Разовые** (триггер-слово «разовый»/«разовые»).
   Трата обязательна в ЭТОМ месяце, но НЕ повторится — билет, справка,
   доверенность, налог. **Обязательно пиши явно словом «разовый»**:
   «разовый расход билет 15000» (одна позиция) или «разовые: доверенность
   3500, налоги 8500» (несколько сразу через запятую/перенос строки). Без
   этого слова фраза уедет в обычную классификацию текста и может попасть
   не туда (обычная трата или постоянная память — как решит Haiku).
   Разовая трата НЕ становится фактом Памяти — это обычная запись в
   финансах (💸 Расход). В следующем периоде она никак не всплывает и
   пересчитывать бюджет из-за нее не нужно.
   Это работает и **внутри composite-дампа /budget**: если в одном
   сообщении настройки пометить строки «разовый: билет 15000» — Sonnet
   кладёт их в отдельный массив `one_time` (не в `fixed`). Такие строки
   **НЕ создают finance-транзакцию** — ни при показе плана, ни при «✅ Принять».
   Они участвуют **только в арифметике текущего расчёта**: входят в
   `one_time_total` → `already_spent` и уменьшают распределяемые в этом
   плане. Реальная транзакция появляется отдельно — когда Кай сама сообщает
   о трате обычным путём («потратила 15000 на билет»). (До фикса от 2026-09-02
   помеченные разовыми строки уходили в `fixed` → `постоянно_*` навсегда;
   до фикса от 2026-09-03 при «Принять» они писались в Финансы, что давало
   двойной счёт при пересчёте.)

3. **Доход** — тут ДВЕ разные вещи с похожим названием, легко спутать:
   - **План дохода** (ключ `income_`) — «бюджет 100000 рублей» / «у меня
     100к в месяц» / «мой доход 90000». Как и «Постоянные» — задаётся
     один раз и живёт вечно, используется как база расчёта КАЖДЫЙ период.
   - **Разовое поступление** («доход 50к нал», «получила зарплату») — это
     обычная финансовая запись (как расход, только со знаком «+»). Она
     **не меняет** план и не запоминается на будущее — это просто факт
     «эти деньги реально пришли в этом периоде». Учитывается Sonnet
     ТОЛЬКО если плана дохода вообще нет (fallback).

4. **Долги** — отдельно от Памяти, своя таблица (`debts`). Две команды
   попадают в одно и то же место:
   - прямая команда — «новый долг Маша 10к до июня», «закрыла долг Х»,
     «отдала Х 5к»;
   - или диалог внутри /budget — если в тексте настройки нашлось 2+ долга
     без стратегии, бот спросит «как планируешь отдавать?» (один долг —
     стратегия считается сама: сумма / месяцы до дедлайна).
   Платёж/стратегия задаётся один раз, дальше используется автоматически.
   В расчёт месяца попадает только ПЕРВЫЙ по дедлайну долг с платежом —
   остальные ждут своей очереди.

5. **Подушка** — пятая сущность, тоже отдельно от Памяти (своя таблица
   `cushion`), НЕ цель. Растущий буфер без конца, пополняется динамически
   (20% дохода в комфортный месяц / остаток факта в тяжёлый) + вручную.
   Полностью описана в `CUSHION.md`.

### Кто что считает

Арифметику плана (лимиты по категориям, взнос в подушку, выбор первого
горящего долга) считает **детерминированный Python** — `compute_limits()` в
`core/budget.py`. Sonnet в `/budget` работает в две фазы: разбирает свободный
текст в структуру (Фаза 1) и пишет текстовые пояснения вокруг уже готовых
цифр (Фаза 2). Ни одной суммы лимита модель не выдаёт. Обоснование —
`docs/CASES/0022`.

### Что пересчитывается само, а что нет

- Новый доход / новая постоянная / новый разовый расход **сами по себе
  НЕ пересчитывают** уже принятый план. Принятый план — это «замороженные»
  числа (факты `лимит_*`), а не формула, которая обновляется на лету.
- Чтобы бюджет учёл новые данные — нужно явно `/budget` → «🔄 Пересчитать»
  → (если тяжёлый месяц — выбрать вариант) → «✅ Принять». Только после
  «Принять» новые лимиты/долги/цели снова записываются как факты.
- Пока не нажала «Принять» — план существует только в текущей сессии
  (не в Памяти), можно свободно «✏️ Изменить данные» или пересчитать заново.

### Полный цикл /budget

> **Железный принцип: планирование пишет только в Память и служебные таблицы
> (`debts`, `cushion`). В Финансы планирование не пишет никогда.** Транзакцию
> в Финансах создаёт только Кай, вручную, по факту траты или поступления.

1. `/budget` — если данных ещё вообще нет (ни постоянных, ни лимитов) →
   бот просит написать всё одним сообщением (доход, постоянные, долги,
   цели), + отдельно напоминает про «разовый расход». Если данные уже
   есть — сразу показывает **сохранённый** план (без Sonnet, бесплатно).
2. Если в тексте 2+ долга без стратегии — бот спрашивает «как планируешь
   отдавать?» (свободный текст). Один долг — стратегия сама (сумма /
   месяцы до дедлайна).
3. Python считает план (`_apply_computed_limits` → `compute_limits`):
   - комфортный месяц (`free_after_debts ≥ 25 500₽`, т.е. остаток **после
     железных** транспорт+импульс ≥ 23 000₽ — единый порог
     `core.budget.BUDGET_TIGHT_THRESHOLD = _PRIORITY_FLOOR + IRON_TOTAL`) —
     один план;
   - тяжёлый месяц (`< 25 500₽`) **И есть реальный платёж по долгу в этом
     периоде** (`total_debt_payment > 0`) — два варианта: **А** «Платить
     по плану» (жёстко, с железными минимумами на еду/транспорт/импульсивные)
     и **Б** «Пересмотреть стратегию» (мягче, но платёж по долгу меньше);
   - тяжёлый месяц, **но платежа по долгу нет** (`total_debt_payment == 0`,
     все долги отложены/на паузе) — **один план** (`is_tight_month: false`,
     без А/Б). Варианту Б нечего уменьшать → развилку не показываем.
4. Если был выбор А/Б — жмёшь кнопку, план обновляется под выбранный вариант.
5. «✅ Принять» — план сохраняется как набор фактов: лимиты (`лимит_*`,
   ручные `[ручной]` не трогаются), постоянные из `plan["fixed"]` (`постоянно_*`),
   доход, цели (`цель_*` — **подушка НЕ здесь**, у неё своя таблица и
   вкладка, см. `CUSHION.md`), долги (в таблицу `debts` со
   стратегией/платежом). Взнос плана в подушку (`cushion_contribution`)
   записывается в `cushion.planned_contribution` и кредитуется в баланс
   один раз на переходе периода. Позиции из `plan["one_time"]` **никуда не
   пишутся** — ни в Память, ни в Финансы; они уже сделали своё дело в
   арифметике расчёта (см. «already_spent» ниже). В цикле `fixed` стоит
   sanity-`warning`, если туда всё же попадёт что-то со словом «разов» в названии.
6. Пока план не принят: «📋 Стратегия долгов» — переспросить стратегию,
   «✏️ Изменить данные» — свободным текстом сказать что поправить и
   пересчитать, «❌ Закрыть план» — выйти из режима настройки совсем.

### already_spent и экономия с прошлого периода

- **already_spent** — сумма ВСЕХ трат текущего периода (не только
  разовых — любая обычная финансовая запись, включая обычные «потратила
  X на Y») **плюс `one_time_total`** — разовые, помеченные прямо в
  composite-дампе /budget (они **никогда не становятся** finance-транзакцией
  через планирование — деньги просто считаются потраченными в этом расчёте).
  При пересчёте плана
  Sonnet сначала вычитает объединённый `already_spent` из «Распределяемых»
  — уже потраченное не считается ещё раз. В начале нового периода (после
  дня зарплаты) финансовая часть сама обнуляется — период для подсчёта
  сдвигается.
  В выводе плана: только разовые (реальных прошлых трат нет) → одна строка
  «📤 Разовые в этом периоде: X₽»; есть и реальные траты, и разовые из
  дампа → две строки — «📤 Уже потрачено (реальные траты): A₽» и
  «📤 Разовые из этого плана: B₽» (A = `already_spent − one_time_total`).
- **savings_from_last_period** — если в закончившемся периоде остались
  неизрасходованные деньги по лимитам, при переходе на новый период
  (день зарплаты) бот присылает обзор и держит эту сумму в памяти сессии
  до следующего пересчёта; при пересчёте плана она **прибавляется** к
  «Распределяемым» — сэкономленное реально уходит в план, а не просто
  упоминается фразой в тексте.
- Оба механизма считаются **один раз**, в общей `_period_spending()`
  (`nexus/handlers/finance.py`), и пробрасываются в **оба** промпта —
  полный (`BUDGET_SONNET_SYSTEM`, Шаг 1.5/1.6) и legacy (первый `/budget`
  с нуля, `_BUDGET_PARSE_PROMPT_LEGACY`, шаг 3). Одно место расчёта
  специально, чтобы не разъезжались как порог 18500/15500 ниже.

### Как Python распределяет лимиты по категориям

Считает **только** `core/budget.py:compute_limits()` (Sonnet цифры лимитов
не выдаёт). Все числа — именованные константы в начале `core/budget.py`,
менять там. Логика одна для комфортного месяца и для варианта А/Б тяжёлого:

1. `discretionary = distributable_pool − total_debt_payment` (это
   `free_after_debts`).
2. **Железные категории** снимаются первыми: 🚕 Транспорт `IRON_TRANSPORT`
   (1 500) + 🎲 Импульсивные `IRON_IMPULSE` (1 000) = `IRON_TOTAL` 2 500₽.
   🍜 Продукты и 🚬 Привычки в железные **не входят**. 💅 Бьюти — тоже нет
   (маникюр перестал быть фиксом). Если `discretionary < IRON_TOTAL` —
   транспорт и импульсивные режутся пропорционально, остальное 0.
3. `pool_after_iron = discretionary − IRON_TOTAL`.
4. **Комфортный месяц** (`pool_after_iron ≥ _PRIORITY_FLOOR`, 23 000):
   - в подушку резервируется `CUSHION_COMFORTABLE_RATE` (20%) от
     `income_total` **до** раздачи лимитов — лимиты считаются от
     уменьшенного пула;
   - 🍜 Продукты = `PRODUCTS_TARGET` (10 000) плоско;
   - 🚬 Привычки = `min(HABITS_CEILING 13 000, остаток пула − продукты)`;
   - остаток после продуктов и привычек — по **фиксированному приоритету**
     `PRIORITY_CHAIN`, каждая категория забирает 50% оставшегося, последняя
     в цепочке — весь хвост (округление не теряется). Не хватило → младшие
     = 0, это ожидаемо:
     1. 🍱 Кафе/Доставка
     2. 💅 Бьюти
     3. 🏥 Здоровье
     4. 👗 Гардероб
     5. 📚 Хобби/Учеба
5. **Тяжёлый месяц** (`pool_after_iron < _PRIORITY_FLOOR`): подушка 0₽,
   `pool_after_iron` делится **пополам** между 🍜 Продуктами и 🚬 Привычками,
   `PRIORITY_CHAIN` вся по нулям.

🐾 Коты и 🏠 Жильё/подписки — это `постоянные`/фикс, не переменные лимиты,
в этом дележе не участвуют.

### Шпаргалка команд

| Действие | Что писать |
|---|---|
| Добавить постоянный расход | «постоянный расход [что] [сумма]» (или «обязательный расход») |
| Убрать постоянный расход | «убери постоянный расход [что]» |
| Изменить постоянный расход | «измени постоянный [что] на [сумма]» |
| Разовая трата этого месяца | «разовый расход [что] [сумма]» или «разовые: [что] [сумма], [что] [сумма]» |
| Задать/изменить план дохода | «бюджет [сумма] рублей» / «у меня [сумма]к в месяц» |
| Разовое поступление денег (без изменения плана) | «доход [сумма] [нал/карта]» |
| Новый долг | «новый долг [имя] [сумма] до [месяц]» |
| Закрыть долг | «закрыла долг [имя]» |
| Частичная выплата долга | «отдала [имя] [сумма]» |
| Ручной лимит по категории | «лимит [категория] [сумма]» |
| Новая цель | «цель [что] [сумма]» |
| Показать план | `/budget` |
| Пересчитать с учётом новых данных | кнопка «🔄 Пересчитать» под /budget |
| Зафиксировать пересчитанный план | кнопка «✅ Принять» |

### ⚠️ Известные нестыковки в коде (зафиксировано как есть, не додумано)

- ~~Железные минимумы (продукты/транспорт/импульсивные) в промпте Sonnet
  суммируются в 15 500₽, но два UI-порога «показать ⚠️ жёстко» сравнивали
  со старым 18 500₽~~ — синхронизировано, оба места (`_run_budget_analysis`,
  `_format_plan`) теперь сравнивают с `15500`.
- ~~already_spent/savings_from_last_period реализованы только в «полном»
  промпте, не в legacy-промпте первого запуска~~ — синхронизировано,
  оба промпта получают эти значения через общий `_period_spending()`.
- ~~При «✅ Принять» позиции `plan["one_time"]` писались в Финансы как
  💸-транзакции — планирование не должно писать в Финансы, и это давало
  двойной счёт при пересчёте после Принятия~~ — **исправлено** (2026-09-03):
  `_save_budget_plan` больше не трогает Финансы, `one_time` живёт только в
  арифметике расчёта.
- ~~Порог «тяжёлого месяца» жил тремя разными числами: `is_tight` в
  `_apply_computed_limits` сравнивал `free_after` с `30000`, плашка «жёстко»
  и лейбл варианта А — с `25500`, а `compute_limits` — с `_PRIORITY_FLOOR`
  (23000) на `pool_after_iron`~~ — **исправлено** (2026-09-03): одна константа
  `core.budget.BUDGET_TIGHT_THRESHOLD = _PRIORITY_FLOOR + IRON_TOTAL` (25500,
  измеряется на `free_after_debts`), импортируется в `finance.py` в оба места.
  Развилка А/Б теперь включается ровно тогда, когда `compute_limits` считает
  месяц не комфортным.
- «Доход» как название путает две разные сущности: план дохода (`income_`,
  Памяти-факт, персистентный) и разовое поступление денег (обычная
  finance-транзакция типа «Доход», не персистентная). Если решишь это
  переименовать так же, как «обязательные → постоянные» — заводи отдельную
  задачу, здесь только зафиксировано наблюдение.
- ~~Утечка полного списка категорий (19 шт., с 🔮 Практика / 🕯️ Расходники
  Арканы, 💰 Зарплата, 💼 Фриланс) в бюджетные промпты; `_BUDGET_VARIABLE_CATS`
  — мёртвая константа~~ — **исправлено**. Оба промпта теперь получают
  ТОЛЬКО `_BUDGET_VARIABLE_CATS` (8) как категории для лимитов: legacy —
  плейсхолдер `{budget_limit_categories}` (`{finance_categories}` убран),
  Sonnet — поле `"budget_limit_categories"` в контекст-JSON
  (`_build_sonnet_input`, `"finance_categories"` убрано). В `ОГРАНИЧЕНИЯ`
  обоих промптов — «категории для limits ТОЛЬКО из этого списка».
  `_BUDGET_VARIABLE_CATS` совпадает с 3 якорными + 5 приоритетного дележа.

---

## Purpose

Budget is a **derived planning view**, not a stored table. It is computed
on demand from existing data: budget facts in Memory (the `memories` table)
plus active debts (the `debts` table). `core/budget.py` is the shared layer
behind the Nexus `/budget` command and the Mini App finance/day views; it
turns saved plan facts into per-category limits and a single daily spend
limit.

**Iron rule: planning writes only to Memory and the service tables (`debts`,
`cushion`). Planning never writes to Finance.** A `nexus_budget` transaction
is created only by Kai, by hand, on an actual spend or income event — never
by `/budget`, plan Accept, or recalc. `plan["one_time"]` items are declared
intentions, not payments; they affect only the arithmetic of the recalc that
declared them.

## Schema

Budget has **no table of its own** — there is no migration, no
`*_table.py`, no repo for "budget". It reads from:

1. **`memories`** (see MEMORY.md) — budget facts identified by `key_name`
   prefix and category. The prefix↔category map is owned by
   `core/budget.py:BUDGET_KEY_TO_CATEGORY`. Examples, non-exhaustive — see
   that constant:
   - `income_` → `📥 Доход`; `постоянно_` → `🔒 Постоянные`;
     `лимит_` → `💰 Лимит`; `цель_` → `🎯 Цели`; `долг_` → `📋 Долги`.
   - the payday is a single Memory fact at exact key `budget_payday`
     (default `1` if absent).
   Amounts are parsed from the fact text by the regexes in `core/budget.py`
   (`LIMIT_AMOUNT_RE`, `INCOME_RE`, `PERMANENT_RE`, `GOAL_RE`).
2. **`debts`** (see the debts domain, `core/repos/pg_debts_repo.py`) — active
   debts with `kind='i_owe'`; the fields consumed are `name`, `amount`,
   `deadline`, `strategy`, `monthly_payment`. Debts come from this table, not
   from Memory. Two independent input paths write here: `_DEBT_CMD_RE`
   commands (`core/classifier.py` → `handle_debt_command`, regex-only, no
   LLM) and the free-text `долг X` phrasing routed through `memory_save` →
   `core/memory.py:save_memory` (Haiku-parsed, then redirected to
   `pg_debts_repo` instead of Memory — see `_save_debt_from_memory`).
3. **One-time expenses are NOT budget facts.** `разовый расход X` /
   `разовые: ...` is classified as `one_time_expense` (`core/classifier.py`,
   `_ONE_TIME_EXPENSE_RE`, checked before `memory_save`/`budget`) and
   written as ordinary `nexus_budget` finance transactions via the shared
   `nexus/handlers/finance.py:_write_one_time_expense` → `_save_finance` →
   `_repo.create_entry`, type `💸 Расход`. They feed into the plan only
   indirectly, via `spending_by_category` → `already_spent` (see Model
   routing). **Inside a composite `/budget` dump**, lines tagged `разовый:`
   are returned by Sonnet in a separate `one_time` array (not `fixed`) and
   contribute to `one_time_total`/`already_spent` **only** — arithmetic of
   the current recalc. `_save_budget_plan` writes them **nowhere**: not to
   Memory, not to Finance. **Planning never creates a finance transaction**
   (see the iron rule below); the real transaction is created separately when
   Kai reports the spend herself.

The limit display map (`лимит` link → emoji label) is owned by
`core/budget.py:LIMIT_DISPLAY`.

## Operations & contract

All in `core/budget.py` (pure async functions; no repo class):

- **get_limits()** → `{cat_link: amount}` — reads current `💰 Лимит`
  memories (`find_by_category`), extracts the category link and amount per
  fact. Skips facts where link or amount can't be parsed.
- **load_budget_data(user_notion_id)** → `{"доходы", "постоянные", "цели",
  "долги", "лимиты"}` — reads budget memories by key prefix
  (`find_by_key_prefixes(["income_", "постоянно_", "лимит_", "цель_"])`,
  current rows only) plus active `i_owe` debts; parses each into a list of
  `{name, amount, …}` dicts. Limits are de-duplicated by display name (the
  higher amount wins).
- **budget_day_limit_from_plan(user_notion_id)** → `int` — the daily spend
  limit from the saved plan (see Invariants for the exact formula). Returns
  `0` when there is no income or on any error.

## Invariants

- **Day-limit formula** (`budget_day_limit_from_plan`), exactly as coded:
  ```
  free = total_income
       − total_obligatory          # sum of постоянные[].amount
       − total_limits
       − total_goals_saving        # sum of цели[].saving
       − total_debt_monthly        # sum of долги[].monthly_payment > 0
  day_limit = max(0, int(free / days_remaining))
  ```
  where `total_income = sum(доходы[].amount)`; if `total_income <= 0` the
  function returns `0` immediately. **Not the same formula** as the Sonnet
  full-recalc path (which additionally applies `already_spent` and
  `savings_from_last_period` — see Model routing); this is the
  lightweight day-limit-only computation used by the Mini App today view.
- **`days_remaining`** comes from `_period_days_remaining(payday)`: days from
  today (00:00 MSK, `_MOSCOW_TZ = UTC+3`) to the day before the next payday,
  floored at `1`. `payday` is read from Memory key `budget_payday`
  (`_budget_payday`, default `1`).
- **Debts are sourced from the `debts` table**, not Memory; only
  `kind='i_owe'`, and only `monthly_payment > 0` contributes to the daily
  formula. Only the single nearest-deadline debt with a monthly payment is
  "active" in a period — see `nexus/handlers/finance.py` debt queueing
  logic (`debts_monthly` vs `queued_debts`).
- **Limits de-duplicate by display name** (`display_limit_name` via
  `LIMIT_DISPLAY`); the larger amount is kept.
- **Computation is stateless.** Each call recomputes from current Memory +
  debts; no computed budget is persisted between calls — but an *accepted*
  plan's derived numbers (`лимит_*`, `цель_*`, debt strategies) ARE persisted
  as facts by `_save_budget_plan`, and stay static until the next accepted
  recalculation (see Human cheat sheet above — "what recalculates on its own").

## Lifecycle / status model

No lifecycle for the derived view itself — budget is recomputed per request.
The underlying facts follow their own stores' lifecycles: budget memories
are soft-deleted/updated per MEMORY.md; debts per the debts domain.

The **setup/recalc session**, however, does have a lifecycle, tracked in a
separate SQLite store (`nexus/handlers/finance.py:_bdb()`,
`pending_budget.db`, table `budget_pending` — moved under `/app/data` in the
#191 fix so it survives container restarts):
`collecting` → (`awaiting_debt_strategy` if 2+ debts without a strategy) →
`analyzing` (Sonnet call) → `has_plan` (plan shown, buttons active,
`bsetup_accept`/`bsetup_recalc`/`bsetup_adjust`/`bsetup_close` available) →
either `_save_budget_plan` (facts written) or session closed/expired.
TTLs: `_BUDGET_TTL` (60 min, collecting/adjusting), `_BUDGET_HAS_PLAN_TTL`
(15 min, has_plan — a shown-but-unaccepted plan expires and must be
recomputed), `_PAYDAY_TTL` (~25h, the "already sent today's payday review"
marker).

The Nexus `/budget` command with no active session and existing data
renders the saved plan and does **not** call Sonnet — see Model routing.

## Callers

- Nexus — `nexus/handlers/finance.py` (`/budget`: `get_limits`,
  `load_budget_data`; the message renders the saved plan), `nexus/nexus_bot.py`.
- Mini App — `miniapp/backend/routes/finance.py` (`get_limits`,
  `load_budget_data`, `budget_day_limit_from_plan` for limit/goal views) and
  `miniapp/backend/routes/today.py` (`budget_day_limit_from_plan` for the day
  limit).

## Model routing (from code)

`core/budget.py` itself uses **no LLM** — it is regex parsing plus
arithmetic. The default `/budget` message is built from the saved plan
without Sonnet (`start_budget_analysis`, "v3.0: /budget shows SAVED plan
from Memory. Recalc only via button").

The on-demand recalculation (`🔄 Пересчитать` / any text while a plan
session is open) uses Sonnet and picks one of **two different prompts**
(`nexus/handlers/finance.py:_run_budget_analysis`):
- **`has_existing_data == True`** (`load_budget_data` — Memory facts plus
  debts — already has *any* non-empty bucket) →
  full context via `_build_sonnet_input` + `BUDGET_SONNET_SYSTEM` (Шаг 1.5/1.6).
- **`has_existing_data == False`** (brand-new setup, empty Memory) →
  `_BUDGET_PARSE_PROMPT_LEGACY`, user's raw setup text only (no
  `_build_sonnet_input` context — no `income_from_memory`, `manual_limits`, etc.).

Both prompt schemas split recurring vs one-off into two arrays — `fixed`
(→ `fixed_total`, → `постоянно_*` on Accept) and `one_time` (→
`one_time_total`, arithmetic only — **not persisted anywhere** on Accept).
`one_time` is **not** in `fixed_total`; it is added into `already_spent`.

Both prompts get `already_spent` (sum of `spending_by_category` — all
`nexus_budget` transactions this period, one-time expenses included, **plus
`one_time_total`** from the current dump) and `savings_from_last_period`
(carried in the `pending_budget.db` session state, set by
`_send_payday_review`) — the `spending_by_category`/finance part from the
**same** helper,
`_period_spending()` — extracted specifically so the two prompts can't
drift apart on this (they did once, see the `18500`/`15500` note above).

Both prompts also see **only** `_BUDGET_VARIABLE_CATS` (8 categories) as the
limit-category vocabulary — legacy via the `{budget_limit_categories}`
placeholder, Sonnet via the `budget_limit_categories` context field. The
full 19-item `FINANCE_CATEGORIES` (with the Arcana `🔮 Практика` /
`🕯️ Расходники` and income categories) is **not** exposed to the budget
prompts.

Sonnet is justified for both (long-form budget reasoning, debt-strategy
trade-offs); Haiku is used only for the smaller sub-parses along the way
(debt-strategy free text → `_parse_debt_strategy_with_haiku`, one-time
expense items → `_ONE_TIME_PARSE_SYSTEM`).

## Verify against code

- `core/budget.py` — `BUDGET_KEY_TO_CATEGORY`, `LIMIT_DISPLAY`, regexes
  (`PERMANENT_RE` et al.), `get_limits`, `load_budget_data`,
  `_budget_payday`, `_period_days_remaining`, `budget_day_limit_from_plan`,
  limit-math constants (`IRON_TRANSPORT`/`IRON_IMPULSE`/`IRON_TOTAL`,
  `PRODUCTS_TARGET`, `HABITS_CEILING`, `_PRIORITY_FLOOR`,
  `CUSHION_COMFORTABLE_RATE`, `BUDGET_TIGHT_THRESHOLD`), `compute_limits`,
  `_distribute_limits`, `PRIORITY_CHAIN`
- `core/memory.py` — `CATEGORIES`, `_PARSE_SYSTEM` (постоянно_/долг_/
  income_ examples), `save_memory` (`долг_` → redirected to
  `pg_debts_repo`, not Memory)
- `core/classifier.py` — `_MEMORY_SAVE_RE` (постоянн/обязательн dual
  support), `_ONE_TIME_EXPENSE_RE`, `_DEBT_CMD_RE`, `_GOAL_CMD_RE`,
  `_LIMIT_OVERRIDE_RE`, `_BUDGET_RE`, `classify()`/`process_item()` routing
- `core/repos/memory_repo.py` / `core/repos/pg_memory_repo.py` —
  `find_by_category`, `find_by_key_prefixes`, `find_by_exact_key` (budget facts)
- `core/repos/pg_debts_repo.py` — active `i_owe` debts read by `load_budget_data`
- `nexus/handlers/finance.py` — `start_budget_setup`, `handle_budget_setup_text`,
  `start_budget_analysis`, `_run_budget_analysis`, `_build_sonnet_input`,
  `_period_spending` (shared already_spent/income-this-period source),
  `BUDGET_SONNET_SYSTEM`, `_BUDGET_PARSE_PROMPT_LEGACY`, `_format_plan`,
  `_save_budget_plan` (`fixed` → `постоянно_*`; `one_time` → not persisted),
  `handle_one_time_expense`, `_write_one_time_expense` (shared one-time
  writer), `_ONE_TIME_PARSE_SYSTEM`,
  `_BUDGET_VARIABLE_CATS` (the 8 limit categories fed to both prompts),
  `_apply_computed_limits`/`_limits_fields` (deterministic limit math),
  `BUDGET_TIGHT_WARN` (alias of `core.budget.BUDGET_TIGHT_THRESHOLD`),
  `_bdb`/`_BUDGET_DB` (session store), `_send_payday_review`
- `nexus/nexus_bot.py` — `/budget` wiring, startup `proactive_budget_review`
- `miniapp/backend/routes/finance.py` — limits/goals views, day limit
- `miniapp/backend/routes/today.py` — `budget_day_limit_from_plan` (day limit)
- `docs/specs/MEMORY.md` — budget facts live in the `memories` table
