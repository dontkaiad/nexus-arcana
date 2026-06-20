#!/bin/bash
# v2 — с паузами и ретраями против rate-limit Wikimedia

set -e

PROJECT_DIR="${PROJECT_DIR:-/Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS}"
DECKS_DIR="$PROJECT_DIR/miniapp/frontend/public/decks"
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

mkdir -p "$DECKS_DIR/rider-waite"

# download with retries: start with 1s delay, double on each 429 up to 60s
fetch() {
  local url="$1"
  local out="$2"
  if [ -f "$out" ] && [ -s "$out" ]; then
    echo "  ⏭  $(basename "$out") already exists"
    return
  fi

  local delay=1
  local attempt=1
  while [ $attempt -le 6 ]; do
    code=$(curl -sL -A "$UA" -o "$out" -w "%{http_code}" "$url")
    if [ "$code" = "200" ] && [ -s "$out" ]; then
      size=$(stat -f%z "$out" 2>/dev/null || stat -c%s "$out" 2>/dev/null)
      echo "  ✅ $(basename "$out") — ${size} bytes (попытка $attempt)"
      # always sleep 1s between successful requests too — not to piss off wiki
      sleep 1
      return
    fi
    if [ "$code" = "429" ]; then
      echo "  ⏳ $(basename "$out") — 429, ждём ${delay}s (попытка $attempt)"
      rm -f "$out"
      sleep $delay
      delay=$((delay * 2))
      [ $delay -gt 60 ] && delay=60
      attempt=$((attempt + 1))
      continue
    fi
    echo "  ❌ $(basename "$out") — HTTP $code (сдаюсь)"
    rm -f "$out"
    return
  done
  echo "  💀 $(basename "$out") — 6 попыток, всё 429"
  rm -f "$out"
}

echo ""
echo "═══ RIDER-WAITE (с паузами + ретраями) ═══"
cd "$DECKS_DIR/rider-waite"

BASE="https://upload.wikimedia.org/wikipedia/commons"

