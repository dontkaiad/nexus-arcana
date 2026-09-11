# ADR-0026 — heylark Booking: веб-календарь на `booking.heylark.dev` + бот-консьерж `@heylark_booking_bot`, роли из общей `grants`, free/busy на внутренних событиях

**Date:** 2026-09-10 (§6 пересмотрен 2026-09-10 — бот вынесен из Nexus)
**Status:** Accepted — backend + фронт (B5) + линковка задач (B6) + Zarya
как двусторонний букинг-менеджер (Z1–Z4: уведы обеим сторонам, напоминания
T-24ч/T-2ч, отмена) построены (сен 2026). B7 (админ-конфиг в Mini App —
история, перенос) реализован, issue открыт под дальнейшие правки Кай.
Эпик #23.
**Domain:** новый `core.booking` + `miniapp/backend` + `heylark_booking_bot` (новый бот) + отдельный Vite-entry
**Issue:** #23 (эпик), развивает [ADR-0012](0012-access-model.md)

> Этот ADR — историческое решение (контекст/альтернативы/rationale), не
> живая модель. За актуальным контрактом (схема, API, инварианты) —
> [`docs/specs/BOOKING.md`](../specs/BOOKING.md), обновляется в том же PR,
> что и модель.

## Контекст

ADR-0012 отложил «calendar visibility tiers + booking landing» в отдельную
проработку. Нужна страница, где:

- **друзья** видят занятость Кай и бронируют встречи;
- **эзо-клиенты Арканы** записываются на расклады/ритуалы;
- гость видит только «занято/свободно» без деталей.

Собрано из двух архитектурных обсуждений (12 и 29 июня) + разведки
(planerka.app — Calendly-модель; Яндекс.Календарь — вид сетки, страницы
бронирования там нет). Полный дизайн и фазы — в эпике #23.

Внешний контекст: nexus-arcana живёт в экосистеме heylark.dev (один VPS,
Docker Compose, Caddy, GitHub Actions; продукты `cats`, `jobs`, dashboard).
Общий слой доступа уже работает и протестирован на `cats`.

## Решение

### 1. Роли — из общей `grants`, не из `core_identity`

- `grants (tg_id, app, role, status)` — существующая таблица heylark
  (используется `cats`); для календаря добавляется `app='booking'`.
- `people (tg_id, display_name)` — общая таблица людей, переиспользуется.
- **admin** — Кай, два `tg_id` захардкожены в коде (не грант-строка).
- **friend** — есть строка в `grants`.
- **guest** — дефолт, строки нет.
- **Роль резолвится по `tg_id` отправителя/сессии, никогда по факту членства
  в Telegram-группе** — состав групп плавающий. Бот и веб сверяют один и тот
  же `grants`: один источник правды на оба входа.
- Выдача вручную: `/grant <tg_id> friend booking`. Без инвайт-ссылок,
  токенов, сроков — масштаб ~5 друзей.

`core_identity` (ADR-0012: «single owner, no in-app RBAC») не трогается —
`grants` это тот самый sharing-слой, который ADR-0012 отложил.

### 2. Видимость — «что видно», не «пускать/не пускать»

Логин-гейт только на сабдоменах; `booking.heylark.dev` открыт всем.
**Вид сетки один для всех.** Содержимое ячеек (что за встреча) и реальные
слоты с возможностью брони — только при `hl_session` + `tg_id` в
`grants` под ролью `friend`/`admin`. Гость видит блоки занятости без деталей,
без брони. admin (Кай) видит содержимое + пишет напрямую.

### 3. Один вход, две аудитории (контексты)

Один URL `booking.heylark.dev` с развилкой на входе: **друг** /
**записаться на расклад/ритуал**. Кай одна, каналов два (Nexus / Аркана) —
сабдомены не плодим, но и не сливаем. Один движок free/busy, два контекста:

