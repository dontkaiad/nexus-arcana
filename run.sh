#!/bin/bash
cd "$(dirname "$0")"
source .venv312/bin/activate

# Прод (python:3.12-slim) и тесты идут на 3.12 — не стартуем молча на другом Python.
PY_VERSION="$(python --version 2>&1)"
case "$PY_VERSION" in
    "Python 3.12."*) ;;
    *)
        echo "❌ Нужен Python 3.12, а в .venv312 сейчас: ${PY_VERSION:-python не найден}."
        echo "   Пересобери: uv venv --python 3.12 .venv312 && uv pip install --python .venv312/bin/python -r requirements-dev.txt"
        exit 1
        ;;
esac

PYTHON="$(which python3)"

echo "Starting ☀️ Nexus + 🌒 Arcana with auto-reload..."

auto_pull() {
    while true; do
        sleep 30
        OLD_HEAD=$(git rev-parse HEAD)
        git pull origin main --ff-only 2>&1 | while read -r line; do
            echo "[auto-pull] $line"
        done
        NEW_HEAD=$(git rev-parse HEAD)
        if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
            echo "[auto-pull] ✅ Обновлено: $(git log --oneline ${OLD_HEAD}..${NEW_HEAD} | head -5)"
        fi
    done
}
auto_pull &
PULL_PID=$!

$PYTHON -m watchfiles "$PYTHON -m nexus.nexus_bot" nexus/ core/ miniapp/ &
NEXUS_PID=$!

$PYTHON -m watchfiles "$PYTHON -m arcana.bot" arcana/ core/ &
ARCANA_PID=$!

echo "☀️ Nexus PID: $NEXUS_PID"
echo "🌒 Arcana PID: $ARCANA_PID"
echo "🔄 Auto-pull PID: $PULL_PID"

trap "kill $NEXUS_PID $ARCANA_PID $PULL_PID 2>/dev/null; echo 'Bots stopped.'" SIGINT SIGTERM

wait