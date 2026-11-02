import React, { useCallback, useEffect, useState } from 'react'
import { Header } from '../people/PersonCard'
import FactGroups from '../people/FactGroups'
import { Note } from '../people/parts'
import { fs, haptic, redText } from '../people/ui'
import CatAvatar from './CatAvatar'
import { attachFact, errText, generalFacts, listCats } from './catsApi'

const NOOP = () => {}
const NONE = new Set()

// «Общее про котов»: факты категории «Коты» без кота (про всех сразу или про кота без карточки).
// У каждого — «Привязать к коту» со списком карточек; после привязки факт переезжает в карточку.
export default function GeneralFacts({ s, onBack, onChanged }) {
  const [data, setData] = useState(null)
  const [cats, setCats] = useState([])
  const [pickFor, setPickFor] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const [g, c] = await Promise.all([generalFacts(), listCats()])
      setData(g); setCats(c.cats || []); setErr('')
    } catch (e) { setErr(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  const attach = async (catId, factId) => {
    setBusy(true); setErr('')
    try { await attachFact(catId, factId); haptic(); setPickFor(null); await load(); onChanged?.() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div>
      <Header s={s} onBack={onBack} title="Общее про котов" />
      <Note s={s}>Факты без привязки к коту. Привяжи к нужному, и они переедут в его карточку.</Note>
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      {!data && !err && <Note s={s}>Загружаю…</Note>}
      {data && data.total === 0 && <Note s={s}>Здесь пусто: все факты привязаны</Note>}
      {data && data.total > 0 && (
        <FactGroups s={s} groups={data.facts} expanded={NONE} onToggle={NOOP}
          right={(f) => (
            <div role="button" aria-label="привязать к коту" onClick={() => setPickFor(pickFor === f.id ? null : f.id)}
              style={{ minHeight: 44, padding: '0 8px', display: 'flex', alignItems: 'center', fontSize: fs(12), fontWeight: 700,
                color: s.text, cursor: 'pointer', textAlign: 'right', flexShrink: 0, maxWidth: 96, lineHeight: 1.2 }}>
              Привязать к коту
            </div>
          )}
          caption={(f) => pickFor !== f.id ? null : (
            <div style={{ margin: '0 4px 8px 12px' }}>
              {cats.length === 0
                ? <Note s={s}>Сначала заведи карточку кота (раздел «Коты» → «+ Кот»)</Note>
                : cats.map((c) => (
                  <div key={c.id} role="button" data-attach-to={c.id} onClick={() => !busy && attach(c.id, f.id)}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 44, padding: '4px 6px', cursor: 'pointer', borderRadius: 10 }}>
                    <CatAvatar s={s} cat={c} size={32} radius={10} />
                    <span style={{ fontSize: fs(14), color: s.text, fontWeight: 500 }}>{c.name}</span>
                  </div>
                ))}
            </div>
          )} />
      )}
    </div>
  )
}