| | `friends` | `arcana` |
|---|---|---|
| доступ | `grants` friend/booking | публично |
| типы встреч | кофе / созвон / прогулка / визит | расклад / ритуал-консультация / сессия |
| окна доступности | **пересмотр (#233):** нет — любой свободный час 13:00–23:00 МСК, `booking_availability` не участвует | свои (`booking_availability`, весь §7 ниже) |
| подтверждение | **нет** — авто-бронь (вариант A) | **да** — «Ожидает подтверждения», Кай подтверждает |
| длительность | заявитель указывает («на сколько часов занять»), дефолт 1ч | фикс по типу встречи |
| запись создаёт | Nexus-задачу на встречу + постфактум-алерт | **🔮 Работу** (`works` + `scheduled_at` + статус «Запланировано») |
| тема | Nexus (день) | Arcana (ночь) |

Обе брони feed'ят одну занятость: эзо-запись блокирует слот друга и наоборот.
Эзо-клиент видит вычисленные свободные слоты под выбранный тип, не сырую сетку.

### 4. Подтверждение брони — вариант A для друзей

Одобренные друзья бронируют сразу, без шага подтверждения. Кай узнаёт
постфактум: Nexus создаёт задачу на встречу + шлёт алерт. Для `arcana` —
подтверждение обязательно (approval-петля с inline-кнопками).

### 5. Источник free/busy — внутренние события, без Google Calendar

Кай не использует Google Calendar, планирует Apple. Источник истины:

- Nexus `tasks` **с временем встречи**, Arcana `works.scheduled_at`,
  подтверждённые брони, ручные `booking_block`.
- **Пересмотр при реализации:** любая активная задача с `deadline` блокирует
  слот (Кай: «по дедлайнам все мои задачи маркируются как встречи») — не
  только задачи с явным «временем встречи», как планировалось здесь; задача
  только с `reminder` (без дедлайна) слот НЕ блокирует. См. `core/booking/busy.py`.
- Длительность занятости: дефолт **1 час**; переопределяется per-row через
  `tasks.duration_min`/`works.duration_min`, если Кай его выставила (#241).
- Окна доступности (`booking_availability`) — делаем, по контексту.

**Apple — один общий исходящий `.ics`-фид** (`/feed/<token>.ics`,
подписка read-only). Одностороннее: читать из Apple ничего не нужно.
Раздельные срезы (друзья / Аркана / личное) Кай смотрит во вкладке «Мой день».

### 6. Бот — **Заря** (⭐ «Заря-заряница»), отдельный `@heylark_booking_bot` (пересмотр)

**Пересмотр решения.** Первоначально — booking-скилл в Nexus; отвергнуто:
`dp.message.middleware(WhitelistMiddleware())` режет любое сообщение не от
`allowed_ids` до хендлеров, а чат-бот в дружеской группе — ещё и риск утечки
приватной поверхности Nexus. Решение: **отдельный бот — `@heylark_booking_bot`, персона «Заря»** (женский
род, как Аркана; ⭐ звезда-заряница — страж небесных врат, между солнцем
Nexus и луной Arcana). Не Nexus, не Arcana, не `l4rk_sys_bot` (тот send-only).

Бот — **не общий ассистент** (для этого есть Nexus/Arcana), а букинг +
консьерж. Тонкий фронт над `core/booking/` + FastAPI; шарит `core/` + Postgres.

**Гейт — общая `grants` (`app='booking'`), по `tg_id` отправителя:**
- **admin** (`config.allowed_ids`) — в ЛС: пульт букинга (входящие заявки,
  `[✅ Подтвердить] [❌] [✏️ Другое время]`, «кто записан на неделе»,
  управление `booking_availability`/`booking_meeting_type`/`booking_block`).
- **friend** (approved grant) — реальные слоты + бронь + консьерж-ответы.
- **guest** — только «занято/свободно» + «напиши Кай напрямую».

**В группах:** LLM-вызов (Haiku) только на прямое обращение (`@mention` /
reply на бота) — не на каждое сообщение (токены Кай). «когда у Кай окно во
вторник» → движок → «свободно вт 14–18. Записать?» → «на сколько часов?» →
бронь (вариант A) → 🔮 Работа/задача + алерт Кай. «расскажи про СДВГ Кай» →
кураторский профиль (см. §9).

**Доступ бота к данным — узкий:** движок занятости читает дедлайны задач +
`works.scheduled_at` как **непрозрачные блоки** (заголовки — только в ЛС
admin'а); кураторский профиль; `booking_*`. `sessions`/`clients`/`memories`/
тексты трактовок — **не трогает ни в каком режиме**. Никакого «owner full
mode» — ломаться и утекать особо нечему.

### 9. Кураторский профиль для друзей

Отдельный store с текстом, который Кай пишет сама: пара абзацев про неё, её
формулировка про СДВГ (как она хочет чтобы друзья понимали), что можно/нельзя
спрашивать. Бот в группах зачитывает **только его** — не `memories`, не
приватную память.

**Реализовано (#242):** флаг `shared` на задачах (`tasks.shared`, NL
"расшарь задачу X") и на списках (`nexus_lists.shared`, колонка есть, NL-флоу
для списков пока нет). `core/shared_items.py:shared_items_summary()`
подмешивается в system-промпт Зари только для `role=friend` — бот сам решает
вплести ли и как, не зачитывает списком.

### 7. Данные — новый `core`-домен `booking` (PG)

`booking_availability` (окна по контексту) · `booking_meeting_type` ·
`booking` (заявка/бронь, `context`, `hours`, `status`, `nexus_task_id?`,
`arcana_work_id?`) · `booking_block`. Плюс `works.scheduled_at` +
статус «Запланировано». Всё под owner-ключом `user_id`.

### 8. Frontend — `booking.heylark.dev`, три лица по роли

**v1 (построено):** single-file `miniapp/backend/booking_web/index.html` —
vanilla JS, без сборки, бэкенд отдаёт его на host `booking.*`; API на том же
origin, cookie `hl_session` работает. Тема свет/тьма (Nexus/Arcana),
localStorage. Месяц-грид ‹›, клик по дню → слоты + бронь. Кай перерисует
дизайн в Claude Design — v1 нужна чтобы оценить логику; отдельная Vite-сборка
позже, если понадобится.

- **guest** — сетка занятости без деталей, стандартный цвет; бронь → «войти
  через Telegram».
- **friend** (`hl_session` + `grants`) — цветовая раскраска: дела Nexus
  светло-зелёным (☀️), Аркана лиловым (🌒), **без заголовков**; реальные
  слоты + бронь напрямую (вариант A).
- **admin** (Кай) — левая панель управления (Заявки / Окна / Типы / Блоки),
  в сетке видны заголовки событий. Отдельной вкладки в Mini App не делаем.

Данные: `GET /api/booking/calendar` (role-aware: guest `{start,end}` / friend
`+kind` / admin `+title`), `/me`, `/tip` (`core/booking/tips.py` —
детерминированный совет из окон доступности, без LLM).

### 10. Топология по репозиториям (план ниже — пересмотрен при реализации)

Изначальный план разбивал эпик на три репо с HTTP между ботом и API (см.
зачёркнутое ниже). **По факту вышло проще**, и это финальная топология:

- **`nexus-arcana`** — всё доменное И прикладное: `core/booking/` (движок
  free/busy + `linkage.py`), `booking_*` таблицы + `works.scheduled_at`,
  Booking API (`miniapp/backend/routes/booking.py`, роут-группа в общем
  FastAPI на `:8000`), фронт `booking_web/` (3 лица, single-file), **и сам
  бот `zarya/`** (не тонкий клиент API — отдельный процесс/контейнер в этом
  же compose, зовёт `core/booking/` **напрямую** по общей БД, без HTTP-хопа
  вообще). «Занято» = задачи Кай + работы Арканы → всё живёт там, где эта
  схема; вынос = хрупкая кросс-репо связь.
- **`heylark-infra`** — только платформа: Caddy vhost `booking.heylark.dev`
  → `nexus-bot:8000`, `grants`/`people` (`app='booking'`), `login.heylark.dev`
  SSO. Никакой доменной логики.

Почему не по плану: бот и API решают одну и ту же задачу над одной БД —
HTTP между ними добавлял бы сеть, сериализацию и отдельный auth-контур без
выгоды (нет внешнего потребителя API, кроме этого бота и веба). Прямой вызов
`core/booking/` из бота = один источник правды, один деплой, без
service-токена между процессами (`BOOKING_SERVICE_TOKEN` остался только для
внешних вызовов вроде feed/admin, не для бот↔API).

~~**Как бот зовёт API:** по внутренней Docker-сети `nexus-arcana_default`
(`external: true`, как `heylark-login`) — `http://nexus-bot:8000/api/booking/*`,
без публичного хопа. Auth бот↔API — service-to-service: общий секрет
`BOOKING_SERVICE_TOKEN` (в обоих `.env`, `openssl rand -hex 32`). Бот сам
резолвит роль через `grants` (он в той же `auth` БД) и передаёт
`tg_id`/`role`/`context`; API доверяет service-токену. Веб-юзеры — сессией
(`hl_session`), бот — токеном и «ручается» за своих.~~

**Секреты (сводка):** `BOOKING_BOT_TOKEN` (BotFather, в `.env` бота) ·
`BOOKING_SERVICE_TOKEN` (общий, бот↔API) · `AUTH_DATABASE_URL` (строка
подключения к `grants`) · `.ics`-фид токен — **не секрет**, HMAC от
существующего `SESSION_SECRET`.

## Альтернативы отвергнуты

- **Booking-скилл внутри Nexus** (первоначальное решение, §6) — `WhitelistMiddleware`
  режет сообщения друзей до хендлеров; чат-бот в дружеской группе светит
  приватную поверхность Nexus и требует постоянной дырки в гейте. Отдельный
  `@heylark_booking_bot` изолирован.
- **Lark как общий ассистент с полным доступом к Nexus+Arcana** — для ЛС у Кай
  уже есть Nexus/Arcana; второй полный ассистент не нужен, а полный доступ у
  бота в дружеских группах = вектор утечки. Bot ограничен букингом + консьержем.
- **Учить `l4rk_sys_bot` (Lark Sys Bot) отвечать** — он send-only (логи
  инфры), нет dispatcher/polling; навесить polling = фактически новый бот.
- **Google Calendar как источник истины (#15)** — Кай на Google не сидит,
  переходит на Apple. Внутренние события + `.ics`-фид наружу проще и не
  завязаны на чужой API. #15 снят с зависимости.
- **friend-link токены / инвайты** — оверинжиниринг под ~5 друзей; ручной
  `/grant`.
- **Порт `core_identity.role` в RBAC** — ADR-0012 уже отверг; `grants` —
  общий для всех heylark-продуктов sharing-слой.
- **Отдельные сабдомены под друзей и Аркану** — Кай одна; один вход с
  развилкой достаточно.
- **Отдельная `booking`→`session` конверсия для Арканы** — у Кай сначала
  консультация/расклад (= 🔮 Работа), фактический `session`/`ritual`
  логируется после; Работа — естественный forward-looking контейнер.

## Последствия

- Новый `core.booking` домен + миграции (применяет Кай).
- `works` получает `scheduled_at` — затрагивает Arcana works spec.
- Зависимость от heylark-infra: `grants` (`app='booking'`), `people`,
  `login.heylark.dev` — часть работы в другом репо.
- Новый Caddy vhost + Vite-entry — деплой-конфиг.
- Новый бот-процесс `@heylark_booking_bot` (свой токен, свой `Dispatcher`/
  middleware, в том же compose).
- #15 (Google Calendar) — закрыть/переформулировать в `.ics`-фид.
- Приватность: реальные `tg_id`, имена друзей/клиентов, содержимое встреч —
  только в БД; фикстуры — placeholder'ы; public — только free/busy.
- **Будущее:** предоплата эзо-слота. Клиент оплачивает бронь заранее →
  деньги идут в **финансовую модель Арканы** (не в 💰 Финансы): при
  проведении `Оплачено`/`Сумма`/`Источник` на 🔮 Работе → `session`/`ritual`,
  feed в P&L `core/cash_register.py` — ровно как Аркана считает деньги
  сейчас. `context='arcana'` + платёжный провайдер; отдельная фаза.

## Verify against code

- `alembic/versions/d4e5f6a7b8c9_booking_tables.py` — `booking_*` + `works.scheduled_at` ✅
- `core/booking/busy.py` — агрегатор занятости (задачи-дедлайны 1ч, works,
  брони, блоки) ✅
- `core/booking/slots.py` — окна доступности → свободные слоты ✅
- `core/booking/ics.py` + `miniapp/backend/routes/booking.py` — `.ics`-фид
  (`GET /feed/<token>.ics`, HMAC от `SESSION_SECRET`) + `GET /api/booking/feed-url` ✅
- `core/auth_grants.py` — `booking_role(tg_id)` (admin/friend/guest), `grants`;
  `/grant` в `nexus_bot.py` ✅
- `miniapp/backend/routes/booking.py` — `booking_principal` seam (session /
  service-token), `GET /api/booking/public` `/slots`, `POST /api/booking/book`
  (friends auto-confirm / arcana pending, 409 conflict), `/requests` +
  `/requests/<id>/confirm|decline` (owner), `/booking/request/<token>` ✅
- `core/booking/repo.py` — `booking` CRUD (+ `set_booking_link`); `core/bot_notify.py:notify_booking_log`
  → topic `TG_LOG_THREAD_BOOKING` ✅
- `core/booking/linkage.py` — **B6**: confirmed `friends` → `tasks` (deadline =
  старт, без reminder — напоминалки у Зари), confirmed `arcana` → `works`
  (status `scheduled` 🗓 Запланировано, `scheduled_at` = старт); id пишется
  назад в `booking.nexus_task_id/arcana_work_id`; `unlink_booking` архивирует
  при отмене. `f7c8b9a0d1e2` сидит `work_status('scheduled')` ✅
- `zarya/` (Zarya, `@heylark_booking_bot`) — `RoleMiddleware` (роль из `grants`),
  `/start` `/slots` + NL, слот→длительность→бронь, `/requests` + `/bookings`
  пульт Кай; `zarya/Dockerfile` (узкий контекст) + compose-сервис `zarya`.
  Прямые вызовы `core/booking/` (шарит БД), не HTTP. ✅
- `zarya/scheduler.py` — **Z2**: booking-напоминания T-24ч + T-2ч, ЛС инициатору
  И всем owner tg_id, in-memory APScheduler, `restore_on_startup()` +
  5-мин sweep (подхватывает брони с веба, [[nexus-reminder-scheduler-fragility]]) ✅
- Двусторонние уведомления (**Z1**): `_notify_owner` → ЛС всем owner tg_id +
  строка в топик 1182; approve/decline/авто-бронь → ЛС инициатору с кнопкой
  «❌ Отменить»; `z:cx:<id>` — отмена любой из сторон → ЛС другой стороне +
  снять напоминания + `unlink_booking` ✅
- `miniapp/backend/routes/booking.py` — `/book` + `/requests/<id>/confirm` →
  `link_booking`; `GET /booking/mine`; `POST /booking/<token>/cancel`;
  фронт `booking_web` — блок «мои брони» + кнопка отмены ✅
- `booking.heylark.dev` — Caddy vhost (heylark-infra) → `nexus-bot:8000`; фронт
  `booking_web` (3 лица, тема Nexus/Arcana) — оба в nexus-arcana (пересмотр §10) ✅
- **Групповой UX** — Group Privacy выключен, обкатан живьём: `_bot_addressed()`
  гейтит и regex-, и Haiku-путь (только DM / `@mention` / reply на Зарю —
  не реагирует на непомеченную болтовню); `on_membership_changed` выкидывает
  Зарю из группы, если добавил не `config.allowed_ids` ✅
- **`friends`: любое свободное время** (пересмотр §3, #233) — `_free_slots_any_time`,
  13:00–23:00 МСК, без `booking_availability`; `arcana` остался windows-based ✅
- **Разовые окна** (#232) — `booking_availability.specific_date`, CHECK XOR с `weekday` ✅
- **Длительность** (#241) — `tasks.duration_min`/`works.duration_min`,
  NL "поставь длительность 2 часа для X" (Nexus only, см. `docs/specs/TASKS.md`) ✅
- **Shared-флаг** (#242) — `tasks.shared`/`nexus_lists.shared`,
  `core/shared_items.py:shared_items_summary` в контексте Зари для друзей (см. §9) ✅
- **Повод обязателен** (#239) — `note` = характер встречи ("шашлыки в
  Токсово"), не имя заявителя; имя уходит в заметку созданной задачи ✅
- **Имя заявителя из `people`** (#238) — веб-бронь резолвит через
  `core.auth_grants.get_display_name`, не `tg:<id>`; владельца личным
  сообщением через Зарю, не только в лог-топик ✅
- **История + перенос** (#220/B7) — `GET /booking/history`,
  `POST /booking/{id}/reschedule` (конфликт-чек исключая саму бронь,
  `update_linked_time` синкает дедлайн задачи/`scheduled_at` работы) ✅
- эпик #23 — полный дизайн, фазы, разведка planerka/Яндекс; открыт для
  дальнейших правок по букингу (Кай, #220)

### Bot-approval login (#23 follow-up)

Telegram Login Widget на десктопе/в приватной вкладке без живой сессии
`web.telegram.org` всегда падал в резервный веб-flow с номером телефона
(поведение Telegram, не наше — воспроизведено на `login.heylark.dev`).
Заменён на подтверждение через Зарю: `POST /auth/tg/start` минтит токен →
deep link в `@heylark_booking_bot` → Заря спрашивает «это ты?» → тап →
страница поллит `GET /auth/tg/poll` и получает `hl_session`-куку.

Общая таблица `login_tokens` в БД `auth` (та же, что `grants`/`invites`/
`people`) — как и `grants`, читается/пишется НАПРЯМУЮ с обеих сторон, без
HTTP между репами:
- `heylark-infra` (`heylark_auth/tg_auth.py`): `init_auth_schema` +
  `create_login_token`/`poll_login_token`/`mark_login_token_used`; роуты
  `login/login_service.py` `/auth/tg/start` + `/auth/tg/poll`; кнопка
  «Войти через Зарю» заменила виджет в `heylark_auth/templates/login.html` ✅
- `nexus-arcana` (`core/login_tokens.py`): `get_pending`/`approve`/`deny`;
  `zarya/handlers.py` — `/start login_<token>` deep-link + `z:login_ok:`/
  `z:login_no:` callbacks ✅
