import React, { useEffect, useState } from 'react'
import { Btn, Label, Note, inputStyle } from './parts'
import RelationPicker from './RelationPicker'
import { errText, giveFacts, listPeople, suggestOwner } from './peopleApi'
import { fs, haptic, redText } from './ui'

// «👤 Это про…» / «👤 Отдать…»: выбор человека, которому принадлежат факты. Сверху — человек из того же чата,
// ниже поиск по Людям, внизу «+ Новый человек». Выбор переносит факты в его карточку (подтверждёнными).
export default function GiveSheet({ s, Sheet, personId, ids, onClose, onDone }) {
  const [sug, setSug] = useState([])
  const [q, setQ] = useState('')
  const [found, setFound] = useState(null)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [relation, setRelation] = useState(null)
  const [about, setAbout] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const open = !!ids && ids.length > 0

  useEffect(() => {
    if (!open) return
    setQ(''); setFound(null); setAdding(false); setName(''); setRelation(null); setAbout(''); setErr('')
    suggestOwner(personId, ids).then((d) => setSug(d.candidates || [])).catch(() => setSug([]))
  }, [open, personId, (ids || []).join(',')])

  useEffect(() => {
    if (!open || q.trim().length < 2) { setFound(null); return undefined }
    const t = setTimeout(() => {
      listPeople({ q: q.trim() }).then((d) => setFound((d.people || []).filter((x) => x.id !== personId && !x.is_self && x.status !== 'rejected'))).catch(() => setFound([]))
    }, 250)
    return () => clearTimeout(t)
  }, [q, open])

  const give = async (to) => {
    setBusy(true); setErr('')
    try {
      const r = await giveFacts(personId, ids, to)
      haptic()
      if (r.conflicts || r.skipped) setErr(`Перенесено ${r.moved}; уже были у человека: ${r.conflicts}; не найдено: ${r.skipped}`)
      onDone(r)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const row = (p, why) => (
    <div key={p.id} role="button" onClick={() => !busy && give(p.id)} style={{
      display: 'flex', flexDirection: 'column', justifyContent: 'center', minHeight: 48, padding: '8px 12px', borderRadius: 12, cursor: 'pointer',
      border: `1px solid ${s.brd}`, marginBottom: 6, background: 'rgba(255,255,255,0.18)',
    }}>
      <span style={{ fontSize: fs(14), color: s.text, fontWeight: 600 }}>{p.label || p.name}</span>
      {why && <span style={{ fontSize: fs(11), color: s.text, opacity: 0.8 }}>{why}</span>}
    </div>
  )

  return (
    <Sheet s={s} open={open} onClose={onClose} title={ids && ids.length > 1 ? `Отдать ${ids.length} фактов…` : 'Это про…'}>
      {sug.length > 0 && (<>
        <Label s={s}>Из того же чата</Label>
        {sug.map((p) => row(p, p.why))}
      </>)}
      <Label s={s}>Найти в Людях</Label>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Имя или другое имя" style={inputStyle(s)} />
      <div style={{ marginTop: 6 }}>
        {found && found.length === 0 && <Note s={s}>Никого не нашла</Note>}
        {(found || []).map((p) => row(p))}
      </div>
      {adding ? (
        <div style={{ marginTop: 10 }}>
          <Label s={s}>Новый человек</Label>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Как зовёшь" style={inputStyle(s)} />
          <RelationPicker s={s} relation={relation} about={about} onRelation={setRelation} onAbout={setAbout} />
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => setAdding(false)}>Отмена</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy || !name.trim()}
              onClick={() => give({ name: name.trim(), relation, about: about.trim() || null })}>{busy ? '…' : 'Создать и отдать'}</Btn>
          </div>
        </div>
      ) : (
        <Btn s={s} style={{ marginTop: 10 }} onClick={() => setAdding(true)}>＋ Новый человек</Btn>
      )}
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
    </Sheet>
  )
}
