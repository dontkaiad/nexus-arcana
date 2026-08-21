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

// SSE-поток (#191): парсит "data: {...}\n\n" построчно и допечатывает через
// onDelta по мере прихода. Не используем native EventSource — он не умеет
// выставлять кастомные заголовки, а авторизация Mini App идёт через
// X-Telegram-Init-Data (см. getInitData), не через cookie. Возвращает
// финальное "done"-событие ({summary, cached, ...}) или null, если поток
// оборвался без него (ошибка сети/сервера на середине).
export async function apiStream(path, onDelta) {
  const r = await fetch(`${BASE}${path}`, {
    headers: { 'X-Telegram-Init-Data': getInitData() },
  })
  if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new Error(`${r.status} ${r.statusText}${text ? ` — ${text.slice(0, 120)}` : ''}`)
  }
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let finalEvent = null
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx).trim()
      buf = buf.slice(idx + 2)
      if (!block.startsWith('data: ')) continue
      const evt = JSON.parse(block.slice('data: '.length))
      if (evt.error) {
        throw new Error(evt.error)
      } else if (evt.done) {
        finalEvent = evt
      } else if (evt.delta !== undefined) {
        onDelta(evt.delta)
      }
    }
  }
  return finalEvent
}

export async function apiPost(path, body, opts = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': getInitData(),
  }
  if (opts.idempotencyKey) {
    headers['Idempotency-Key'] = opts.idempotencyKey
  }
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body || {}),
  })
  if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new Error(`${r.status} ${r.statusText}${text ? ` — ${text.slice(0, 120)}` : ''}`)
  }
  return r.json()
}
