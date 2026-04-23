#!/usr/bin/env python3
"""
Generate valid initData for local dev and write to .env.local
Usage: python3 gen-dev-initdata.py
"""
import hmac, hashlib, json, time, urllib.parse
from pathlib import Path

PROJECT_DIR = Path("/Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS")
YOUR_TG_ID = 67686090
YOUR_FIRST_NAME = "Kai"
YOUR_USERNAME = "dontkaiad"

# Read bot token
env_file = PROJECT_DIR / ".env"
bot_token = None
for line in env_file.read_text().splitlines():
    if line.startswith("NEXUS_BOT_TOKEN="):
        bot_token = line.split("=", 1)[1].strip().strip('"').strip("'")
        break

if not bot_token:
    raise SystemExit("NEXUS_BOT_TOKEN not found in .env")

# Build user JSON (exactly as Telegram does)
user_obj = {
    "id": YOUR_TG_ID,
    "first_name": YOUR_FIRST_NAME,
    "username": YOUR_USERNAME,
    "language_code": "ru",
    "allows_write_to_pm": True,
}
user_json = json.dumps(user_obj, separators=(',', ':'), ensure_ascii=False)

auth_date = str(int(time.time()))
query_id = f"AAH{YOUR_TG_ID}00000000"

# Fields to sign (excluding hash)
fields = {
    "query_id": query_id,
    "user": user_json,
    "auth_date": auth_date,
}

# Telegram's signing procedure:
# 1. secret_key = HMAC-SHA256("WebAppData", bot_token).digest()
# 2. data_check_string = sorted lines of "key=value" joined by \n
# 3. hash = HMAC-SHA256(secret_key, data_check_string).hexdigest()

secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
hash_hex = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

# Build initData query string (URL-encoded)
init_data_parts = []
for k in sorted(fields.keys()):
    init_data_parts.append(f"{k}={urllib.parse.quote(fields[k], safe='')}")
init_data_parts.append(f"hash={hash_hex}")
init_data = "&".join(init_data_parts)

# Write to .env.local
env_local = PROJECT_DIR / "miniapp" / "frontend" / ".env.local"
env_local.write_text(f"VITE_DEV_INIT_DATA={init_data}\n")

print(f"✅ Generated initData ({len(init_data)} chars)")
print(f"✅ Written to: {env_local}")
print(f"")
print(f"Auth date: {auth_date} (valid for 24 hours)")
print(f"User ID:   {YOUR_TG_ID}")
print(f"")
print(f"Next steps:")
print(f"  1. Restart vite:  cd miniapp/frontend && npm run dev")
print(f"  2. Open:          http://localhost:5173")
