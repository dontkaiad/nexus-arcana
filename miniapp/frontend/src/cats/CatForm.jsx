import React, { useEffect, useState } from 'react'
import { redText } from '../people/ui'
import { Btn, Label, Note, inputStyle } from '../people/parts'
import { createCat, errText, patchCat, splitAliases } from './catsApi'

// «+ Кот» и правка карточки: имя + клички (через запятую). Клички нужны боту, чтобы узнавать кота в сообщении.
// В режиме правки есть «Убрать карточку» (status=rejected, в два тапа); факты кота остаются в Памяти.
export default function CatForm({ s, Sheet, open, cat, onClose, onSaved, onRemoved }) {
  const [name, setName] = useState('')
  const [aliases, setAliases] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [confirmRm, setConfirmRm] = useState(false)
  useEffect(() => {
    if (open) { setName(cat?.name || ''); setAliases((cat?.aliases || []).join(', ')); setErr(''); setConfirmRm(false) }
  }, [open, cat])

  const save = async () => {
    if (!name.trim()) { setErr('Имя не может быть пустым'); return }
    setBusy(true); setErr('')
    try {
      const body = { name: name.trim(), aliases: splitAliases(aliases) }
      onSaved(cat ? await patchCat(cat.id, body) : await createCat(body))
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true); setErr('')
    try { await patchCat(cat.id, { status: 'rejected' }); onRemoved?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Sheet s={s} open={open} onClose={onClose} title={cat ? 'Карточка кота' : 'Новый кот'}>
      <Label s={s}>Имя</Label>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Как зовут" style={inputStyle(s)} />
      <Label s={s}>Клички (через запятую)</Label>
      <input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="Например: Пушок, Пушистик" style={inputStyle(s)} />
      <Note s={s}>По имени и кличкам бот привяжет к коту факты из сообщений.</Note>
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <Btn s={s} style={{ flex: 1 }} onClick={onClose}>Отмена</Btn>
        <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy} onClick={save}>{busy ? '…' : cat ? 'Сохранить' : 'Создать'}</Btn>
      </div>
      {cat && (
        confirmRm
          ? <Btn s={s} kind="danger" style={{ width: '100%', marginTop: 10 }} disabled={busy} onClick={remove}>Точно убрать карточку?</Btn>
          : <Btn s={s} kind="danger" style={{ width: '100%', marginTop: 10 }} onClick={() => setConfirmRm(true)}>Убрать карточку</Btn>
      )}
    </Sheet>
  )
}