CARDS=(
  "00_fool.jpg|9/90/RWS_Tarot_00_Fool.jpg"
  "01_magician.jpg|d/de/RWS_Tarot_01_Magician.jpg"
  "02_high_priestess.jpg|8/88/RWS_Tarot_02_High_Priestess.jpg"
  "03_empress.jpg|d/d2/RWS_Tarot_03_Empress.jpg"
  "04_emperor.jpg|c/c3/RWS_Tarot_04_Emperor.jpg"
  "05_hierophant.jpg|8/8d/RWS_Tarot_05_Hierophant.jpg"
  "06_lovers.jpg|3/3a/TheLovers.jpg"
  "07_chariot.jpg|9/9b/RWS_Tarot_07_Chariot.jpg"
  "08_strength.jpg|f/f5/RWS_Tarot_08_Strength.jpg"
  "09_hermit.jpg|4/4d/RWS_Tarot_09_Hermit.jpg"
  "10_wheel_of_fortune.jpg|3/3c/RWS_Tarot_10_Wheel_of_Fortune.jpg"
  "11_justice.jpg|e/e0/RWS_Tarot_11_Justice.jpg"
  "12_hanged_man.jpg|2/2b/RWS_Tarot_12_Hanged_Man.jpg"
  "13_death.jpg|d/d7/RWS_Tarot_13_Death.jpg"
  "14_temperance.jpg|f/f8/RWS_Tarot_14_Temperance.jpg"
  "15_devil.jpg|5/55/RWS_Tarot_15_Devil.jpg"
  "16_tower.jpg|5/53/RWS_Tarot_16_Tower.jpg"
  "17_star.jpg|d/db/RWS_Tarot_17_Star.jpg"
  "18_moon.jpg|7/7f/RWS_Tarot_18_Moon.jpg"
  "19_sun.jpg|1/17/RWS_Tarot_19_Sun.jpg"
  "20_judgement.jpg|d/dd/RWS_Tarot_20_Judgement.jpg"
  "21_world.jpg|f/ff/RWS_Tarot_21_World.jpg"
  "wa_01_ace.jpg|1/11/Wands01.jpg"
  "wa_02.jpg|0/0f/Wands02.jpg"
  "wa_03.jpg|f/ff/Wands03.jpg"
  "wa_04.jpg|a/a4/Wands04.jpg"
  "wa_05.jpg|9/9d/Wands05.jpg"
  "wa_06.jpg|3/3b/Wands06.jpg"
  "wa_07.jpg|e/e4/Wands07.jpg"
  "wa_08.jpg|6/6b/Wands08.jpg"
  "wa_09.jpg|4/4d/Tarot_Nine_of_Wands.jpg"
  "wa_10.jpg|0/0b/Wands10.jpg"
  "wa_11_page.jpg|6/6a/Wands11.jpg"
  "wa_12_knight.jpg|1/16/Wands12.jpg"
  "wa_13_queen.jpg|0/0d/Wands13.jpg"
  "wa_14_king.jpg|c/ce/Wands14.jpg"
  "cu_01_ace.jpg|3/36/Cups01.jpg"
  "cu_02.jpg|f/f8/Cups02.jpg"
  "cu_03.jpg|7/7a/Cups03.jpg"
  "cu_04.jpg|3/35/Cups04.jpg"
  "cu_05.jpg|d/d7/Cups05.jpg"
  "cu_06.jpg|1/17/Cups06.jpg"
  "cu_07.jpg|a/ae/Cups07.jpg"
  "cu_08.jpg|6/60/Cups08.jpg"
  "cu_09.jpg|2/24/Cups09.jpg"
  "cu_10.jpg|8/84/Cups10.jpg"
  "cu_11_page.jpg|a/ad/Cups11.jpg"
  "cu_12_knight.jpg|f/fa/Cups12.jpg"
  "cu_13_queen.jpg|6/62/Cups13.jpg"
  "cu_14_king.jpg|0/04/Cups14.jpg"
  "sw_01_ace.jpg|1/1a/Swords01.jpg"
  "sw_02.jpg|9/9e/Swords02.jpg"
  "sw_03.jpg|0/02/Swords03.jpg"
  "sw_04.jpg|b/bf/Swords04.jpg"
  "sw_05.jpg|2/23/Swords05.jpg"
  "sw_06.jpg|2/29/Swords06.jpg"
  "sw_07.jpg|3/34/Swords07.jpg"
  "sw_08.jpg|a/a7/Swords08.jpg"
  "sw_09.jpg|2/2f/Swords09.jpg"
  "sw_10.jpg|d/d4/Swords10.jpg"
  "sw_11_page.jpg|4/4c/Swords11.jpg"
  "sw_12_knight.jpg|b/b0/Swords12.jpg"
  "sw_13_queen.jpg|d/d4/Swords13.jpg"
  "sw_14_king.jpg|3/33/Swords14.jpg"
  "pe_01_ace.jpg|f/fd/Pents01.jpg"
  "pe_02.jpg|9/9f/Pents02.jpg"
  "pe_03.jpg|4/42/Pents03.jpg"
  "pe_04.jpg|3/35/Pents04.jpg"
  "pe_05.jpg|9/96/Pents05.jpg"
  "pe_06.jpg|a/a6/Pents06.jpg"
  "pe_07.jpg|6/6a/Pents07.jpg"
  "pe_08.jpg|4/49/Pents08.jpg"
  "pe_09.jpg|f/f0/Pents09.jpg"
  "pe_10.jpg|4/42/Pents10.jpg"
  "pe_11_page.jpg|e/ec/Pents11.jpg"
  "pe_12_knight.jpg|d/d5/Pents12.jpg"
  "pe_13_queen.jpg|8/88/Pents13.jpg"
  "pe_14_king.jpg|1/1c/Pents14.jpg"
)

for entry in "${CARDS[@]}"; do
  out="${entry%%|*}"
  path="${entry##*|}"
  fetch "$BASE/$path" "$out"
done

echo ""
echo "═══ РЕЗУЛЬТАТ ═══"
count=$(ls "$DECKS_DIR/rider-waite"/*.jpg 2>/dev/null | wc -l | tr -d ' ')
echo "Rider-Waite: ${count}/78"
if [ "$count" -lt 78 ]; then
  echo "Не всё — запусти ещё раз, уже скачанные пропустятся"
fi
