// Блок «последний раз» над списком котов: «лотки · сегодня 16:13». Чистые функции без React и без обращений к DOM (проверяются в tests/test_care_block_ui.py).
// Время всегда по Москве (UTC+3, без перехода на летнее время), независимо от пояса телефона.
const MSK_MS = 3 * 3600 * 1000
const DAY_MS = 24 * 3600 * 1000
const pad = (n) => String(n).padStart(2, '0')

function mskParts(date) {
  const d = new Date(date.getTime() + MSK_MS)
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, day: d.getUTCDate(), hh: d.getUTCHours(), mm: d.getUTCMinutes(), dayIndex: Math.floor((date.getTime() + MSK_MS) / DAY_MS) }
}

// ISO-время (UTC) → «сегодня 16:13» | «вчера 18:50» | «05.11 09:00» (московские сутки); нет или не время → null.
export function formatLastDone(iso, now = new Date()) {
  if (typeof iso !== 'string' || !iso) return null
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return null
  const a = mskParts(t)
  const b = mskParts(now)
  const clock = `${pad(a.hh)}:${pad(a.mm)}`
  if (a.dayIndex === b.dayIndex) return `сегодня ${clock}`
  if (a.dayIndex === b.dayIndex - 1) return `вчера ${clock}`
  return `${pad(a.day)}.${pad(a.m)}${a.y !== b.y ? `.${a.y}` : ''} ${clock}`
}

// Строка блока: «лотки · сегодня 16:13»; нет выполнений → «лотки · ещё не отмечено». Нет метки → null (строка не рисуется).
export function careLine(entry, now = new Date()) {
  const label = entry && typeof entry.label === 'string' ? entry.label.trim() : ''
  if (!label) return null
  return `${label} · ${formatLastDone(entry.last_done_at, now) || 'ещё не отмечено'}`
}

export function careLines(care, now = new Date()) {
  return (Array.isArray(care) ? care : []).map((e) => careLine(e, now)).filter(Boolean)
}
