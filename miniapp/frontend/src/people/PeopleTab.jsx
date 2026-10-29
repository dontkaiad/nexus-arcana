import React, { useEffect, useState } from 'react'
import { Search, X } from 'lucide-react'
import PersonCard, { Header } from './PersonCard'
import ReviewMode from './ReviewMode'
import { Btn, Chip, Note, SourceLinks, inputStyle } from './parts'
import { listPeople } from './peopleApi'
import { fmtBirthday, fs, overlayBg, pendingOf, plural, redText } from './ui'

const FILTERS = [
  { k: 'all', l: 'Все', status: undefined },
  { k: 'pending', l: 'На проверке', status: 'pending' },
  { k: 'confirmed', l: '✓', status: 'confirmed' },
  { k: 'rejected', l: 'Отклонённые', status: 'rejected' },
]

// Раздел «Люди»: полноэкранный оверлей над Mini App (7-я вкладка в нижней панели на 375px
// не помещается). Список → «Разобрать» (ReviewMode) или карточка человека.
// `Sheet` и тема `s` приходят из App.jsx — циклического импорта нет.
export default function PeopleTab({ s, Sheet, onClose, onChanged, initialId, onOpenClient }) {
  const [view, setView] = useState(initialId ? { name: 'card', id: initialId } : { name: 'list' })
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [dq, setDq] = useState('')
  const [rows, setRows] = useState(null)
  const [pendingTotal, setPendingTotal] = useState(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => { const t = setTimeout(() => setDq(q.trim()), 250); return () => clearTimeout(t) }, [q])

  useEffect(() => {
    if (view.name !== 'list') return undefined
    let dead = false
    setErr('')
    const f = FILTERS.find((x) => x.k === filter)
    listPeople({ status: f.status, q: dq }).then((d) => !dead && setRows(d.people || []))
      .catch((e) => !dead && (setErr(String(e.message || e)), setRows([])))
    listPeople({ status: 'pending' }).then((d) => !dead && setPendingTotal((d.people || []).filter((x) => !x.is_self).length)).catch(() => {})
    return () => { dead = true }
  }, [filter, dq, tick, view.name])

  const refresh = () => { setTick((t) => t + 1); onChanged?.() }
  const back = () => { setView({ name: 'list' }); refresh() }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 60, overflowY: 'auto', overflowX: 'hidden', WebkitOverflowScrolling: 'touch',
      padding: '10px 14px calc(env(safe-area-inset-bottom, 0px) + 24px)',
      background: overlayBg(s),
    }}>
      {view.name === 'review' && <ReviewMode s={s} Sheet={Sheet} onExit={back} only={view.only} />}
      {view.name === 'card' && <PersonCard s={s} Sheet={Sheet} id={view.id} onBack={back} onChanged={onChanged} onOpenClient={onOpenClient}
        onStartReview={(id) => setView({ name: 'review', only: id })} />}
      {view.name === 'list' && (
        <div>
          <Header s={s} onBack={onClose} title="Люди"
            right={<div onClick={() => { setSearchOpen((v) => !v); if (searchOpen) setQ('') }}
              style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
              {searchOpen ? <X size={fs(19)} color={s.tS} /> : <Search size={fs(19)} color={s.tS} />}
            </div>} />
          {searchOpen && (
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Имя или другое имя"
              style={{ ...inputStyle(s), marginBottom: 8 }} />
          )}

          <Btn s={s} kind="primary" disabled={!pendingTotal} onClick={() => setView({ name: 'review' })} style={{ width: '100%', marginBottom: 8 }}>
            {pendingTotal ? `Разобрать: ${pendingTotal}` : pendingTotal === 0 ? 'Разбирать нечего' : 'Разобрать…'}
          </Btn>
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', margin: '0 -14px 10px', padding: '0 14px', scrollbarWidth: 'none' }}>
            {FILTERS.map((f) => (
              <div key={f.k} className={`pill${filter === f.k ? ' active' : ''}`} onClick={() => setFilter(f.k)} style={{ flexShrink: 0, minHeight: 36 }}>{f.l}</div>
            ))}
          </div>

          {rows === null && <Note s={s}>Загружаю…</Note>}
          {err && <Note s={s} color={redText(s)}>{err}</Note>}
          {rows && rows.length === 0 && !err && <Note s={s}>{dq ? 'Никого не нашла' : 'Здесь пока пусто'}</Note>}
          {(rows || []).map((p) => {
            const n = pendingOf(p)
            return (
              <div key={p.id} className="glass tap" onClick={() => setView({ name: 'card', id: p.id })}
                style={{ padding: '10px 14px', marginBottom: 6, minHeight: 56, ...(p.is_self ? { border: `1.5px solid ${s.acc}`, marginBottom: 10 } : {}) }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {p.is_self && <Chip s={s} color={s.acc}>Я</Chip>}
                  <div style={{ flex: 1, minWidth: 0, fontSize: fs(15), fontWeight: 500, color: s.text, wordBreak: 'break-word' }}>{p.name}</div>
                  {n > 0 && <Chip s={s} color={s.amber}>●{n}</Chip>}
                  {p.client_id && <Chip s={s} color={s.acc}>🌒 Клиент</Chip>}
                  {p.status === 'rejected' && <Chip s={s} color={s.red}>отклонён</Chip>}
                </div>
                <div style={{ fontSize: fs(11), color: s.text, marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  {!p.is_self && <span>🎂 {fmtBirthday(p.birthday)}</span>}
                  <SourceLinks s={s} p={p} />
                  {p.counts?.confirmed > 0 && <span>✓ {p.counts.confirmed} {plural(p.counts.confirmed, 'факт', 'факта', 'фактов')}</span>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
