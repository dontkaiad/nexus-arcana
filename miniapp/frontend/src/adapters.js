// miniapp/frontend/src/adapters.js
// Превращают ответ API в форму, которую ожидают MOCK-компоненты.
// Один экран = одна adapt*-функция. Новые добавляем сюда же.

const RU_MONTH_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'май', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
]
const RU_MONTH_FULL = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

function parseIsoDate(iso) {
  if (!iso || typeof iso !== 'string') return null
  // "2026-04-27" → Date at local midnight
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return null
  const [, y, mo, d] = m
  return new Date(+y, +mo - 1, +d)
}

// "27 апр" если текущий год, иначе "15 мая 2027"
export function formatShortDate(iso, now = new Date()) {
  const dt = parseIsoDate(iso)
  if (!dt) return iso || ''
  const day = dt.getDate()
  const mo = RU_MONTH_SHORT[dt.getMonth()]
  const year = dt.getFullYear()
  return year === now.getFullYear() ? `${day} ${mo}` : `${day} ${mo} ${year}`
}

// "22 апреля, вт" (weekday уже приходит из API)
export function formatFullDate(iso, weekday) {
  const dt = parseIsoDate(iso)
  if (!dt) return iso || ''
  const day = dt.getDate()
  const mo = RU_MONTH_FULL[dt.getMonth()]
  return weekday ? `${day} ${mo}, ${weekday}` : `${day} ${mo}`
}

// 60 → "за 1 ч", 30 → "за 30 мин", 90 → "за 1 ч 30 мин", 120 → "за 2 ч"
export function formatReminder(minutes) {
  if (!minutes || minutes <= 0) return null
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `за ${m} мин`
  if (m === 0) return `за ${h} ч`
  return `за ${h} ч ${m} мин`
}

// ── /api/today → MOCK.today shape ──────────────────────────────────────────

export function adaptToday(data) {
  if (!data) return null
  return {
    date: formatFullDate(data.date, data.weekday),
    streak: data.streak?.current ?? 0,
    budgetDay: data.budget?.day ?? 0,
    spentDay: data.budget?.spent_today ?? 0,
    overdue: (data.overdue || []).map((o) => ({
      id: o.id,
      title: o.title,
      cat: o.cat || '',
      prio: o.prio || '⚪',
      days: o.days_ago,
    })),
    scheduled: (data.scheduled || []).map((x) => ({
      id: x.id,
      title: x.title,
      cat: x.cat || '',
      prio: x.prio || '⚪',
      time: x.time || '',
      rem: formatReminder(x.reminder_min),
      rpt: x.repeat ? `🔄 ${x.repeat}` : undefined,
      streak: x.streak || 0,
    })),
    tasks: (data.tasks || []).map((x) => ({
      id: x.id,
      title: x.title,
      cat: x.cat || '',
      prio: x.prio || '⚪',
      date: formatShortDate(x.date),
    })),
    adhdTip: data.adhd_tip || '',
  }
}

// ── /api/arcana/today → shape для ArDay ────────────────────────────────────

export function adaptArcanaToday(data) {
  if (!data) return null
  return {
    date: formatFullDate(data.date, data.weekday),
    moon: data.moon || { glyph: '🌑', name: '—', days: 0, illum: 0 },
    sessionsToday: (data.sessions_today || []).map((x) => ({
      id: x.id,
      time: x.time || '',
      client: x.client,
      client_id: x.client_id,
      self_client: x.self_client,
      type: x.type || '',
      area: Array.isArray(x.area) ? x.area.join(', ') : (x.area || ''),
      status: x.status || 'upcoming',
    })),
    worksToday: (data.works_today || []).map((w) => ({
      id: w.id,
      title: w.title,
      cat: (w.cat && (w.cat.full || w.cat.name)) || '',
      prio: w.prio || '⚪',
    })),
    unchecked30d: data.unchecked_30d ?? 0,
    accuracy: data.accuracy ?? 0,
    monthBlock: {
      label: data.month_stats?.label || '',
      inc: data.month_stats?.income ?? 0,
      supplies: data.month_stats?.supplies ?? 0,
      accuracy: data.month_stats?.accuracy ?? 0,
      sessions: data.month_stats?.sessions ?? 0,
    },
  }
}
