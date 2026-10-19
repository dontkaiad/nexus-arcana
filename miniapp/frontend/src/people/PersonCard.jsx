import React, { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Pencil } from 'lucide-react'
import FactGroups from './FactGroups'
import MergePicker from './MergePicker'
import PersonEdit from './PersonEdit'
import { Btn, Chip, Note, SourceLinks, inputStyle, Label } from './parts'
import {
  addFact, approveAll, approveFact, errText, getPerson, rejectAll, rejectFact,
} from './peopleApi'
import { STATUS_LABEL, TYPE_CHOICES, fmtBirthday, fs, haptic, plural, statusColor, typeMeta } from './ui'

// Карточка человека вне режима разбора: правка, ручной факт, склейка, ревью по одному факту.
export default function PersonCard({ s, Sheet, id, onBack, onChanged, onStartReview }) {
  const [p, setP] = useState(null)
  const [err, setErr] = useState('')
  const [expanded, setExpanded] = useState(new Set())
  const [edit, setEdit] = useState(false)
  const [merge, setMerge] = useState(false)
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [type, setType] = useState('other')
  const [busy, setBusy] = useState(false)
  const [confirmRej, setConfirmRej] = useState(false)

  const load = useCallback(async () => {
    try { setP(await getPerson(id)); setErr('') } catch (e) { setErr(errText(e)) }
  }, [id])
  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    setBusy(true); setErr('')
    try { await fn(); haptic(); await load(); onChanged?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const toggle = (fid) => setExpanded((prev) => { const n = new Set(prev); n.has(fid) ? n.delete(fid) : n.add(fid); return n })

  if (!p) {
    return (
      <div>
        <Header s={s} onBack={onBack} title="" />
        <Note s={s} color={err ? s.red : undefined}>{err || 'Загружаю…'}</Note>
      </div>
    )
  }
  const pend = p.counts?.pending || 0
  const age = p.age != null ? ` · ${p.age} ${plural(p.age, 'год', 'года', 'лет')}` : ''

  return (
    <div>
      <Header s={s} onBack={onBack} title={p.name}
        right={<div onClick={() => setEdit(true)} style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><Pencil size={fs(17)} color={s.tS} /></div>} />

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', margin: '2px 0 6px' }}>
        {p.is_self
          ? <Chip s={s} color={s.acc}>Я</Chip>
          : <Chip s={s} color={statusColor(s, p.status)}>{STATUS_LABEL[p.status] || p.status}</Chip>}
        <span style={{ fontSize: fs(12), color: s.text, fontWeight: 500 }}>
          🎂 {fmtBirthday(p.birthday)}{age}
        </span>
      </div>
      <div style={{ fontSize: fs(12), marginBottom: 6 }}><SourceLinks s={s} p={p} /></div>
      {p.aliases?.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
          {p.aliases.map((a) => <Chip key={a} s={s}>{a}</Chip>)}
        </div>
      )}
      {p.notes && <div style={{ fontSize: fs(12), color: s.tS, whiteSpace: 'pre-wrap', margin: '4px 0 8px', wordBreak: 'break-word' }}>{p.notes}</div>}

      {err && <Note s={s} color={s.red}>{err}</Note>}

      {p.facts.length === 0 && <Note s={s}>Фактов пока нет</Note>}
      <FactGroups s={s} groups={p.facts} expanded={expanded} onToggle={toggle} showMentions={p.is_self}
        right={(f) => (
          <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
            {f.review_status !== 'confirmed' && (
              <div onClick={() => act(() => approveFact(p.id, f.id))} style={iconBtn(s.good)}>✓</div>
            )}
            {f.review_status !== 'rejected' && (
              <div onClick={() => act(() => rejectFact(p.id, f.id))} style={iconBtn(s.red)}>✕</div>
            )}
          </div>
        )}
        left={(f) => (
          <div title={f.review_status} style={{ width: 10, height: 10, borderRadius: 5, margin: '0 10px 0 6px', flexShrink: 0, background: statusColor(s, f.review_status) }} />
        )}
      />

      {adding ? (
        <div style={{ marginTop: 8 }}>
          <Label s={s}>Новый факт</Label>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Например: аллергия на орехи" style={inputStyle(s)} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0' }}>
            {TYPE_CHOICES.map((t) => (
              <div key={t} onClick={() => setType(t)} style={{
                padding: '8px 12px', minHeight: 36, borderRadius: 999, fontSize: fs(12), cursor: 'pointer',
                border: `1px solid ${type === t ? s.acc : s.tS}`, background: type === t ? s.acc : 'transparent', color: type === t ? '#fff' : s.text,
              }}>{typeMeta(t).icon} {typeMeta(t).label.toLowerCase()}</div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => { setAdding(false); setText('') }}>Отмена</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy || !text.trim()}
              onClick={() => act(async () => { await addFact(p.id, text.trim(), type); setText(''); setAdding(false) })}>Добавить</Btn>
          </div>
        </div>
      ) : (
        <Btn s={s} style={{ marginTop: 6 }} onClick={() => setAdding(true)}>＋ Добавить факт</Btn>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 }}>
        {p.is_self && pend > 0 && onStartReview && (
          <Btn s={s} kind="primary" onClick={() => onStartReview(p.id)}>Разобрать по одному ({pend})</Btn>
        )}
        {pend > 0 && (
          <Btn s={s} kind={p.is_self ? 'ghost' : 'primary'} disabled={busy} onClick={() => act(() => approveAll(p.id))}>✓ Принять все ({pend}){p.is_self ? '' : ' и подтвердить'}</Btn>
        )}
        {!p.is_self && <div style={{ display: 'flex', gap: 8 }}>
          <Btn s={s} style={{ flex: 1 }} onClick={() => setMerge(true)}>Склеить с…</Btn>
          {p.status === 'rejected'
            ? <Btn s={s} style={{ flex: 1 }} disabled={busy} onClick={() => act(() => approveAll(p.id))}>Вернуть</Btn>
            : confirmRej
              ? <Btn s={s} kind="danger" style={{ flex: 1 }} disabled={busy} onClick={() => act(async () => { await rejectAll(p.id); setConfirmRej(false) })}>Точно отклонить?</Btn>
              : <Btn s={s} kind="danger" style={{ flex: 1 }} onClick={() => setConfirmRej(true)}>Не мой чел</Btn>}
        </div>}
      </div>

      <PersonEdit s={s} Sheet={Sheet} person={p} open={edit} onClose={() => setEdit(false)}
        onSaved={() => { setEdit(false); load(); onChanged?.() }} />
      <MergePicker s={s} Sheet={Sheet} person={p} open={merge} onClose={() => setMerge(false)}
        onMerged={() => { setMerge(false); onChanged?.(); onBack() }} />
    </div>
  )
}

const iconBtn = (c) => ({
  width: 40, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 18, color: c, cursor: 'pointer', fontWeight: 700,
})

export function Header({ s, onBack, title, right, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, minHeight: 48 }}>
      <div onClick={onBack} style={{ width: 44, height: 44, marginLeft: -10, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
        <ArrowLeft size={fs(20)} color={s.text} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="page-title" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
        {sub}
      </div>
      {right}
    </div>
  )
}
