# CUSHION — data-model contract (финансовая подушка)

Code conforms to: HEAD of the `budget: динамический взнос в подушку` change.
Update this spec in the same PR that changes the model.

> Contract, not snapshot. Describes the derived model and the guarantees of each
> operation. Enumerations point at the owning code constant rather than restating it.

> **Отступление от конвенции specs (English-only):** раздел «Человеческая
> шпаргалка» ниже — по-русски, простыми словами, для Кай (СДВГ, явный запрос).
> Технический контракт (Schema / Operations / Invariants) — на английском, как обычно.
> Родственная спека — `BUDGET.md`. Архитектурное обоснование — `docs/CASES/0022`.

## Человеческая шпаргалка (для Кай)

### Что такое подушка и чем она НЕ является

Подушка — это **растущий буфер без конечной точки**. Не «накопить 100к и купить
телефон», а «пусть лежит и растёт, на случай если всё пойдёт не так».

Это **отдельная сущность**, не цель:

| | Цель (`цель_`) | Подушка |
|---|---|---|
| Смысл | накопить сумму → купить одну вещь → закрыть | буфер, растёт бесконечно |
| Где хранится | факт в Памяти (`цель_телефон`) | своя таблица `cushion` в PG |
| Есть конец? | да (target достигнут = закрыта) | нет (target — только ориентир прогресса) |
| Трекинг накоплений | нет | да — баланс + лог каждого пополнения |

Раньше (до 2026-09-02) подушка заводилась как обычная `цель: подушка 100000`
и смешивалась в списке целей с айфоном и наушниками. Теперь — своя вкладка,
свой баланс, своя история.

**Старый факт `цель_подушка` в Памяти игнорируется** — и ботом, и Mini App.
Если он у тебя остался от прошлых планов, он просто не показывается. Заводить
подушку заново — новым форматом (ниже).

### Как подушка пополняется — две ветки

Подушка растёт **на переходе периода** (в день пэйдея, вместе с ревью). Сумма
пополнения = **план + факт**:

1. **План** — взнос из последнего принятого бюджет-плана прошлого периода:
   - **Комфортный месяц** (после железных минимумов в пуле остаётся ≥ 23 000₽ —
     хватает на продукты-цель + потолок привычек): в подушку резервируется
     **20% дохода** (`CUSHION_COMFORTABLE_RATE`) — *до* расчёта лимитов, то есть
     лимиты по категориям считаются уже от уменьшенного пула. В плане это видно
     строкой «🛡️ Подушка: +X₽ (20% дохода, комфортный месяц)».
   - **Тяжёлый месяц** (пула не хватает): заранее **ничего не резервируется** —
     весь остаток идёт на жизнь. В плане: «🛡️ Подушка: 0₽ в этом периоде —
     тяжёлый месяц, остаток пойдёт по факту».

2. **Факт** — реальная экономия периода: если по итогам месяца потратила меньше
   лимитов (`total_saved > 0` в ревью), эта разница тоже уходит в подушку.
   Работает **в обоих типах месяца** — даже в тяжёлый, если удалось сэкономить.

Обе суммы складываются в **одно** пополнение и одно сообщение:
«🛡️ Расчёт подушки: +{X}₽ (план: {A}₽ + экономия: {B}₽). В трекере теперь:
{баланс}₽. 💳 Не забудь перевести {X}₽ на реальный счёт подушки вручную —
трекер сам деньги не двигает, только считает.»

3. **Nexus не связан с банком — все суммы виртуальные, реальный перевод делает
   сама Кай.** Трекер только считает и показывает, «баланс» в нём — это
   «сколько *должно* лежать на счёте подушки, если переводить дисциплинированно»,
   а не остаток реального счёта. Пополнение вручную (команда «положила в
   подушку X») — это то же самое: запись в трекер, деньги переводишь отдельно.

