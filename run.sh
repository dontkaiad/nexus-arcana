#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

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

$PYTHON -m watchfiles "$PYTHON -m nexus.nexus_bot" nexus/ core/ &
NEXUS_PID=$!

$PYTHON -m watchfiles "$PYTHON -m arcana.bot" arcana/ core/ &
ARCANA_PID=$!

echo "☀️ Nexus PID: $NEXUS_PID"
echo "🌒 Arcana PID: $ARCANA_PID"
echo "🔄 Auto-pull PID: $PULL_PID"

trap "kill $NEXUS_PID $ARCANA_PID $PULL_PID 2>/dev/null; echo 'Bots stopped.'" SIGINT SIGTERM

wait