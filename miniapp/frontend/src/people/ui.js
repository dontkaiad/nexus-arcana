// Общие мелочи раздела «Люди». Масштаб шрифта = FS из App.jsx (держать в синхроне).
export const FS = 1.2
export const fs = (n) => Math.round(n * FS)

// Порядок и подписи групп фактов: important первым (как отдаёт API).
export const TYPE_META = {
  important: { icon: '⚠️', label: 'ВАЖНО' },
  likes: { icon: '❤️', label: 'ЛЮБИТ' },
  dislikes: { icon: '💔', label: 'НЕ ЛЮБИТ' },
  other: { icon: '📝', label: 'ДРУГОЕ' },
}
export const TYPE_CHOICES = ['important', 'likes', 'dislikes', 'other']

export const typeMeta = (t) => TYPE_META[t] || { icon: '📝', label: String(t || 'другое').toUpperCase() }

// API: 'YYYY-MM-DD' | '--MM-DD' (год неизвестен) | null  →  '14.03' / '14.03.1990' / '--.--'
export function fmtBirthday(iso) {
  const m = /^(\d{4}|-)-(\d{2})-(\d{2})$/.exec(iso || '')
  if (!m) return '--.--'
  return m[1] === '-' ? `${m[3]}.${m[2]}` : `${m[3]}.${m[2]}.${m[1]}`
}

export const sourcesOf = (p) => [p.vk_id && 'VK', p.tg_id && 'TG'].filter(Boolean)

export const pendingOf = (p) => (p?.counts?.pending) || 0

export function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}

export function haptic(kind = 'light') {
  try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(kind) } catch { /* вне Telegram */ }
}

// Статус → цвет по токенам темы s.
export const statusColor = (s, status) =>
  status === 'confirmed' ? s.good : status === 'rejected' ? s.red : s.amber

export const STATUS_LABEL = { pending: 'на проверке', confirmed: 'подтверждён', rejected: 'отклонён' }

// 'YYYY-MM-DD' → 'дд.мм.гггг'
export function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '')
  return m ? `${m[3]}.${m[2]}.${m[1]}` : ''
}

export const PLATFORM = { vk: 'VK', tg: 'TG' }

// Под цитатой: «VK · 12.03.2021» (+ «· написала Кай», если цитата — её реплика)
export function provenanceLine(f) {
  const parts = []
  const pf = PLATFORM[f.platform]
  if (pf) parts.push(pf)
  const d = fmtDate(f.message_date)
  if (d) parts.push(d)
  if (parts.length === 0) return ''
  if (f.quote_author === 'kai') parts.push('написала Кай')
  return parts.join(' · ')
}

// Метка упоминаний факта: «×7 · последнее 2023»
export function mentionLabel(f) {
  const n = f.mention_count || 1
  const y = (f.last_mention || '').slice(0, 4)
  return `×${n}${y ? ` · последнее ${y}` : ''}`
}

// Ссылки на источник человека: «TG · @username» / «TG · id123» и «VK · id456»
export function sourceLinks(p) {
  const out = []
  if (p.vk_id) out.push({ key: 'vk', label: `VK · id${p.vk_id}`, href: `https://vk.com/id${p.vk_id}` })
  if (p.tg_id || p.tg_username) {
    out.push({
      key: 'tg',
      label: p.tg_username ? `TG · @${p.tg_username}` : `TG · id${p.tg_id}`,
      href: p.tg_username ? `https://t.me/${p.tg_username}` : `tg://user?id=${p.tg_id}`,
    })
  }
  return out
}

export function openLink(e, href) {
  e.stopPropagation()
  e.preventDefault()
  try {
    if (window.Telegram?.WebApp?.openLink && /^https?:/.test(href)) { window.Telegram.WebApp.openLink(href); return }
  } catch { /* ignore */ }
  window.open(href, '_blank', 'noopener')
}
