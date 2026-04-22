# Nexus × Arcana — Mini App (frontend)

Vite + React 18. Экранов 11 (прототип `miniapp_full_v7_2.jsx`), в wave 4a через
API подключены только **Мой день** (`NxDay`) и **Мой день Арканы** (`ArDay`).
Остальные 9 пока на MOCK — подключение в wave 4b.

## Dev

1. Запусти бота, чтобы поднялся FastAPI на `:8000`:
   ```bash
   cd ../../ && ./run.sh
   ```

2. Получи `initData` для dev (инструкция в `.env.example`):
   - Открой Mini App в Telegram → DevTools → консоль:
     `window.Telegram.WebApp.initData` → скопируй строку.

3. Положи в `.env.local`:
   ```
   VITE_DEV_INIT_DATA=<твоя_строка_на_24_часа>
   ```

4. Запусти:
   ```bash
   npm install
   npm run dev
   ```

5. Открой http://localhost:5173

## Архитектура

```
src/
  main.jsx           — точка входа
  App.jsx            — весь UI (скопирован из frontend-seed/, адаптирован точечно)
  api.js             — fetch-wrapper с X-Telegram-Init-Data заголовком
  hooks/useApi.js    — { data, loading, error, refetch }
  adapters.js        — API-ответы → форма которую ждут MOCK-компоненты
public/
  nexus.png, arcana.png — аватарки шапки
```

## Добавить новый экран к API (паттерн для wave 4b)

1. Написать `adaptXxx(data)` в `src/adapters.js`.
2. В соответствующем компоненте в `App.jsx`:
   ```jsx
   const { data, loading, error, refetch } = useApi('/api/xxx')
   if (loading) return <Empty s={s} text="Загружаю..." />
   if (error)   return <ErrorGlass .../>
   const x = adaptXxx(data)
   // дальше — существующий JSX
   ```

## Build

```bash
npm run build      # в dist/
npm run preview    # локальный статический сервер
```

## Dev через Telegram туннель

Для тестирования в Telegram Mini App через Cloudflare tunnel:

```bash
npm run dev:tunnel
```

Это запустит `vite build --watch` + `vite preview` одновременно — собирается prod-бандл и отдаётся как статика. Через туннель работает быстро (обычный `vite dev` виснет на стриминге большого `App.jsx`).

Туннель поднимается отдельно:

```bash
cloudflared tunnel --url http://localhost:5173 --protocol http2
```

URL туннеля настраивается в BotFather как menu button.
