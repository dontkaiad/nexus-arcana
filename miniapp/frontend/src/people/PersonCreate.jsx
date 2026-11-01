import React, { useEffect, useState } from 'react'
import { redText } from './ui'
import { createPerson, errText } from './peopleApi'
import { Btn, Label, Note, inputStyle } from './parts'
import RelationPicker from './RelationPicker'

// «+ Человек»: имя + «кто это». Тёзки разрешены (на это указывает homonym_ids в ответе — подскажем, но не запретим).
export default function PersonCreate({ s, Sheet, open, onClose, onCreated }) {
  const [name, setName] = useState('')
  const [relation, setRelation] = useState(null)
  const [about, setAbout] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (open) { setName(''); setRelation(null); setAbout(''); setErr('') } }, [open])

  const save = async () => {
    if (!name.trim()) { setErr('Имя не может быть пустым'); return }
    setBusy(true); setErr('')
    try { onCreated(await createPerson({ name: name.trim(), relation, about: about.trim() || null })) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Sheet s={s} open={open} onClose={onClose} title="Новый человек">
      <Label s={s}>Имя</Label>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Как зовёшь" style={inputStyle(s)} />
      <RelationPicker s={s} relation={relation} about={about} onRelation={setRelation} onAbout={setAbout} />
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <Btn s={s} style={{ flex: 1 }} onClick={onClose}>Отмена</Btn>
        <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy} onClick={save}>{busy ? '…' : 'Создать'}</Btn>
      </div>
    </Sheet>
  )
}
