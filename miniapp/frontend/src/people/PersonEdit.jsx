import React, { useEffect, useState } from 'react'
import { patchPerson, errText } from './peopleApi'
import { Btn, Label, Note, inputStyle } from './parts'

const toDateInput = (iso) => {
  const m = /^(\d{4}|-)-(\d{2})-(\d{2})$/.exec(iso || '')
  return m ? `${m[1] === '-' ? '2000' : m[1]}-${m[2]}-${m[3]}` : ''
}

// Правка карточки: имя, другие имена, ДР (+ «год неизвестен»), tg/vk, заметки.
// Шлём только изменённые поля (PATCH: не присланное не трогается, null — обнуляет).
export default function PersonEdit({ s, Sheet, person, open, onClose, onSaved }) {
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!open || !person) return
    setErr('')
    setF({
      name: person.name || '',
      aliases: (person.aliases || []).join(', '),
      birthday: toDateInput(person.birthday),
      unknownYear: person.birthday ? person.birthday_year_known === false : false,
      notes: person.notes || '',
    })
  }, [open, person?.id])

  if (!f) return null
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  const save = async () => {
    const body = {}
    const name = f.name.trim()
    if (!name) { setErr('Имя не может быть пустым'); return }
    if (name !== person.name) body.name = name
    const aliases = f.aliases.split(',').map((x) => x.trim()).filter(Boolean)
    if (JSON.stringify(aliases) !== JSON.stringify(person.aliases || [])) body.aliases = aliases
    const oldBd = toDateInput(person.birthday)
    if (f.birthday !== oldBd || (f.birthday && f.unknownYear !== (person.birthday_year_known === false))) {
      body.birthday = f.birthday || null
      if (f.birthday) body.birthday_year_known = !f.unknownYear
    }
    if (f.notes !== (person.notes || '')) body.notes = f.notes || null
    if (Object.keys(body).length === 0) { onClose(); return }
    setBusy(true); setErr('')
    try { onSaved(await patchPerson(person.id, body)) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Sheet s={s} open={open} onClose={onClose} title="Правка">
      <Label s={s}>Имя</Label>
      <input value={f.name} onChange={set('name')} style={inputStyle(s)} />
      <Label s={s}>Другие имена (через запятую)</Label>
      <input value={f.aliases} onChange={set('aliases')} placeholder="Машка, Мария, @nick" style={inputStyle(s)} />
      <div style={{ fontSize: 12, color: s.text, opacity: 0.7, margin: '4px 2px 0', lineHeight: 1.35 }}>
        Как ты зовёшь человека — чтобы бот понимал, о ком речь.
      </div>
      <Label s={s}>День рождения</Label>
      <input type="date" value={f.birthday} onChange={set('birthday')} style={inputStyle(s)} />
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 44, color: s.text, fontSize: 14 }}>
        <input type="checkbox" checked={f.unknownYear} onChange={set('unknownYear')} style={{ width: 20, height: 20 }} />
        Год неизвестен
      </label>
      <Label s={s}>Заметки</Label>
      <textarea value={f.notes} onChange={set('notes')} rows={3} style={{ ...inputStyle(s), resize: 'vertical' }} />
      {err && <Note s={s} color={s.red}>{err}</Note>}
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <Btn s={s} style={{ flex: 1 }} onClick={onClose}>Отмена</Btn>
        <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy} onClick={save}>{busy ? '…' : 'Сохранить'}</Btn>
      </div>
    </Sheet>
  )
}
