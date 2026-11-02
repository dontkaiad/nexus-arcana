import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Pencil } from 'lucide-react'
import { Header } from '../people/PersonCard'
import FactGroups from '../people/FactGroups'
import { Btn, Chip, Label, Note, inputStyle } from '../people/parts'
import { TYPE_CHOICES, fs, haptic, plural, primaryColors, redText, typeMeta } from '../people/ui'
import CatAvatar from './CatAvatar'
import CatForm from './CatForm'
import { addCatFact, errText, getCat, uploadCatPhoto } from './catsApi'

const NOOP = () => {}
const NONE = new Set()

// Карточка кота: фото (или заглушка + «Загрузить фото»), клички, факты по типам, «Добавить факт».
export default function CatCard({ s, Sheet, id, onBack, onChanged }) {
  const [cat, setCat] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState(false)
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [type, setType] = useState('other')
  const fileRef = useRef(null)

  const load = useCallback(async () => {
    try { setCat(await getCat(id)); setErr('') } catch (e) { setErr(errText(e)) }
  }, [id])
  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    setBusy(true); setErr('')
    try { await fn(); haptic(); await load(); onChanged?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const pick = (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''                                   // тот же файл можно выбрать повторно
    if (f) act(() => uploadCatPhoto(id, f))
  }

  if (!cat) {
    return (
      <div>
        <Header s={s} onBack={onBack} title="" />
        <Note s={s} color={err ? redText(s) : undefined}>{err || 'Загружаю…'}</Note>
      </div>
    )
  }
  const n = cat.facts_count || 0
  return (
    <div>
      <Header s={s} onBack={onBack} title={cat.name}
        right={<div aria-label="править" onClick={() => setEdit(true)} style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><Pencil size={fs(17)} color={s.tS} /></div>} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '6px 0 10px' }}>
        <CatAvatar s={s} cat={cat} size={96} radius={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <input ref={fileRef} type="file" accept="image/*" onChange={pick} style={{ display: 'none' }} data-testid="cat-photo-input" />
          <Btn s={s} disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? '…' : cat.photo_url ? 'Сменить фото' : 'Загрузить фото'}
          </Btn>
          <div style={{ fontSize: fs(12), color: s.text, marginTop: 6 }}>
            {n} {plural(n, 'факт', 'факта', 'фактов')}
          </div>
        </div>
      </div>
      {cat.aliases?.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
          {cat.aliases.map((a) => <Chip key={a} s={s}>{a}</Chip>)}
        </div>
      )}

      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      {cat.facts.length === 0 && <Note s={s}>Фактов пока нет. Напиши боту «{cat.name} …» или добавь вручную.</Note>}
      <FactGroups s={s} groups={cat.facts} expanded={NONE} onToggle={NOOP}
        textStyle={(f) => (f.is_current === false ? { opacity: 0.6 } : {})}
        caption={(f) => (f.is_current === false
          ? <div style={{ fontSize: fs(10), fontWeight: 700, color: s.text, margin: '-4px 0 6px 12px' }}>неактуально</div> : null)} />

      {adding ? (
        <div style={{ marginTop: 8 }}>
          <Label s={s}>Новый факт</Label>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Например: не ест курицу" style={inputStyle(s)} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0' }}>
            {TYPE_CHOICES.map((t) => (
              <div key={t} onClick={() => setType(t)} style={{
                padding: '8px 12px', minHeight: 36, borderRadius: 999, fontSize: fs(12), cursor: 'pointer',
                border: `1px solid ${type === t ? primaryColors(s).background : s.tS}`,
                background: type === t ? primaryColors(s).background : 'transparent', color: type === t ? primaryColors(s).color : s.text,
              }}>{typeMeta(t).icon} {typeMeta(t).label.toLowerCase()}</div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => { setAdding(false); setText('') }}>Отмена</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy || !text.trim()}
              onClick={() => act(async () => { await addCatFact(cat.id, text.trim(), type); setText(''); setAdding(false) })}>Добавить</Btn>
          </div>
        </div>
      ) : (
        <Btn s={s} style={{ marginTop: 6 }} onClick={() => setAdding(true)}>＋ Добавить факт</Btn>
      )}

      <CatForm s={s} Sheet={Sheet} open={edit} cat={cat} onClose={() => setEdit(false)}
        onSaved={() => { setEdit(false); load(); onChanged?.() }}
        onRemoved={() => { setEdit(false); onChanged?.(); onBack() }} />
    </div>
  )
}
