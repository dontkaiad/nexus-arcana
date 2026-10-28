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

// «Удалённый аккаунт · TG/VK»: страницы больше нет — ссылку на источник не показываем
// [а-яё]* вместо \w: в JS \w без флага u не знает кириллицу
export const isDeletedAccount = (p) => /^\s*удал[её]нн[а-яё]*\s+аккаунт/i.test(p?.name || '')

// Ссылки на источник человека (список, разбор): основной TG и основной VK + «+N» остальных аккаунтов.
// У «Удалённого аккаунта» ссылок нет.
export function sourceLinks(p) {
  const out = []
  if (isDeletedAccount(p)) return out
  const accs = p.accounts && p.accounts.length ? p.accounts : legacyAccounts(p)
  const prim = (plat) => accs.find((a) => a.platform === plat && a.is_primary) || accs.find((a) => a.platform === plat)
  const vk = prim('vk'), tg = prim('tg')
  if (vk) out.push({ key: 'vk', label: `VK · id${vk.external_id}`, href: `https://vk.com/id${vk.external_id}` })
  if (tg) {
    out.push({
      key: 'tg',
      label: tg.username ? `TG · @${tg.username}` : `TG · id${tg.external_id}`,
      href: tg.username ? `https://t.me/${tg.username}` : `tg://user?id=${tg.external_id}`,
    })
  }
  const extra = accs.length - out.length
  if (extra > 0) out.push({ key: 'more', label: `+${extra}`, href: null })
  return out
}

// старые ответы без `accounts` (tg_id/vk_id в самой записи)
function legacyAccounts(p) {
  const out = []
  if (p.tg_id) out.push({ platform: 'tg', external_id: p.tg_id, username: p.tg_username || null, is_primary: true })
  if (p.vk_id) out.push({ platform: 'vk', external_id: p.vk_id, username: null, is_primary: true })
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

// ── аккаунты человека в карточке ────────────────────────────────────────────
//   VK                      → vk.com/id<id>           (внешняя ссылка)
//   TG с username           → t.me/<username>         (Telegram.WebApp.openTelegramLink)
//   TG только с id          → тап копирует id         (профиль по одному id не открыть)
//   «Удалённый аккаунт», «Я» → без ссылок (аккаунты показываем текстом)
export function accountAction(a) {
  if (a.platform === 'vk') return { kind: 'vk', label: `VK · id${a.external_id}`, url: `https://vk.com/id${a.external_id}` }
  if (a.username) return { kind: 'tg', label: `TG · @${a.username}`, url: `https://t.me/${a.username}` }
  return { kind: 'copy', label: `TG · id${a.external_id}`, text: String(a.external_id) }
}

export const accountsOf = (p) => (p?.accounts && p.accounts.length ? p.accounts : legacyAccounts(p || {}))

export function linksAllowed(p) {
  return !(!p || p.is_self || isDeletedAccount(p))
}

export function openTelegramLink(url) {
  const wa = window.Telegram?.WebApp
  try {
    if (wa?.openTelegramLink) { wa.openTelegramLink(url); return }
  } catch { /* ниже запасной путь */ }
  window.open(url, '_blank', 'noopener')
}

export function openExternal(url) {
  const wa = window.Telegram?.WebApp
  try {
    if (wa?.openLink) { wa.openLink(url); return }
  } catch { /* ниже запасной путь */ }
  window.open(url, '_blank', 'noopener')
}

export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true }
  } catch { /* запасной путь для WebView без clipboard API */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;opacity:0;left:-9999px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch { return false }
}
