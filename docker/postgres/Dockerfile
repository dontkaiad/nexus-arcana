# PostgreSQL 16 + pgvector — СВОЙ образ db (не официальный pgvector/pgvector:pg16).
#
# Зачем свой: официальный pgvector/pgvector:pg16 собран на Debian 12 (glibc 2.36),
# а прод-том nexus_arcana инициализирован под postgres:16 = Debian 13 (glibc 2.41).
# Свап на чужой glibc → collation version mismatch (text-индексы/уникальные на
# тексте могут промахиваться, нужен REINDEX). FROM postgres:16 держит glibc
# СИНХРОННЫМ с прод-томом → mismatch'а нет, REINDEX не нужен (см. RAG-миграцию
# Qdrant→pgvector). pgvector ставится из PGDG-репозитория (он уже в образе postgres).
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
