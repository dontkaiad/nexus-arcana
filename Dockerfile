# ── Stage 1: build frontend ───────────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY miniapp/frontend/package*.json ./
RUN npm ci
COPY miniapp/frontend/ .
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Собранный фронт из стейджа frontend (без node/node_modules в финальном образе)
COPY --from=frontend /frontend/dist /app/miniapp/frontend/dist

# Версия образа для старт-пинга в мониторинг. .git в образ НЕ копируется
# (.dockerignore), git внутри контейнера не работает — печём ВРЕМЯ СБОРКИ
# (+ короткий SHA коммита, если deploy передал --build-arg GIT_SHA). Слой
# пересобирается всегда, когда менялся код (его parent — `COPY . .`), → свежий
# деплой = свежий BUILD_STAMP. Старое время в мониторинге = контейнер не
# перечитал код. GIT_SHA пуст → стамп = только время сборки, без мусора.
ARG GIT_SHA=
RUN printf 'built %s%s' "$(date -u +%Y-%m-%dT%H:%MZ)" "${GIT_SHA:+ · $GIT_SHA}" > /app/BUILD_STAMP

CMD ["python", "-m", "nexus.nexus_bot"]
