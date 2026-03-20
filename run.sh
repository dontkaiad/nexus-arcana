#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

PYTHON="$(which python3)"

echo "Starting ☀️ Nexus + 🌒 Arcana with auto-reload..."

auto_pull() {
    while true; do
        sleep 30
        RESULT=$(git pull origin main --quiet 2>&1)
        if echo "$RESULT" | grep -q "Already up to date"; then
            :
        else
            echo "[auto-pull] Изменения подтянуты: $RESULT"
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