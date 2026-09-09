// outbox.js — очередь на запись для офлайн-режима (#189, Phase 1).
//
// Сценарий: нет сети (самолёт / лес) → запись расхода/задачи/списка/памяти
// кладётся в localStorage-очередь → досылается при появлении связи.
//
// Безопасность повторной отправки: каждый элемент несёт стабильный
// Idempotency-Key, сгенерированный при постановке в очередь. Бэкенд
// (core/repos/idempotency_repo.py) дедуплицирует по (tg_id, key), так что
// двойная отправка при флаки-сети создаёт ровно одну запись.
//
// Только «на бегу» сущности: задачи, финансы, списки, память. Аркана
// (ритуалы/сеансы/клиенты) — отдельный разговор (см. issue #189), пока не
// ставятся в очередь.

const KEY = 'nx_outbox_v1'

// Пути, которые разрешено ставить в офлайн-очередь. Точное совпадение или
// префикс с '/' на конце шаблона (для path-параметров).
const OFFLINE_PATHS = [
  '/api/tasks',                 // create
  '/api/tasks/',                // done/reopen/cancel/postpone/edit
  '/api/finance',               // create
  '/api/finance/',              // debt / goal / cushion
  '/api/expenses',              // deprecated alias
  '/api/lists',                 // create
  '/api/lists/',                // done/checkout/delete/patch
  '/api/memory',                // create
  '/api/memory/',               // patch/delete
]

export function isOfflineEligible(path) {
  const p = (path || '').split('?')[0]
  return OFFLINE_PATHS.some((t) =>
    t.endsWith('/') ? p.startsWith(t) : p === t,
  )
}

function _read() {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function _write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* приватное окно / переполнение — тихо теряем, лучше чем краш */
  }
}

function _uuid() {
  try {
    return crypto.randomUUID()
  } catch {
    return `ob-${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
}

export function outboxCount() {
  return _read().length
}

export function outboxList() {
  return _read()
}

// Положить запись в очередь. Возвращает элемент с id + idempotencyKey.
export function enqueue({ method, path, body, label }) {
  const list = _read()
  const entry = {
    id: _uuid(),
    method: (method || 'POST').toUpperCase(),
    path,
    body: body ?? null,
    idempotencyKey: _uuid(),
    label: label || path,
    ts: Date.now(),
    tries: 0,
  }
  list.push(entry)
  _write(list)
  return entry
}

export function removeFromOutbox(id) {
  _write(_read().filter((e) => e.id !== id))
}

let _flushing = false

// Прогнать очередь. sendRaw(entry) должен вернуть Promise:
//   - resolve  → элемент отправлен, убираем
//   - reject с err.permanent === true → 4xx, дропаем + лог
//   - reject иначе → сеть/5xx, оставляем в очереди, прерываем проход
// Возвращает {sent, failed, remaining}.
export async function flushOutbox(sendRaw) {
  if (_flushing) return { sent: 0, failed: 0, remaining: outboxCount(), skipped: true }
  _flushing = true
  let sent = 0
  let failed = 0
  try {
    // порядок сохранения — FIFO
    for (const entry of _read()) {
      try {
        await sendRaw(entry)
        removeFromOutbox(entry.id)
        sent += 1
      } catch (err) {
        if (err && err.permanent) {
          // eslint-disable-next-line no-console
          console.warn('[outbox] permanent failure, dropping', entry.label, err.message)
          removeFromOutbox(entry.id)
          failed += 1
          continue
        }
        // транзиентная ошибка — увеличиваем счётчик попыток, прерываемся:
        // нет смысла долбить остальные тем же оборванным соединением
        const list = _read()
        const it = list.find((e) => e.id === entry.id)
        if (it) { it.tries = (it.tries || 0) + 1; _write(list) }
        break
      }
    }
  } finally {
    _flushing = false
  }
  return { sent, failed, remaining: outboxCount() }
}

export function clearOutbox() {
  _write([])
}
