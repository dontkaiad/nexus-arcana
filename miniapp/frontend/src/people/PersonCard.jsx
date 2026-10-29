import React, { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Pencil } from 'lucide-react'
import FactGroups from './FactGroups'
import MergePicker from './MergePicker'
import PersonEdit from './PersonEdit'
import Accounts from './Accounts'
import { Btn, Chip, Note, inputStyle, Label } from './parts'
import {
  addFact, approveAll, approveFact, eraseFact, errText, getPerson, rejectAll, rejectFact, restoreFact,
} from './peopleApi'
import { STATUS_LABEL, TYPE_CHOICES, fmtBirthday, fs, haptic, plural, primaryColors, redText, statusColor, typeMeta } from './ui'

// Карточка человека вне режима разбора: правка, ручной факт, склейка, ревью по одному факту.
export default function PersonCard({ s, Sheet, id, onBack, onChanged, onStartReview, onOpenClient }) {
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
  const [eraseId, setEraseId] = useState(null)        // факт, для которого ждём второй тап «Стереть»

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
        <Note s={s} color={err ? redText(s) : undefined}>{err || 'Загружаю…'}</Note>
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
        {p.client_id && (
          <span role="button" onClick={() => onOpenClient?.(p.client_id)} style={{ cursor: 'pointer', minHeight: 32, display: 'inline-flex', alignItems: 'center' }}>
            <Chip s={s} color={s.acc}>🌒 Клиент Арканы ›</Chip>
          </span>
        )}
      </div>
      <Accounts s={s} person={p} onChanged={() => { load(); onChanged?.() }} />
      {p.aliases?.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
          {p.aliases.map((a) => <Chip key={a} s={s}>{a}</Chip>)}
        </div>
      )}
      {p.notes && <div style={{ fontSize: fs(12), color: s.text, whiteSpace: 'pre-wrap', margin: '4px 0 8px', wordBreak: 'break-word' }}>{p.notes}</div>}

      {err && <Note s={s} color={redText(s)}>{err}</Note>}

      {p.facts.length === 0 && <Note s={s}>Фактов пока нет</Note>}
      <FactGroups s={s} groups={p.facts} expanded={expanded} onToggle={toggle} showMentions={p.is_self}
        textStyle={(f) => (f.review_status === 'rejected' ? { textDecoration: 'line-through', color: s.tS } : {})}
        left={(f) => (
          <div title={f.review_status} style={{ width: 10, height: 10, borderRadius: 5, margin: '0 10px 0 6px', flexShrink: 0, background: statusColor(s, f.review_status) }} />
        )}
        right={(f) => (
          <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
            {f.review_status === 'pending' && (<>
              <div role="button" aria-label="принять" onClick={() => act(() => approveFact(p.id, f.id))} style={iconBtn(s.text)}>✓</div>
              <div role="button" aria-label="отклонить" onClick={() => act(() => rejectFact(p.id, f.id))} style={iconBtn(redText(s))}>✕</div>
            </>)}
            {f.review_status === 'confirmed' && (
              <div role="button" onClick={() => act(() => rejectFact(p.id, f.id))} style={textBtn(redText(s))}>✕ убрать</div>
            )}
          </div>
        )}
        caption={(f) => (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', margin: '-4px 0 6px 26px' }}>
            <span style={{ fontSize: fs(10), fontWeight: 700, letterSpacing: '0.3px', color: s.text }}>
              {f.review_status === 'pending' ? 'на проверке' : f.review_status === 'confirmed' ? 'принят' : 'отклонён'}
            </span>
            {f.review_status === 'rejected' && (<>
              <div role="button" onClick={() => act(() => restoreFact(p.id, f.id))} style={pillBtn(s, s.text)}>вернуть</div>
              {eraseId === f.id
                ? <div role="button" onClick={() => { setEraseId(null); act(() => eraseFact(p.id, f.id)) }} style={pillBtn(s, redText(s), true)}>Стереть навсегда?</div>
                : <div role="button" onClick={() => setEraseId(f.id)} style={pillBtn(s, redText(s))}>Стереть</div>}
            </>)}
          </div>
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
                border: `1px solid ${type === t ? primaryColors(s).background : s.tS}`, background: type === t ? primaryColors(s).background : 'transparent', color: type === t ? primaryColors(s).color : s.text,
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

const textBtn = (c) => ({
  minHeight: 44, padding: '0 10px', display: 'flex', alignItems: 'center', fontSize: 13, fontWeight: 700, color: c,
  cursor: 'pointer', whiteSpace: 'nowrap',
})

const pillBtn = (s, c, strong) => ({
  minHeight: 36, padding: '6px 12px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', fontSize: 12, fontWeight: 600,
  color: c, border: `1px solid ${c}66`, cursor: 'pointer', background: strong ? `${c}22` : 'transparent',
})

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
