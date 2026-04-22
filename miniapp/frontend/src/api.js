const BASE = import.meta.env.VITE_API_BASE || ''

function getInitData() {
  if (typeof window !== 'undefined' && window.Telegram?.WebApp?.initData) {
    return window.Telegram.WebApp.initData
  }
  return import.meta.env.VITE_DEV_INIT_DATA || ''
}

export async function apiGet(path) {
  const r = await fetch(`${BASE}${path}`, {
    headers: { 'X-Telegram-Init-Data': getInitData() },
  })
  if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new Error(`${r.status} ${r.statusText}${text ? ` — ${text.slice(0, 120)}` : ''}`)
  }
  return r.json()
}
