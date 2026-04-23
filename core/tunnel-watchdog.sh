#!/bin/bash
# Tunnel watchdog: keeps cloudflared alive + auto-updates BotFather menu URL
# Usage: ./tunnel-watchdog.sh
#
# Requires: cloudflared, curl, jq
# Reads: NEXUS_BOT_TOKEN from project _env

set -e

PROJECT_DIR="/Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS"
ENV_FILE="$PROJECT_DIR/.env"

# Load bot token
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: _env not found at $ENV_FILE"
  exit 1
fi
BOT_TOKEN=$(grep -E '^NEXUS_BOT_TOKEN=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: NEXUS_BOT_TOKEN not found in _env"
  exit 1
fi

LOG_FILE="/tmp/cloudflared-watchdog.log"
URL_FILE="/tmp/cloudflared-current-url"
LAST_URL=""

trap 'echo "Watchdog stopped."; pkill -f "cloudflared tunnel" 2>/dev/null; exit 0' INT TERM

update_botfather_menu() {
  local url="$1"
  echo "[$(date '+%H:%M:%S')] Updating BotFather menu URL to: $url"
  response=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setChatMenuButton" \
    -H "Content-Type: application/json" \
    -d "{
      \"menu_button\": {
        \"type\": \"web_app\",
        \"text\": \"Приложение\",
        \"web_app\": {\"url\": \"${url}\"}
      }
    }")
  if echo "$response" | grep -q '"ok":true'; then
    echo "[$(date '+%H:%M:%S')] ✅ Menu URL updated"
  else
    echo "[$(date '+%H:%M:%S')] ❌ Failed: $response"
  fi
}

start_tunnel() {
  echo "[$(date '+%H:%M:%S')] Starting cloudflared..."
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  sleep 1

  cloudflared tunnel --url http://localhost:5173 --protocol http2 > "$LOG_FILE" 2>&1 &
  TUNNEL_PID=$!

  # Wait for URL to appear in log (max 30 sec)
  for i in {1..30}; do
    sleep 1
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -1)
    if [ -n "$URL" ]; then
      echo "[$(date '+%H:%M:%S')] ✅ Tunnel up: $URL"
      echo "$URL" > "$URL_FILE"
      if [ "$URL" != "$LAST_URL" ]; then
        update_botfather_menu "$URL"
        LAST_URL="$URL"
      fi
      return 0
    fi
  done
  echo "[$(date '+%H:%M:%S')] ❌ Tunnel failed to start in 30s"
  return 1
}

check_tunnel_alive() {
  local url="$1"
  status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  # 200=ok, 401=ok (api active), 530=tunnel dead
  if [ "$status" = "530" ] || [ "$status" = "000" ]; then
    return 1
  fi
  return 0
}

echo "🚀 Tunnel Watchdog started"
echo "   Bot token: ${BOT_TOKEN:0:10}..."
echo "   Logs: $LOG_FILE"
echo "   Press Ctrl+C to stop"
echo ""

start_tunnel || exit 1

while true; do
  sleep 30
  CURRENT_URL=$(cat "$URL_FILE" 2>/dev/null || echo "")

  if [ -z "$CURRENT_URL" ] || ! check_tunnel_alive "$CURRENT_URL"; then
    echo "[$(date '+%H:%M:%S')] ⚠️  Tunnel dead, restarting..."
    start_tunnel || sleep 5
  else
    echo "[$(date '+%H:%M:%S')] ✅ Tunnel alive: $CURRENT_URL"
  fi
done
