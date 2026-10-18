// Тонкая обёртка над /api/people. Мутации идут мимо офлайн-очереди (путь не в whitelist
// outbox): ревью должно либо пройти сразу, либо показать ошибку — не «записано локально».
import { apiGet, apiPost, apiPatch } from '../api'

const qs = (o) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')
  return p.length ? '?' + p.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&') : ''
}

export const listPeople = ({ status, q } = {}) => apiGet('/api/people' + qs({ status, q }))
export const getPerson = (id) => apiGet(`/api/people/${id}`)
export const patchPerson = (id, body) => apiPatch(`/api/people/${id}`, body)

// Пакетное ревью (одна транзакция): approve/reject — id фактов; reset + person_status:'pending' — undo.
export const reviewPerson = (id, { approve = [], reject = [], reset = [], person_status = 'confirmed' } = {}) =>
  apiPost(`/api/people/${id}/review`, { approve, reject, reset, person_status })

export const approveFact = (id, fid) => apiPost(`/api/people/${id}/facts/${fid}/approve`, {})
export const rejectFact = (id, fid) => apiPost(`/api/people/${id}/facts/${fid}/reject`, {})
export const approveAll = (id) => apiPost(`/api/people/${id}/approve-all`, {})
export const rejectAll = (id) => apiPost(`/api/people/${id}/reject-all`, {})
export const addFact = (id, text, type) => apiPost(`/api/people/${id}/facts`, { text, type })
export const duplicates = () => apiGet('/api/people/duplicates')
export const mergePerson = (id, intoId) => apiPost(`/api/people/${id}/merge`, { into_id: intoId })

// Человекочитаемая ошибка из HttpError ("409 Conflict — {...}") и пр.
export function errText(e) {
  const m = String(e?.message || e || '')
  if (/conflicting_ids/.test(m)) return 'У людей разные Telegram/VK-аккаунты — склеить нельзя'
  if (/already belongs/.test(m)) return 'Этот tg_id/vk_id уже у другого человека'
  if (/^404/.test(m)) return 'Не найдено (возможно, уже изменено)'
  return m.replace(/^\d+\s+[^—]*—\s*/, '') || 'Ошибка'
}
