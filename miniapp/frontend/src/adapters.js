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

const RU_MONTH_NAME_NOM = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

// "2026-04" → "Апрель 2026"
export function formatMonth(iso) {
  if (!iso || typeof iso !== 'string') return ''
  const [y, m] = iso.split('-').map(Number)
  if (!y || !m) return iso
  return `${RU_MONTH_NAME_NOM[m - 1]} ${y}`
}

// Универсальный formatDate — mode: "short" | "full" | "auto" (default full)
export function formatDate(iso, mode = 'full', now = new Date()) {
  const dt = parseIsoDate(iso)
  if (!dt) return iso || ''
  const day = dt.getDate()
  const year = dt.getFullYear()
  const sameYear = year === now.getFullYear()
  if (mode === 'short') {
    const mo = RU_MONTH_SHORT[dt.getMonth()]
    return sameYear ? `${day} ${mo}` : `${day} ${mo} ${year}`
  }
  const mo = RU_MONTH_FULL[dt.getMonth()]
  return sameYear ? `${day} ${mo}` : `${day} ${mo} ${year}`
}

// "every_2d" → "каждые 2 дня", "every_1d" → "каждый день", "every_7d" → "каждую неделю"
// Также маппит русские значения Notion "Ежедневно"/"Еженедельно"/... в короткую форму.
const _RU_REPEAT_MAP = {
  'ежедневно': 'каждый день',
  'ежедневный': 'каждый день',
  'еженедельно': 'каждую неделю',
  'еженедельный': 'каждую неделю',
  'ежемесячно': 'каждый месяц',
  'ежемесячный': 'каждый месяц',
  'ежегодно': 'каждый год',
}
export function formatRepeat(raw) {
  if (!raw) return ''
  const s = String(raw).trim()
  const m = s.match(/^every_(\d+)d$/)
  if (m) {
    const n = parseInt(m[1], 10)
    if (n === 1) return 'каждый день'
    if (n === 7) return 'каждую неделю'
    if (n === 14) return 'каждые 2 недели'
    const mod = n % 10
    const tail = mod === 1 && n !== 11 ? 'день' : 'дня'
    return `каждые ${n} ${tail}`
  }
  const ruKey = s.toLowerCase().replace(/^[^\p{L}]+/u, '')
  if (_RU_REPEAT_MAP[ruKey]) return _RU_REPEAT_MAP[ruKey]
  return s
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
  const todayIso = data.date || ''
  const todayTasksRaw = (data.tasks || []).filter(
    (x) => !x.date || !todayIso || x.date <= todayIso,
  )
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
    scheduled: (data.scheduled || []).map((x) => {
      // wave8.8: client-side safety — если бэкенд прислал "HH:MM|every_Nd"
      // (старый непарсенный формат), разбираем здесь.
      // wave8.44: парсенный из time интервал — источник истины (там реальный
      // every_Nd), колонка «Повтор» в Notion может быть stale ("Ежедневно").
      let rawTime = x.time || ''
      let repeat = x.repeat || ''
      if (rawTime && rawTime.includes('|')) {
        const [t, r] = rawTime.split('|', 2)
        rawTime = (t || '').trim()
        if (r) repeat = r.trim()
      }
      return {
        id: x.id,
        title: x.title,
        cat: x.cat || '',
        prio: x.prio || '⚪',
        time: rawTime,
        rem: formatReminder(x.reminder_min),
        rpt: repeat ? `🔄 ${formatRepeat(repeat)}` : undefined,
        streak: x.streak || 0,
      }
    }),
    tasks: todayTasksRaw.map((x) => ({
      id: x.id,
      title: x.title,
      cat: x.cat || '',
      prio: x.prio || '⚪',
      date: formatDate(x.date, 'full'),
      // wave8.9: показываем И дедлайн, И напоминалку, если они есть
      deadlineTime: x.deadline_time || null,
      reminderTime: x.reminder_time || null,
    })),
    noDate: (data.no_date || []).map((x) => ({
      id: x.id,
      title: x.title,
      cat: x.cat || '',
      prio: x.prio || '⚪',
      daysSinceCreated: x.days_since_created ?? null,
    })),
    adhdTip: data.adhd_tip || '',
  }
}

// Полное имя категории из объекта cat_from_notion {emoji, name, full} / строки
export function catFull(cat) {
  if (!cat) return ''
  if (typeof cat === 'string') return cat
  return cat.full || cat.name || cat.emoji || ''
}

