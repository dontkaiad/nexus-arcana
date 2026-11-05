import React, { useCallback, useEffect, useState } from 'react'
import { Btn, Note } from './parts'
import { dismissSimilar, errText, mergeSimilar, similarFacts } from './peopleApi'
import { fs, haptic, redText } from './ui'

// Раздел «Похожие (N групп)» карточки «Я»: группы фактов с почти одинаковым смыслом (по готовым эмбеддингам).
// В группе тап по формулировке выбирает ту, что останется (по умолчанию — ручной/принятый/частый факт);
// «Объединить» прячет остальные под ней (цитаты и упоминания переходят к ней, «Разъединить» возвращает),
// «Это разное» навсегда убирает группу из предложений.
export default function SimilarFacts({ s, personId, onChanged }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [keep, setKeep] = useState({})          // key группы → выбранный id
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setData(await similarFacts(personId)); setErr('') } catch (e) { setErr(errText(e)) }
  }, [personId])
  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    setBusy(true); setErr('')
    try { await fn(); haptic(); await load(); onChanged?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (!data || data.count === 0) return err ? <Note s={s} color={redText(s)}>{err}</Note> : null
  return (
    <div className="glass" style={{ marginBottom: 10, padding: '4px 12px' }}>
      <div role="button" onClick={() => setOpen(!open)} style={{ minHeight: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', fontWeight: 700, fontSize: fs(13), color: s.text }}>
        <span>🔁 Похожие ({data.count} {data.count === 1 ? 'группа' : 'групп'})</span>
        <span>{open ? '▾' : '▸'}</span>
      </div>
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      {open && data.groups.map((g) => {
        const sel = keep[g.key] ?? g.keep_id
        const ids = g.items.map((i) => i.id)
        return (
          <div key={g.key} style={{ borderTop: `0.5px solid ${s.brd}`, padding: '8px 0' }}>
            {g.items.map((i) => (
              <div key={i.id} role="radio" aria-checked={sel === i.id} onClick={() => setKeep({ ...keep, [g.key]: i.id })}
                style={{ display: 'flex', gap: 8, alignItems: 'flex-start', minHeight: 44, padding: '6px 0', cursor: 'pointer' }}>
                <div style={{ width: 20, height: 20, borderRadius: 10, flexShrink: 0, marginTop: 2, border: `1.5px solid ${sel === i.id ? s.acc : s.tM}`, background: sel === i.id ? s.acc : 'transparent' }} />
                <div style={{ flex: 1, minWidth: 0, fontSize: fs(14), color: s.text, lineHeight: 1.35, wordBreak: 'break-word' }}>
                  {i.text}
                  <div style={{ fontSize: fs(10), fontWeight: 600, marginTop: 2 }}>
                    {i.source === 'manual' ? '✍︎ вручную · ' : ''}×{i.mention_count}{i.review_status === 'confirmed' ? ' · принят' : ''}
                  </div>
                </div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <Btn s={s} kind="primary" disabled={busy} style={{ flex: 1 }} onClick={() => act(() => mergeSimilar(personId, sel, ids))}>Объединить</Btn>
              <Btn s={s} disabled={busy} style={{ flex: 1 }} onClick={() => act(() => dismissSimilar(personId, ids))}>Это разное</Btn>
            </div>
          </div>
        )
      })}
    </div>
  )
}
