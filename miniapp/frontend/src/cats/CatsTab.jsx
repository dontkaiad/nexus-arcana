import React, { useEffect, useState } from 'react'
import { Header } from '../people/PersonCard'
import { Btn, Chip, Note } from '../people/parts'
import { fs, overlayBg, plural, redText } from '../people/ui'
import CatAvatar from './CatAvatar'
import CatCard from './CatCard'
import CatForm from './CatForm'
import GeneralFacts from './GeneralFacts'
import { listCats } from './catsApi'

// Раздел «Коты»: полноэкранный оверлей над Mini App (как «Люди»). Список строится от таблицы cats,
// а не от категории Памяти. Отдельной строкой — «Общее про котов» (факты категории без кота).
// `Sheet` и тема `s` приходят из App.jsx — циклического импорта нет.
export default function CatsTab({ s, Sheet, onClose, onChanged, initialId }) {
  const [view, setView] = useState(initialId ? { name: 'card', id: initialId } : { name: 'list' })
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (view.name !== 'list') return undefined
    let dead = false
    setErr('')
    listCats().then((d) => !dead && setData(d)).catch((e) => !dead && (setErr(String(e.message || e)), setData({ cats: [], total: 0, general_count: 0 })))
    return () => { dead = true }
  }, [tick, view.name])

  const refresh = () => { setTick((t) => t + 1); onChanged?.() }
  const back = () => { setView({ name: 'list' }); refresh() }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 60, overflowY: 'auto', overflowX: 'hidden', WebkitOverflowScrolling: 'touch',
      padding: '10px 14px calc(env(safe-area-inset-bottom, 0px) + 24px)',
      background: overlayBg(s),
    }}>
      {view.name === 'card' && <CatCard s={s} Sheet={Sheet} id={view.id} onBack={back} onChanged={onChanged} />}
      {view.name === 'general' && <GeneralFacts s={s} onBack={back} onChanged={onChanged} />}
      {view.name === 'list' && (
        <div>
          <Header s={s} onBack={onClose} title="Коты" />
          <Btn s={s} style={{ width: '100%', marginBottom: 8 }} onClick={() => setCreating(true)}>+ Кот</Btn>
          <CatForm s={s} Sheet={Sheet} open={creating} onClose={() => setCreating(false)}
            onSaved={(c) => { setCreating(false); refresh(); setView({ name: 'card', id: c.id }) }} />

          {data === null && <Note s={s}>Загружаю…</Note>}
          {err && <Note s={s} color={redText(s)}>{err}</Note>}
          {data && data.total === 0 && !err && (
            <Note s={s}>Котов пока нет. Заведи карточку («+ Кот»): бот начнёт привязывать к ней факты из сообщений.</Note>
          )}
          {(data?.cats || []).map((c) => (
            <div key={c.id} className="glass tap" onClick={() => setView({ name: 'card', id: c.id })}
              style={{ padding: '10px 14px', marginBottom: 6, minHeight: 56, display: 'flex', alignItems: 'center', gap: 12 }}>
              <CatAvatar s={s} cat={c} size={48} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: fs(15), fontWeight: 500, color: s.text, wordBreak: 'break-word' }}>{c.name}</div>
                <div style={{ fontSize: fs(11), color: s.text, marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span>{c.facts_count} {plural(c.facts_count, 'факт', 'факта', 'фактов')}</span>
                  {(c.aliases || []).length > 0 && <span>{c.aliases.join(', ')}</span>}
                  {!c.photo_url && <Chip s={s} color={s.amber}>нет фото</Chip>}
                </div>
              </div>
            </div>
          ))}
          {data?.general_count > 0 && (
            <div role="button" className="glass tap" onClick={() => setView({ name: 'general' })}
              style={{ padding: '10px 14px', marginTop: 10, minHeight: 56, display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 48, height: 48, borderRadius: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: fs(22), background: `${s.acc}22` }}>🐾</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: fs(15), fontWeight: 500, color: s.text }}>Общее про котов</div>
                <div style={{ fontSize: fs(11), color: s.text, marginTop: 2 }}>
                  {data.general_count} {plural(data.general_count, 'факт', 'факта', 'фактов')} без привязки
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
