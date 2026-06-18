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

CMD ["python", "-m", "nexus.nexus_bot"]
