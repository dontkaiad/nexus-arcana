# booking.heylark.dev — бриф для редизайна

Отдай Claude Design **`index.html`** + этот файл.

`index.html` — весь фронт в одном файле (HTML + inline `<style>` + inline
`<script>`, vanilla JS, без сборки). Бэкенд отдаёт его как есть на host
`booking.*`. Можно менять всё визуальное и раскладку **при сохранении вызовов
`/api/booking/*` и ролевой логики ниже**.

## Что это

Публичный календарь Кай + запись. Три роли, определяются сервером
(`GET /api/booking/me` → `{role}`):

| роль | что видит | что может |
|---|---|---|
| **guest** (не залогинен) | месяц-сетку с блоками занятости, **один нейтральный цвет**, без деталей | смотреть; для записи → кнопка «Войти через Telegram» на `https://login.heylark.dev/?next=<текущий url>` |
| **friend** (залогинен, есть грант) | занятость с цветом по типу: **☀️ Nexus — зелёный**, **🌒 Arcana — лиловый**, брони — бирюзовый. **Заголовки НЕ показываются** | выбрать день → слот → указать часы → бронь (подтверждается сразу) |
| **admin** (Кай) | то же + **заголовки событий**; слева — панель управления | всё + управление окнами/типами/блоками/заявками |

Контекст записи: `friends` (встречи) или `arcana` (расклады/ритуалы) —
переключатель в левой панели у non-admin.

## Раскладка (текущая, можно менять)

- **левая панель на всю высоту** (sticky, свой скролл): фото Кай (сейчас
  SVG-заглушка в `AVA`, подменить на реальное фото), имя, роль, краткий бриф
  (`BIO`), детерминированный совет (`GET /api/booking/tip`), легенда цветов.
  У **admin** вместо этого — 4 всегда-раскрытые секции: `📥 Заявки`,
  `🗓 Когда я свободна`, `☕ Типы встреч`, `🚫 Когда я недоступна`, каждая
  со списком + компактной формой добавления.
- **центр**: месяц-сетка `‹ месяц год ›` + «сегодня». Клетка дня: число,
  цветные точки по типам событий, счётчик свободных слотов. Дни недели с
  окном доступности слегка подсвечены (только admin).
- **справа** (sticky, появляется при клике на день): события дня + свободные
  слоты кнопками + «Записаться».

Тема: **светлая = Nexus (мягкая зелень)**, **тёмная = Arcana (глубокая синь)**,
переключатель, сохраняется в `localStorage['z-th']`. Референс UX: Google
Calendar / Яндекс.Календарь / planerka.app. Не агрессивно, спокойно.

## API (всё под `/api/booking`, cookie `hl_session` шлётся автоматически)

| метод | путь | роль | ответ / тело |
|---|---|---|---|
| GET | `/me` | все | `{role, tg_id}` |
| GET | `/tip?context=friends\|arcana` | все | `{tip: "..."}` |
| GET | `/calendar?days=90&back=14` | все | `{role, events:[{start,end, kind?, title?}]}` · `kind`∈`nexus\|arcana\|booking\|block\|busy` только friend/admin; `title` только admin |
| GET | `/slots?context=&days=75` | friend/admin (friends) · все (arcana) | `{slots:[{start,end}]}` |
| POST | `/book` | friend/admin (friends) · все (arcana) | тело `{context,start,hours}` → `{id,token,status}` (`confirmed`\|`pending`) |
| GET | `/request/<token>` | по токену | `{status,start,end,...}` — страница статуса заявки |
| GET | `/availability` `/meeting-types` `/blocks` | admin | `{windows\|types\|blocks:[...]}` |
| POST | `/availability` | admin | `{context,weekday(0-6),start_time"HH:MM",end_time,slot_minutes}` |
| POST | `/meeting-types` | admin | `{context,slug,title,duration_min}` |
| POST | `/blocks` | admin | `{start"ISO",end,reason}` |
| PATCH/DELETE | `/availability/<id>` `/meeting-types/<id>` `/blocks/<id>` | admin | — |
| GET | `/requests?status=open` | admin | `{requests:[{id,requester_name,start,status,note}]}` |
| POST | `/requests/<id>/confirm` `/decline` | admin | `{status}` |

Ошибки: 401/403 (нет прав), 409 (слот занят), 400 (плохое время).

## Фиксировано (не ломать)

- ролевая раскраска: guest нейтрально; friend цвет-по-типу без заголовка;
  admin с заголовком
- смотреть можно без авторизации, бронь — только с `hl_session`
- вызовы `/api/booking/*` с `credentials:"include"`
- host-detection не нужен — файл всегда открывается на `booking.heylark.dev`

## Свободно менять

вся вёрстка, типографика, цвета (в рамках Nexus-свет / Arcana-тьма),
компоненты, анимации, мобильная раскладка, можно перейти на React/фреймворк
(тогда нужен билд-шаг — сейчас его нет).
