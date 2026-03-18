#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

PYTHON="$(which python3)"

echo "Starting ☀️ Nexus + 🌒 Arcana with auto-reload..."

$PYTHON -m watchfiles "$PYTHON -m nexus.nexus_bot" nexus/ core/ &
NEXUS_PID=$!

$PYTHON -m watchfiles "$PYTHON -m arcana.bot" arcana/ core/ &
ARCANA_PID=$!

echo "☀️ Nexus PID: $NEXUS_PID"
echo "🌒 Arcana PID: $ARCANA_PID"

trap "kill $NEXUS_PID $ARCANA_PID; echo 'Bots stopped.'" SIGINT SIGTERM

wait