import React, { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { duplicates, listPeople, mergePerson, errText } from './peopleApi'
import { Btn, Chip, Note, inputStyle } from './parts'
import { fs, fmtBirthday, sourcesOf, pendingOf, redText } from './ui'

// «Склеить с…»: текущий человек (`person`) вливается в выбранного. Сверху — кандидаты
// из /people/duplicates, ниже — поиск по всем людям (VK «Мария Иванова» ↔ TG «Маша🌸»
// автоматика не сопоставит, поэтому ручной поиск обязателен).
export default function MergePicker({ s, Sheet, person, open, onClose, onMerged }) {
  const [cands, setCands] = useState([])
  const [q, setQ] = useState('')
  const [found, setFound] = useState(null)
  const [pick, setPick] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!open) return
    setPick(null); setErr(''); setQ(''); setFound(null)
    duplicates().then((d) => {
      const mine = []
      for (const p of d.pairs || []) {
        if (p.a.id === person.id) mine.push({ ...p.b, score: p.score, reasons: p.reasons })
        else if (p.b.id === person.id) mine.push({ ...p.a, score: p.score, reasons: p.reasons })
      }
      setCands(mine)
    }).catch(() => setCands([]))
  }, [open, person?.id])

  useEffect(() => {
    if (!open || q.trim().length < 2) { setFound(null); return }
    const t = setTimeout(() => {
      listPeople({ q: q.trim() }).then((d) => setFound((d.people || []).filter((x) => x.id !== person.id))).catch(() => setFound([]))
    }, 250)
    return () => clearTimeout(t)
  }, [q, open])

  const doMerge = async () => {
    setBusy(true); setErr('')
    try {
      await mergePerson(person.id, pick.id)
      onMerged(pick)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const row = (p, extra) => (
    <div key={p.id} onClick={() => setPick(p)} style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', minHeight: 48, borderRadius: 12, cursor: 'pointer',
      border: `1px solid ${s.brd}`, marginBottom: 6, background: 'rgba(255,255,255,0.18)',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: fs(14), color: s.text, fontWeight: 500, wordBreak: 'break-word' }}>{p.name}</div>
        <div style={{ fontSize: fs(11), color: s.tS }}>
          🎂 {fmtBirthday(p.birthday)}{sourcesOf(p).length ? ' · ' + sourcesOf(p).join(' · ') : ''}
        </div>
      </div>
      {extra}
    </div>
  )

  return (
    <Sheet s={s} open={open} onClose={onClose} title="Склеить с…">
      {pick ? (
        <div>
          <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>
            Влить <b>{person.name}</b> в <b>{pick.name}</b>?<br />
            <span style={{ color: s.tS }}>«{person.name}» исчезнет: факты, другие имена и пустые поля перейдут к «{pick.name}». Отменить нельзя.</span>
          </div>
          {err && <Note s={s} color={redText(s)}>{err}</Note>}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => setPick(null)}>Назад</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy} onClick={doMerge}>{busy ? '…' : 'Склеить'}</Btn>
          </div>
        </div>
      ) : (
        <div>
          {cands.length > 0 && (
            <>
              <div style={{ fontSize: fs(10), fontWeight: 600, letterSpacing: '0.4px', color: s.tM, margin: '0 0 6px' }}>ПОХОЖИЕ</div>
              {cands.map((c) => row(c, <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                {(c.reasons || []).includes('shared_names') && <Chip s={s}>общие имена</Chip>}
                <Chip s={s} color={s.amber}>{Math.round(c.score * 100)}%</Chip>
              </span>))}
            </>
          )}
          <div style={{ position: 'relative', margin: '10px 0' }}>
            <Search size={fs(15)} color={s.tM} style={{ position: 'absolute', left: 12, top: 15 }} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти человека по имени или другому имени"
              style={{ ...inputStyle(s), paddingLeft: 34 }} />
          </div>
          {found && found.length === 0 && <Note s={s}>Никого не нашла</Note>}
          {(found || []).map((p) => row(p, pendingOf(p) > 0 ? <Chip s={s} color={s.amber}>●{pendingOf(p)}</Chip> : null))}
        </div>
      )}
    </Sheet>
  )
}
