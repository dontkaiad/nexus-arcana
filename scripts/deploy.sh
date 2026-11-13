#!/usr/bin/env bash
set -euo pipefail
cd /home/kai/nexus-arcana
git pull origin main
GIT_SHA=$(git rev-parse --short HEAD)

# #222/#23: docker compose build ВСЕГДА заворачивает вывод в attestation
# manifest + manifest list, даже с build.provenance/sbom:false в
# docker-compose.yml (баг/ограничение compose v5, COMPOSE_BAKE=false тоже
# не спасает) -> новый image ID на КАЖДОЙ сборке даже при 100% кеше ->
# лишние рестарты контейнеров + задвоенные/затроенные пинги деплоя.
# Голый `docker buildx build --provenance=false --sbom=false --load`
# СТАБИЛЕН между одинаковыми сборками — собираем образы напрямую, а
# `docker compose up -d` (без --build) пересоздаёт только то, что реально
# изменилось.
docker buildx build --provenance=false --sbom=false --load \
  -f docker/postgres/Dockerfile -t nexus-arcana-db:latest docker/postgres
docker buildx build --provenance=false --sbom=false --load \
  --build-arg GIT_SHA="$GIT_SHA" -t nexus-arcana-bots:latest .
# SHA последнего коммита, трогавшего входы сборки Зари (zarya/build-inputs.txt), а не HEAD:
# иначе образ и контейнер Зари менялись бы на каждом деплое.
ZSHA=$(git log -1 --format=%h -- $(cat zarya/build-inputs.txt))
docker buildx build --provenance=false --sbom=false --load \
  --build-arg GIT_SHA="$ZSHA" -f zarya/Dockerfile -t zarya-bot:latest .

docker compose run --rm nexus alembic upgrade head
docker compose up -d
echo "deploy done: $(date -u +%FT%TZ)"