export function catEmoji(cat) {
  if (!cat) return ''
  if (typeof cat === 'string') {
    const m = cat.match(/^\S+/)
    return m ? m[0] : ''
  }
  return cat.emoji || ''
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

// ── /api/tasks → MOCK.tasks shape ─────────────────────────────────────────

export function adaptTasks(data) {
  if (!data) return []
  return (data.tasks || []).map((t) => {
    // wave8.44: deadline_time может приходить как "HH:MM|every_Nd" — парсим,
    // и предпочитаем машинный every_Nd колоночному «Повтор» (часто stale).
    let time = t.deadline_time || null
    let repeat = t.repeat || ''
    if (time && time.includes('|')) {
      const [tm, r] = time.split('|', 2)
      time = (tm || '').trim() || null
      if (r) repeat = r.trim()
    }
    return {
      id: t.id,
      title: t.title || '',
      cat: catFull(t.cat),
      prio: t.prio || '⚪',
      status: t.status || 'active',
      date: t.deadline ? formatDate(t.deadline, 'full') : null,
      time,
      rpt: repeat ? `🔄 ${formatRepeat(repeat)}` : undefined,
      streak: t.streak || 0,
    }
  })
}

// ── /api/finance — 4 view-adapt ───────────────────────────────────────────

export function adaptFinanceToday(data) {
  if (!data) return { total: 0, items: [], budget: null }
  return {
    total: data.total ?? 0,
    items: (data.items || []).map((x) => ({
      id: x.id,
      desc: x.desc || '',
      cat: catFull(x.cat),
      amt: x.amt ?? 0,
    })),
    budget: data.budget || null,
  }
}

export function adaptFinanceMonth(data) {
  if (!data) return { inc: 0, exp: 0, balance: 0, cats: [] }
  return {
    inc: data.income ?? 0,
    exp: data.expense ?? 0,
    balance: data.balance ?? 0,
    cats: (data.by_category || []).map((c) => ({
      name: catFull(c.cat),
      raw: c.cat,  // wave6: исходная структура категории для drill-down
      spent: c.spent ?? 0,
      limit: c.limit ?? null,
      pct: c.pct ?? null,
    })),
  }
}

export function adaptFinanceLimits(data) {
  if (!data) return []
  return (data.categories || []).map((c) => ({
    name: catFull(c.cat),
    raw: c.cat,  // wave7.5.2: для drill-down
    spent: c.spent ?? 0,
    limit: c.limit ?? 0,
    pct: c.pct ?? 0,
    zone: c.zone || 'green',
  }))
}

export function adaptFinanceGoals(data) {
  if (!data) return { debts: [], goals: [], closedDebts: [], closedGoals: [] }
  return {
    debts: (data.debts || []).map((d) => ({
      n: d.name,
      total: d.total ?? 0,
      left: d.left ?? d.total ?? 0,
      by: d.by || '—',
      note: d.note || '',
      monthly: d.monthly_payment ?? 0,
      schedule: d.schedule || [],
      ends: d.ends || null,
      takenAt: d.taken_at || null,
    })),
    goals: (data.goals || []).map((g) => ({
      n: g.name,
      t: g.target ?? 0,
      s: g.saved ?? 0,
      after: g.after || '—',
      monthly: g.monthly ?? 0,
    })),
    closedDebts: (data.closed_debts || []).map((d) => ({
      n: d.name,
      total: d.total ?? 0,
      monthly: d.monthly_payment ?? 0,
      note: d.note || '',
      closedAt: d.closed_at || null,
    })),
    closedGoals: (data.closed_goals || []).map((g) => ({
      n: g.name,
      t: g.target ?? 0,
      monthly: g.monthly ?? 0,
      closedAt: g.closed_at || null,
    })),
  }
}

// ── /api/lists → MOCK.lists.{buy,check,inv} shape ─────────────────────────

export function adaptLists(data) {
  if (!data) return []
  const type = data.type
  return (data.items || []).map((x) => {
    const base = {
      id: x.id,
      name: x.name || '',
      cat: catEmoji(x.cat),
      catFull: catFull(x.cat),
      group: x.group || '',
      done: !!x.done,
    }
    if (type === 'inv') {
      return {
        ...base,
        qty: x.qty ?? 1,
        exp: x.expires ? formatShortDate(x.expires) : undefined,
      }
    }
    if (type === 'check' && x.parent) {
      // wave8.47: метаданные родительской задачи для шапки группы
      let time = x.parent.deadline_time || null
      let repeat = x.parent.repeat || ''
      if (time && time.includes('|')) {
        const [tm, r] = time.split('|', 2)
        time = (tm || '').trim() || null
        if (r) repeat = r.trim()
      }
      base.parent = {
        cat: catFull(x.parent.cat),
        prio: x.parent.prio || '⚪',
        date: x.parent.deadline ? formatDate(x.parent.deadline, 'full') : null,
        time,
        rpt: repeat ? `🔄 ${formatRepeat(repeat)}` : undefined,
        reminderMin: x.parent.reminder_min ?? null,
      }
    }
    return base
  })
}

// ── /api/memory → {items, categories} ─────────────────────────────────────

export function adaptMemory(data) {
  if (!data) return { items: [], categories: [] }
  return {
    items: (data.items || []).map((m) => ({
      id: m.id,
      text: m.text || '',
      cat: m.cat || '—',
    })),
    categories: data.categories || [],
  }
}

// ── /api/memory/adhd → {profile, records} ─────────────────────────────────

export function adaptAdhd(data) {
  const empty = { profile: '', groups: { patterns: [], strategies: [], triggers: [], specifics: [] } }
  if (!data) return empty
  const g = data.groups || {}
  return {
    profile: data.profile || '',
    groups: {
      patterns: g.patterns || [],
      strategies: g.strategies || [],
      triggers: g.triggers || [],
      specifics: g.specifics || [],
    },
  }
}

// ── /api/calendar → {[day]: ["emoji title", ...]} ─────────────────────────

export function adaptCalendar(data) {
  if (!data) return { tasksByDay: {}, month: null }
  const days = data.days || {}
  const tasksByDay = {}
  for (const [day, bucket] of Object.entries(days)) {
    const tasks = bucket.tasks || []
    if (tasks.length === 0) continue
    tasksByDay[Number(day)] = tasks.map((t) => ({
      id: t.id,
      title: t.title || '',
      cat: t.cat || '',
      prio: t.prio || '⚪',
      time: t.time || null,
      rpt: t.repeat ? `🔄 ${formatRepeat(t.repeat)}` : undefined,
    }))
  }
  return { tasksByDay, month: data.month || null, days }
}

// ── /api/arcana/sessions → MOCK.sessions brief list ───────────────────────

export function adaptSessionBrief(x) {
  return {
    id: x.id,
    q: x.question || '',
    client: x.client || 'Личный',
    client_id: x.client_id,
    area: Array.isArray(x.area) ? x.area.join(', ') : (x.area || ''),
    deck: x.deck || '',
    type: x.type || '',
    date: x.date ? formatDate(x.date, 'full') : '',
    time: x.date_time || '',
    cards: (x.cards_brief || []).map((name) => ({ name, pos: null, icon: null })),
    done: x.done || '⏳ Не проверено',
    price: x.price ?? 0,
    paid: x.paid ?? 0,
  }
}

export function adaptSessions(data) {
  if (!data) return []
  return (data.sessions || []).map(adaptSessionBrief)
}

// Полная детализация для SessionDetail
export function adaptSessionDetail(data) {
  if (!data) return null
  return {
    id: data.id,
    q: data.question || '',
    client: data.client || 'Личный',
    client_id: data.client_id,
    area: Array.isArray(data.area) ? data.area.join(', ') : (data.area || ''),
    deck: data.deck || '',
    deckId: data.deck_id || 'rider-waite',
    type: data.type || '',
    date: data.date ? formatDate(data.date) : '',
    // wave6.4: canonical cards with file/en/ru
    cards: (data.cards || []).map((c) => ({
      en: c.en || c.raw || '',
      ru: c.ru || '',
      file: c.file || null,
      matched: !!c.matched,
      pos: c.pos || '',
      icon: c.icon || '',
    })),
    bottomCard: data.bottom_card || null,
    bottom: data.bottom || null,
    interp: data.interpretation || '',
    done: data.done || '⏳ Не проверено',
    price: data.price ?? 0,
    paid: data.paid ?? 0,
    photo_url: data.photo_url || null,
  }
}

// ── /api/arcana/clients → MOCK.clients brief list ─────────────────────────

export function adaptClients(data) {
  if (!data) return { clients: [], total_debt: 0 }
  return {
    total_debt: data.total_debt ?? 0,
    total: data.total ?? 0,
    clients: (data.clients || []).map((c) => ({
      id: c.id,
      name: c.name || '',
      initial: c.initial || (c.name || '?')[0],
      status: c.status || '🟢',
      sessions: c.sessions_count ?? 0,
      rituals: c.rituals_count ?? 0,
      debt: c.debt ?? 0,
      total: c.total_paid ?? 0,
      self: (c.status || '').includes('Я') || (c.name || '').toLowerCase() === 'кай',
    })),
  }
}

export function adaptClientDossier(data) {
  if (!data) return null
  return {
    id: data.id,
    name: data.name || '',
    initial: data.initial || (data.name || '?')[0],
    status: data.status || '🟢',
    contact: data.contact || '—',
    since: data.since ? formatShortDate(data.since) : '—',
    request: data.request || '—',
    notes: data.notes || '',
    photo_url: data.photo_url || null,
    sessions: data.stats?.sessions ?? 0,
    rituals: data.stats?.rituals ?? 0,
    debt: data.stats?.debt ?? 0,
    total: data.stats?.total_paid ?? 0,
    self: (data.name || '').toLowerCase() === 'кай',
    history: (data.history || []).map((h) => ({
      id: h.id,
      date: h.date ? formatShortDate(h.date) : '—',
      type: h.kind === 'session' ? '🃏' : '🕯️',
      desc: h.desc || '',
      amount: h.amount ?? 0,
      paid: !!h.paid,
    })),
  }
}

// ── /api/arcana/rituals → MOCK.rituals brief list ─────────────────────────

export function adaptRituals(data) {
  if (!data) return []
  return (data.rituals || []).map((r) => ({
    id: r.id,
    name: r.name || '',
    goal: r.goal || '—',
    place: r.place || '—',
    type: r.type || '🌟',
    date: r.date ? formatDate(r.date, 'full') : '',
    result: (r.result || '⏳').split(' ')[0],
    client: r.client || null,
    price: r.price ?? 0,
    paid: r.paid ?? 0,
  }))
}

export function adaptRitualDetail(data) {
  if (!data) return null
  return {
    id: data.id,
    name: data.name || '',
    goal: data.goal || '—',
    place: data.place || '—',
    type: data.type || '🌟',
    date: data.date ? formatDate(data.date, 'full') : '',
    result: (data.result || '⏳').split(' ')[0],
    client: data.client || null,
    question: data.question || '',
    price: data.price ?? 0,
    paid: data.paid ?? 0,
    supplies: (data.supplies || []).map((x) => ({
      name: x.name || '',
      qty: x.qty || '',
      price: x.price ?? 0,
    })),
    time: data.time_min ?? 0,
    offerings: data.offerings || '',
    powers: data.powers || '',
    structure: data.structure || [],
  }
}

// ── /api/arcana/grimoire → MOCK.grimoire brief list ───────────────────────

export function adaptGrimoire(data) {
  if (!data) return { items: [], categories: [] }
  return {
    items: (data.items || []).map((g) => ({
      id: g.id,
      name: g.name || '',
      cat: g.cat || '—',
      theme: g.theme || '📖',
    })),
    categories: data.categories || [],
  }
}

export function adaptGrimoireDetail(data) {
  if (!data) return null
  return {
    id: data.id,
    name: data.name || '',
    cat: data.cat || '—',
    themes: data.themes || [],
    content: data.content || '',
    source: data.source || '',
  }
}

// ── /api/arcana/stats → {pct, allVer, months, practice} ───────────────────

export function adaptArcanaStats(data) {
  if (!data) return null
  const pf = data.practice_finance?.current_month || {}
  return {
    pct: data.accuracy_overall ?? 0,
    allVer: data.verified_total ?? 0,
    months: (data.months || []).map((m) => ({
      name: m.name || '',
      total: m.total ?? 0,
      yes: m.yes ?? 0,
      partial: m.partial ?? 0,
      no: m.no ?? 0,
      pending: m.pending ?? 0,
      pct: m.pct ?? 0,
    })),
    practice: {
      inc: pf.income ?? 0,
      exp: pf.expense ?? 0,
      profit: pf.profit ?? 0,
    },
  }
}
