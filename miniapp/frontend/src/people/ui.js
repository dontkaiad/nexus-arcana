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
