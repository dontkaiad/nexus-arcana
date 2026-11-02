// Тонкая обёртка над /api/cats. Мутации идут мимо офлайн-очереди (путь не в whitelist outbox):
// правка карточки должна пройти сразу или показать ошибку, а не «записано локально».
import { apiGet, apiPatch, apiPost } from '../api'

export const listCats = () => apiGet('/api/cats')
export const getCat = (id) => apiGet(`/api/cats/${id}`)
export const generalFacts = () => apiGet('/api/cats/general')
export const createCat = (body) => apiPost('/api/cats', body)
export const patchCat = (id, body) => apiPatch(`/api/cats/${id}`, body)
export const addCatFact = (id, text, type) => apiPost(`/api/cats/${id}/facts`, { text, type })
export const attachFact = (catId, factId) => apiPost(`/api/cats/${catId}/facts/${factId}/attach`, {})

export const PHOTO_MAX_BYTES = 5 * 1024 * 1024

// Клички одной строкой через запятую/точку с запятой/перенос → массив без пустых.
export const splitAliases = (str) => String(str || '').split(/[,;\n]/).map((x) => x.trim()).filter(Boolean)

// Загрузка фото (multipart): apiPost шлёт JSON, поэтому fetch напрямую, как загрузка фото расклада.
export async function uploadCatPhoto(id, file) {
  if (!file || !String(file.type || '').startsWith('image/')) throw new Error('Нужна картинка (jpg, png…)')
  if (file.size > PHOTO_MAX_BYTES) throw new Error('Фото больше 5 МБ')
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${import.meta.env.VITE_API_BASE || ''}/api/cats/${id}/photo`, {
    method: 'POST',
    headers: { 'X-Telegram-Init-Data': window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || '' },
    body: fd,
  })
  if (!r.ok) {
    const j = await r.json().catch(() => ({}))
    throw new Error(`${r.status} — ${j.detail || r.statusText}`)
  }
  return r.json()
}

// Человекочитаемая ошибка из HttpError ("409 Conflict — {...}") и пр.
export function errText(e) {
  const m = String(e?.message || e || '')
  if (/already exists/.test(m)) return 'Кот с таким именем уже есть'
  if (/already belongs/.test(m)) return 'Этот факт уже привязан к другому коту или человеку'
  if (/already has this fact/.test(m)) return 'У этого кота уже есть такой факт'
  if (/not a cat fact/.test(m)) return 'Привязать можно только факт из раздела «Коты»'
  if (/cloudinary not configured/.test(m)) return 'Загрузка фото не настроена на сервере'
  if (/^413/.test(m) || /больше 5/.test(m)) return 'Фото больше 5 МБ'
  if (/^415/.test(m)) return 'Нужна картинка (jpg, png…)'
  if (/^404/.test(m)) return 'Не найдено (возможно, уже изменено)'
  return m.replace(/^\d+\s+[^—]*—\s*/, '') || 'Ошибка'
}
