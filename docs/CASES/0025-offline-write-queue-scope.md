# ADR-0025 — офлайн-очередь на запись: только «на бегу» сущности, не вся Аркана

**Date:** 2026-09-09
**Status:** Accepted (Phase 1)
**Domain:** Mini App (`miniapp/frontend`), `miniapp/backend/routes/writes.py`
**Issue:** #189

## Context

#189: Mini App должен принимать записи без сети (самолёт, лес) и досылать
их при подключении. Разведка показала:

- все записи идут через 3 функции `api.js` (`apiPost` / `apiPatch` /
  `apiDelete`) — единая точка;
- денежные POST уже несут `Idempotency-Key` и дедуплицируются на бэке
  (`core/repos/idempotency_repo.py`, INSERT ON CONFLICT + replay-кеш);
- «8 pending SQLite» из формулировки issue — это серверное состояние
  бот-диалогов, к офлайну Mini App отношения не имеют.

Реальный вопрос — **какие сущности класть в офлайн-очередь**. Все POST-ы
Mini App или подмножество?

## Decision

**Phase 1 — очередь на запись только для «на бегу» сущностей:**
задачи, финансы (расход/доход/долг/цель/подушка), списки, память.

Механика:
1. `src/outbox.js` — FIFO-очередь в `localStorage`, элемент
   `{id, method, path, body, idempotencyKey, ts, tries}`.
2. `api.js._mutate`: `!navigator.onLine` или сетевая ошибка (TypeError от
   `fetch`) на whitelisted-пути → `enqueue()` + бросить `OfflineQueuedError`
   (`.queued === true`). UI трактует как «сохранено локально», не ошибку.
3. Досыл: на `window 'online'`, при монтировании App, раз в 30с
   (`useOfflineQueue`). Реплей по порядку; `Idempotency-Key` (сгенерирован
   при enqueue, стабилен между попытками) делает двойную отправку
   безопасной; перманентный 4xx → дроп + `console.warn`.
4. Бэкенд: `idempotent()` добавлен на create-эндпоинты без него — `/tasks`,
   `/lists`, `/memory`, `/finance/debt`, `/finance/goal/contribute`,
   `/finance/cushion/deposit`. `mark-done/cancel/reopen` идемпотентны по
   природе, не трогаем.

Whitelist путей — `outbox.js:OFFLINE_PATHS`.

## Почему не вся Аркана в Phase 1

Ритуалы/сеансы **нужны** офлайн (Кай: «в лесу связи нет») — но их запись
устроена принципиально иначе, чем задача/расход:

| | задача / расход | ритуал / сеанс |
|---|---|---|
| вход | структурная форма (title, amount, cat) | свободный текст + фото |
| обработка на сервере | прямой INSERT | Haiku-парсинг, `resolve_or_create` клиента, `work_relation`, RAG-индексация, бартер-промпт |
| результат | одна строка | N триплетов + Работа + финансовая запись + вектор |
| конфликт | нет (append) | клиент мог быть создан/переименован оффлайн-периодом |

Класть сырой текст ритуала в ту же `outbox`-очередь и гнать его в
`POST /api/arcana/...` при синке — можно, но:
- эндпоинтов создания ритуала/сеанса **из Mini App пока нет** (создаются
  только ботом; Mini App их только читает + verify);
- парсинг на синке = отложенный Haiku-вызов, нужен UX «обрабатываю
  отложенное» + обработка ошибок парсинга задним числом;
- клиент-резолюшн оффлайн — отдельная задача (кэш списка клиентов на
  устройстве, локальное создание, слияние при синке).

Это Phase 3, отдельная проработка (см. #189). Phase 1 сознательно
ограничен тем, что уже имеет create-эндпоинт и append-семантику.

## Alternatives rejected

- **Service worker + PWA + офлайн-кэш GET сразу** — Phase 2 (#189).
  Чтение офлайн — самостоятельная работа (кэш-стратегия, инвалидация,
  manifest), не блокирует очередь на запись.
- **IndexedDB вместо localStorage** — оверинжиниринг для очереди из
  единиц элементов по ~200 байт. `localStorage` синхронный, проще, есть
  везде; try/catch на приватное окно.
- **Версионирование записей + отказ на устаревшей (`If-Match`)** — не в
  Phase 1. Create/mark-done — last-write-wins безопасно. Правки
  (task edit, memory edit) из офлайна редки; риск затереть серверное
  изменение принят осознанно, конфликт-резолюшн — Phase 2/3.

## Consequences

- Записать расход/задачу/список/память в самолёте → синк на земле работает
  с одной записью на выходе (idempotency).
- Ритуал в лесу пока НЕ поддержан офлайн — Кай знает, ждём Phase 3.
- Правка (не создание) из офлайна может тихо перезаписать более свежее
  серверное значение — редкий кейс, задокументирован.
- `outbox.js` не покрыт автотестами (в репо нет JS-раннера); контракт
  бэкенда (idempotency) покрыт `tests/test_idempotency.py`.

## Verify against code

- `miniapp/frontend/src/outbox.js` — очередь, whitelist, `flushOutbox`
- `miniapp/frontend/src/api.js` — `_mutate`, `OfflineQueuedError`,
  `flushOfflineQueue`, `_sendQueued`
- `miniapp/frontend/src/App.jsx` — `useOfflineQueue`, хедер-бейдж,
  `QuickForm.wrap` (обработка `.queued`)
- `miniapp/backend/routes/writes.py` — `idempotent()` на create-эндпоинтах
- `core/repos/idempotency_repo.py` — дедуп
- `tests/test_idempotency.py` — `/tasks` `/lists` `/memory` dedup
