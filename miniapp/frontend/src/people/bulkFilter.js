// Фильтры массового разбора фактов карточки «Я» (без LLM). Чистые функции: работают по уже загруженной карточке.
// Факт: { id, type, review_status, mention_count, last_mention, message_date, created_at, ... }

export const TYPES = ['important', 'likes', 'dislikes', 'other']
export const MENTION_BUCKETS = ['1', '2', '3+']
export const STATUSES = ['pending', 'confirmed', 'rejected']

export const flatten = (groups) => (groups || []).flatMap((g) => (g.items || []).map((it) => ({ ...it, type: g.type })))

export const mentionBucket = (n) => ((n || 1) >= 3 ? '3+' : String(n || 1))

// Год последнего упоминания: last_mention → дата сообщения → дата создания записи.
export function yearOf(f) {
  const d = f.last_mention || f.message_date || (f.created_at || '').slice(0, 10)
  const m = /^(\d{4})/.exec(d || '')
  return m ? m[1] : '—'
}

export const EMPTY_FILTER = { status: 'pending', type: 'all', mentions: 'all', year: 'all' }

export function matches(f, flt, skip) {
  if (skip !== 'status' && flt.status !== 'all' && f.review_status !== flt.status) return false
  if (skip !== 'type' && flt.type !== 'all' && f.type !== flt.type) return false
  if (skip !== 'mentions' && flt.mentions !== 'all' && mentionBucket(f.mention_count) !== flt.mentions) return false
  if (skip !== 'year' && flt.year !== 'all' && yearOf(f) !== flt.year) return false
  return true
}

export const applyFilter = (facts, flt) => facts.filter((f) => matches(f, flt))

// Сколько фактов у каждой опции ПРИ остальных выбранных фильтрах (как фасеты в магазине).
export function facetCounts(facts, flt, key, options) {
  const out = {}
  for (const o of options) out[o] = facts.filter((f) => matches(f, { ...flt, [key]: o }, null)).length
  out.all = facts.filter((f) => matches(f, { ...flt, [key]: 'all' }, null)).length
  return out
}

export const yearsOf = (facts) => [...new Set(facts.map(yearOf))].sort().reverse()

// Список фактов → группы по типу в порядке важности (как отдаёт API); последние упоминания сверху.
export function regroup(facts) {
  const by = {}
  for (const f of facts) (by[f.type] = by[f.type] || []).push(f)
  const order = [...TYPES.filter((t) => by[t]), ...Object.keys(by).filter((t) => !TYPES.includes(t)).sort()]
  return order.map((t) => ({
    type: t,
    items: by[t].slice().sort((a, b) => (b.last_mention || b.message_date || '').localeCompare(a.last_mention || a.message_date || '')),
  }))
}

// ── Мультивыбор фактов (карточка человека и «Я») ─────────────────────────────
export const idsOf = (groups) => (groups || []).flatMap((g) => (g.items || []).map((i) => i.id))

export function toggleId(selected, id) {
  const n = new Set(selected)
  n.has(id) ? n.delete(id) : n.add(id)
  return n
}

// Выбор живёт внутри текущего видимого набора: всё, чего не видно, из выбора выпадает.
export const clampSelection = (selected, visibleIds) => new Set([...selected].filter((id) => visibleIds.includes(id)))

// Группы по категории «Я» (порядок задаёт `order`, неизвестные — в конце по алфавиту); внутри — свежие упоминания сверху.
export function regroupByCategory(facts, order) {
  const by = {}
  for (const f of facts) (by[f.category || '—'] = by[f.category || '—'] || []).push(f)
  const names = [...order.filter((c) => by[c]), ...Object.keys(by).filter((c) => !order.includes(c)).sort()]
  return names.map((c) => ({
    type: c,
    items: by[c].slice().sort((a, b) => (b.last_mention || b.message_date || '').localeCompare(a.last_mention || a.message_date || '')),
  }))
}