Пополнение на переходе периода происходит **один раз за период** (под тем же
guard'ом, что и ревью) — повторно принять тот же план в течение месяца и
задвоить накопление нельзя.

### Команды ввода (в чат боту)

- **Пополнить вручную:** «положила в подушку 5000», «добавь в подушку 5к»,
  «закинула в подушку 3000». → баланс += сумма, запись в лог с пометкой
  «вручную».
- **Задать / изменить цель-ориентир:** «подушка 250000», «измени подушку на
  250к», «подушка цель 250000». → меняется только `target`, баланс не трогается.
  Цель необязательна — без неё подушка просто показывает баланс без процента.

Ловится по слову «подушк…» + число, **до** классификации целей/долгов/памяти —
поэтому «подушка 250000» больше не уедет в `цель_подушка`.

### Что видно в Mini App

Отдельная вкладка **«Подушка»** в Финансах (рядом с Сегодня / Месяц / Лимиты /
Цели):

- баланс крупно + прогресс-бар до цели (если цель задана) + строка плана
  прошлого периода;
- история пополнений: дата · сумма · «вручную» / «с зарплаты» · пометка;
- поле редактирования цели (сохраняется через `POST /api/finance/cushion/target`).

Маленькой карточки подушки во вкладке «Цели» больше нет — она живёт только
в своей вкладке.

---

## Purpose

Track a no-endpoint savings buffer separately from `цель_` goals: a running
balance, an optional aspirational target, a per-transaction accumulation log, and
a contribution figure that adapts to the month's budget plan.

## Schema

### Table `cushion` — one row per user

See `core/repos/cushion_table.py`.

| column | meaning |
|---|---|
| `user_notion_id` | owner; unique (`uq_cushion_owner`) — one cushion per user |
| `balance` | accumulated total; **incremented only**, never overwritten |
| `target` | aspirational figure for the progress %; nullable |
| `planned_contribution` | `cushion_contribution` from the last accepted budget plan (20% of income in a comfortable month, 0 in a tight one); written by `_save_budget_plan`, read by `_send_payday_review` |

Row is lazily created on first write (`_ensure_row_sync`).

### Table `cushion_transactions` — append-only log

| column | meaning |
|---|---|
| `amount` | credited amount |
| `source` | `'manual'` \| `'payday_auto'` (`ck_cushion_tx_source`) |
| `note` | free-text detail (period tag, plan/underspend breakdown) |
| `created_at` | index `ix_cushion_tx_owner_created` |

Every `balance` increment writes exactly one log row **in the same DB
transaction** as the increment.

### `compute_limits()` return — `core/budget.py`

`compute_limits(distributable_pool, total_debt_payment, income_total=0.0)` returns
`{"limits": {category: int}, "cushion_contribution": int}`.

- `cushion_contribution = round(income_total * CUSHION_COMFORTABLE_RATE)` iff
  `(discretionary − IRON_TOTAL) ≥ PRIORITY_FLOOR` (comfortable) **and**
  `income_total > 0`; clamped to `[0, discretionary]`; subtracted from the pool
  **before** `limits` are distributed.
- `0` otherwise (tight month, or income unknown).
- `sum(limits.values()) == discretionary − cushion_contribution`, exact.

## Operations & contract

| operation | code | effect |
|---|---|---|
| manual deposit | `handle_cushion_command` (deposit branch) → `add_to_balance(source='manual')` | `balance += amount`; log row |
| set target | `handle_cushion_command` (target branch) / `POST /finance/cushion/target` → `set_target` | `target` set (or cleared on 0/null); **balance untouched, no log row** |
| plan accepted | `_save_budget_plan` → `set_planned_contribution(plan["cushion_contribution"])` | `planned_contribution` overwritten; **balance untouched** |
| period rollover | `_send_payday_review` → `add_to_balance(planned + total_saved, source='payday_auto')` | one credit = accepted plan's contribution + positive real underspend; one log row; one message |
| read (bot) | `core/budget.py::load_budget_data` → `result["подушка"]` | `{balance, target, planned_contribution}`; absent key if no row |
| read (Mini App) | `GET /api/finance?view=cushion` | `{balance, target, planned_contribution, page, has_more, transactions[]}` |

## Invariants

- `balance` is monotonic non-decreasing (no operation debits it).
- `set_target` and `set_planned_contribution` never touch `balance` and never
  write a `cushion_transactions` row.
- Payday credit fires **at most once per budget period** — `_send_payday_review`
  is under the `_payday_already_sent` / `_payday_mark` guard; re-accepting the
  same plan mid-period does not re-credit.
- `цель_подушка` Memory facts are excluded from the goals list everywhere
  (`load_budget_data`, `_load_closed_budget`) — the cushion is not a goal.
- Comfortable-month cushion reservation happens **before** limit distribution:
  the category limits shown always sum to the post-cushion pool.

## Callers

- `nexus/handlers/finance.py` — `handle_cushion_command`, `_apply_computed_limits`,
  `_limits_fields`, `_format_plan`, `_save_budget_plan`, `_send_payday_review`,
  `_budget_period_review` (cushion-aware advice)
- `core/classifier.py` — `_CUSHION_CMD_RE`, `classify()` / `process_item()` routing
  (`cushion_command`, before goal/debt/memory)
- `core/budget.py` — `compute_limits`, `load_budget_data`
- `miniapp/backend/routes/finance.py` — `_view_cushion`
- `miniapp/backend/routes/writes.py` — `POST /finance/cushion/target`
- `miniapp/frontend/src/App.jsx` — `CushionScreen`; `adapters.js` —
  `adaptFinanceCushion`

## Verify against code

- `core/repos/cushion_table.py`, `core/repos/pg_cushion_repo.py`
- `core/budget.py` — `compute_limits`, `_distribute_limits`,
  `CUSHION_COMFORTABLE_RATE`, `load_budget_data` cushion block
- `nexus/handlers/finance.py` — functions listed under Callers
- `core/classifier.py` — `_CUSHION_CMD_RE`, cushion routing
- `alembic/versions/b8c9d0e1f2a3_cushion.py`,
  `c9d0e1f2a3b4_cushion_transactions.py`,
  `d0e1f2a3b4c5_cushion_planned_contribution.py`
- `miniapp/backend/routes/finance.py` (`_view_cushion`),
  `miniapp/backend/routes/writes.py` (`finance_cushion_set_target`)
- `docs/CASES/0022-budget-arithmetic-determinism.md`
