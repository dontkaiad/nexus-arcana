import { enqueue, flushOutbox, isOfflineEligible, outboxCount } from './outbox'

const BASE = import.meta.env.VITE_API_BASE || ''

function getInitData() {
  if (typeof window !== 'undefined' && window.Telegram?.WebApp?.initData) {
    return window.Telegram.WebApp.initData
  }
  return import.meta.env.VITE_DEV_INIT_DATA || ''
}

// Бросается когда мутация не ушла из-за отсутствия сети, но легла в
// офлайн-очередь (#189). UI показывает «📴 сохранено локально», не «ошибка».
export class OfflineQueuedError extends Error {
  constructor(entry) {
    super('offline — записано локально, отправлю при связи')
    this.name = 'OfflineQueuedError'
    this.queued = true
    this.entry = entry
  }
}

// Ошибка от сервера (2xx не пришёл). permanent=true для 4xx (не ретраить).
class HttpError extends Error {
  constructor(status, statusText, bodyText) {
    super(`${status} ${statusText}${bodyText ? ` — ${bodyText.slice(0, 120)}` : ''}`)
    this.name = 'HttpError'
    this.status = status
    this.permanent = status >= 400 && status < 500 && status !== 429
  }
}

function isNetworkError(e) {
  // fetch() бросает TypeError при обрыве сети / DNS / CORS-preflight fail
  return e instanceof TypeError || /network|failed to fetch|load failed/i.test(e?.message || '')
}

async function _raw(method, path, { body, headers = {}, idempotencyKey } = {}) {
  const h = { 'X-Telegram-Init-Data': getInitData(), ...headers }
  if (body !== undefined) h['Content-Type'] = 'application/json'
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: h,
    body: body !== undefined ? JSON.stringify(body ?? {}) : undefined,
  })
  if (!r.ok) {
    // #222: браузерный доступ (core.heylark.dev) без Telegram initData и без
    // валидной hl_session-куки — не показываем голый JSON 401, а уводим на
    // единый SSO-логин. Внутри настоящего Telegram Mini App initData всегда
    // есть, там 401 (и тем более 403 — не тот tg_id) остаётся обычной ошибкой.
    if (r.status === 401 && !getInitData()) {
      window.location.href = 'https://login.heylark.dev/?next=' + encodeURIComponent(window.location.href)
      return new Promise(() => {}) // навигация уже пошла — не резолвим, чтобы UI не мигнул ошибкой
    }
    const text = await r.text().catch(() => '')
    throw new HttpError(r.status, r.statusText, text)
  }
  return r.json()
}

// Мутация с офлайн-очередью. offline или сетевая ошибка на eligible-пути →
// enqueue + OfflineQueuedError.
async function _mutate(method, path, body, opts = {}) {
  const offline =
    typeof navigator !== 'undefined' && navigator.onLine === false
  const eligible = isOfflineEligible(path)

  if (offline && eligible) {
    const entry = enqueue({ method, path, body, label: opts.label })
    throw new OfflineQueuedError(entry)
  }
  try {
    return await _raw(method, path, { body, idempotencyKey: opts.idempotencyKey })
  } catch (e) {
    if (isNetworkError(e) && eligible) {
      const entry = enqueue({ method, path, body, label: opts.label })
      throw new OfflineQueuedError(entry)
    }
    throw e
  }
}

// ── Публичный API ───────────────────────────────────────────────────────────

export async function apiGet(path) {
  return _raw('GET', path)
}

export async function apiPost(path, body, opts = {}) {
  return _mutate('POST', path, body, opts)
}

export async function apiPatch(path, body, opts = {}) {
  return _mutate('PATCH', path, body, opts)
}

export async function apiDelete(path, opts = {}) {
  return _mutate('DELETE', path, undefined, opts)
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

// ── Офлайн-очередь: досыл ──────────────────────────────────────────────────

async function _sendQueued(entry) {
  try {
    return await _raw(entry.method, entry.path, {
      body: entry.method === 'DELETE' ? undefined : entry.body,
      idempotencyKey: entry.idempotencyKey,
    })
  } catch (e) {
    if (e instanceof HttpError && e.permanent) {
      e.permanent = true
      throw e
    }
    // сетевая / 5xx / 429 — оставить в очереди
    throw e
  }
}

export async function flushOfflineQueue() {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    return { sent: 0, failed: 0, remaining: outboxCount(), offline: true }
  }
  return flushOutbox(_sendQueued)
}

export { outboxCount, isOfflineEligible } from './outbox'
