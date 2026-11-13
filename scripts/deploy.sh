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

# Pre-migration backup (ADR-0014). Every failure below stops the deploy before
# alembic and before `docker compose up -d`: set -e plus explicit checks, no
# pipe or redirect hides an exit code.
umask 077
BACKUP_DIR="$HOME/backups"
BACKUP_KEEP=7
# ~/backups is shared with cron dumps and manual snapshots: an existing
# directory keeps its mode and content, only a directory created here gets 700.
if [ ! -d "$BACKUP_DIR" ]; then
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
fi

DB_BYTES=$(docker exec nexus-arcana-db psql -U kai -d nexus_arcana -Atc "SELECT pg_database_size(current_database())")
FREE_BYTES=$(df --output=avail -B1 "$BACKUP_DIR" | tail -n 1 | tr -d ' ')
NEED_BYTES=$(( DB_BYTES + 268435456 ))
if [ "$FREE_BYTES" -lt "$NEED_BYTES" ]; then
  echo "backup aborted: free $FREE_BYTES bytes, need $NEED_BYTES" >&2
  exit 1
fi

BACKUP_TS=$(date +%F_%H%M%S)
BACKUP_TMP=$(mktemp "$BACKUP_DIR/.predeploy_nexus_arcana_${BACKUP_TS}.XXXXXX")
trap 'rm -f "$BACKUP_TMP"' EXIT
if ! docker exec nexus-arcana-db pg_dump -U kai -d nexus_arcana -Fc > "$BACKUP_TMP"; then
  echo "backup aborted: pg_dump failed" >&2
  exit 1
fi
if [ ! -s "$BACKUP_TMP" ]; then
  echo "backup aborted: dump is empty" >&2
  exit 1
fi
if ! BACKUP_ENTRIES=$(docker exec -i nexus-arcana-db pg_restore -l < "$BACKUP_TMP" | grep -c '^[0-9][0-9]*;'); then
  BACKUP_ENTRIES=0
fi
if [ "$BACKUP_ENTRIES" -lt 1 ]; then
  echo "backup aborted: pg_restore -l shows no entries" >&2
  exit 1
fi
BACKUP_FILE="$BACKUP_DIR/predeploy_nexus_arcana_${BACKUP_TS}.dump"
chmod 600 "$BACKUP_TMP"
mv "$BACKUP_TMP" "$BACKUP_FILE"
trap - EXIT
echo "backup ok: $BACKUP_FILE ($BACKUP_ENTRIES entries)"
# Rotation touches ONLY predeploy_nexus_arcana_*.dump (cron nexus_arcana_*, pre_*, auth_*, santa_* stay).
ls -1t "$BACKUP_DIR"/predeploy_nexus_arcana_*.dump | tail -n +$(( BACKUP_KEEP + 1 )) | xargs -r rm -f --

docker compose run --rm nexus alembic upgrade head
docker compose up -d
echo "deploy done: $(date -u +%FT%TZ)"
