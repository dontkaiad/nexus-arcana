import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Pencil, Undo2 } from 'lucide-react'
import FactGroups from './FactGroups'
import MergePicker from './MergePicker'
import PersonEdit from './PersonEdit'
import { Header } from './PersonCard'
import { Btn, Chip, Note, SourceLinks, Tick } from './parts'
import { errText, getPerson, listPeople, rejectAll, reviewPerson } from './peopleApi'
import { fmtBirthday, fs, haptic } from './ui'

// Режим разбора: один человек за раз, все галочки по умолчанию стоят — снимаешь только
// мусор и жмёшь одну кнопку. Очередь = люди со статусом pending (сервер хранит прогресс:
// принятый человек из очереди уходит, можно бросить и продолжить). Счётчик «N / всего»:
// total запоминается в localStorage, чтобы после перезахода не «сжиматься».
const TOTAL_KEY = 'people_review_total_v1'
const readTotal = () => { try { return Number(localStorage.getItem(TOTAL_KEY)) || 0 } catch { return 0 } }
const writeTotal = (n) => { try { localStorage.setItem(TOTAL_KEY, String(n)) } catch { /* private mode */ } }

const flat = (card) => (card?.facts || []).flatMap((g) => g.items)
const pendingFacts = (card) => flat(card).filter((f) => f.review_status === 'pending')
const pendingGroups = (card) =>
  (card?.facts || []).map((g) => ({ ...g, items: g.items.filter((f) => f.review_status === 'pending') })).filter((g) => g.items.length)

export default function ReviewMode({ s, Sheet, onExit, only }) {
  const [queue, setQueue] = useState(null)
  const [total, setTotal] = useState(0)
  const [card, setCard] = useState(null)
  const [checked, setChecked] = useState(new Set())
  const [expanded, setExpanded] = useState(new Set())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [last, setLast] = useState(null)       // что можно отменить: {item, ids}
  const [confirmRej, setConfirmRej] = useState(false)
  const [merge, setMerge] = useState(false)
  const [edit, setEdit] = useState(false)
  const cache = useRef(new Map())
  const cur = queue && queue[0]

  useEffect(() => {
    if (only) {                                    // разбор одной карточки («Я»): без общей очереди и счётчика
      getPerson(only).then((p) => { setTotal(1); setQueue([p]) }).catch((e) => { setErr(errText(e)); setQueue([]) })
      return
    }
    listPeople({ status: 'pending' }).then((d) => {
      const ppl = (d.people || []).filter((x) => !x.is_self)
      const t = ppl.length ? Math.max(readTotal(), ppl.length) : 0
      writeTotal(t); setTotal(t); setQueue(ppl)
    }).catch((e) => { setErr(errText(e)); setQueue([]) })
  }, [])

  const loadCard = useCallback(async (id) => {
    if (cache.current.has(id)) return cache.current.get(id)
    const c = await getPerson(id)
    cache.current.set(id, c)
    return c
  }, [])

  useEffect(() => {
    if (!cur) { setCard(null); return undefined }
    let dead = false
    setCard(null); setErr(''); setConfirmRej(false)
    loadCard(cur.id).then((c) => {
      if (dead) return
      setCard(c)
      setChecked(new Set(pendingFacts(c).map((f) => f.id)))   // все отмечены по умолчанию
      setExpanded(new Set())
    }).catch((e) => !dead && setErr(errText(e)))
    if (queue[1]) loadCard(queue[1].id).catch(() => {})        // префетч следующего
    return () => { dead = true }
  }, [cur?.id])

  const remaining = queue ? queue.length : 0
  const position = total ? Math.min(total, total - remaining + 1) : 0

  const step = async (fn) => {
    setBusy(true); setErr('')
    try {
      const ids = await fn()
      cache.current.delete(cur.id)
      setLast({ item: cur, ids })
      setQueue((q) => q.slice(1))
      haptic()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false); setConfirmRej(false) }
  }

  const accept = () => {
    const pend = pendingFacts(card)
    const approve = pend.filter((f) => checked.has(f.id)).map((f) => f.id)
    const reject = pend.filter((f) => !checked.has(f.id)).map((f) => f.id)
    return step(async () => { await reviewPerson(cur.id, { approve, reject }); return [...approve, ...reject] })
  }
  const notMine = () => step(async () => {
    const ids = pendingFacts(card).map((f) => f.id)
    await rejectAll(cur.id)
    return ids
  })

  const undo = async () => {
    if (!last || busy) return
    setBusy(true); setErr('')
    try {
      await reviewPerson(last.item.id, { reset: last.ids, person_status: 'pending' })
      cache.current.delete(last.item.id)
      setQueue((q) => [last.item, ...q])
      setLast(null)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const reloadCard = async () => {
    const known = new Set(flat(card).map((f) => f.id))
    cache.current.delete(cur.id)
    const c = await loadCard(cur.id)
    setCard(c)
    const ids = new Set(pendingFacts(c).map((f) => f.id))
    setChecked((prev) => new Set([...ids].filter((id) => prev.has(id) || !known.has(id))))
  }

  const toggleCheck = (id) => setChecked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleOpen = (id) => setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })

  if (queue === null) return <div><Header s={s} onBack={onExit} title="Разбор" /><Note s={s} color={err ? s.red : undefined}>{err || 'Загружаю…'}</Note></div>

  if (!cur) {
    return (
      <div>
        <Header s={s} onBack={onExit} title="Разбор" />
        <Note s={s}>{total ? 'Всё разобрано 🎉' : 'Разбирать нечего — очередь пуста'}</Note>
        {last && <Btn s={s} onClick={undo} disabled={busy}><Undo2 size={fs(15)} /> Отменить последнее</Btn>}
        {err && <Note s={s} color={s.red}>{err}</Note>}
        <Btn s={s} kind="primary" style={{ marginTop: 10 }} onClick={onExit}>К списку</Btn>
      </div>
    )
  }

  const pend = card ? pendingFacts(card) : []
  const nChecked = pend.filter((f) => checked.has(f.id)).length
  const done = card ? flat(card).filter((f) => f.review_status === 'confirmed').length : 0

  return (
    <div style={{ paddingBottom: 230 }}>
      <Header s={s} onBack={onExit} title={cur.name}
        sub={<div style={{ fontSize: fs(11), color: s.text, fontWeight: 500 }}>{cur.is_self ? 'Я' : <>🎂 {fmtBirthday(cur.birthday)}  <SourceLinks s={s} p={cur} /></>}</div>}
        right={<>
          {!only && <span style={{ fontSize: fs(13), color: s.text, fontWeight: 700, whiteSpace: 'nowrap', margin: '0 2px' }}>{position} / {total}</span>}
          <div onClick={() => setEdit(true)} style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><Pencil size={fs(17)} color={s.tS} /></div>
        </>} />

      {cur.aliases?.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', margin: '0 0 6px' }}>
          {cur.aliases.slice(0, 6).map((a) => <Chip key={a} s={s}>{a}</Chip>)}
        </div>
      )}

      {!card && <Note s={s} color={err ? s.red : undefined}>{err || 'Загружаю…'}</Note>}
      {card && pend.length === 0 && <Note s={s}>Новых фактов нет — можно просто подтвердить человека</Note>}
      {card && (
        <FactGroups s={s} groups={pendingGroups(card)} expanded={expanded} onToggle={toggleOpen} showMentions={card.is_self}
          left={(f, t) => <Tick s={s} on={checked.has(f.id)} onClick={() => toggleCheck(f.id)} color={t === 'important' ? s.red : undefined} />} />
      )}
      {card && done > 0 && <div style={{ fontSize: fs(11), color: s.text, opacity: 0.7, textAlign: 'center', marginTop: 6 }}>Уже принято ранее: {done}</div>}
      {card && err && <Note s={s} color={s.red}>{err}</Note>}

      <div style={{
        position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 5,
        padding: '18px 14px calc(env(safe-area-inset-bottom, 0px) + 14px)',
        background: `linear-gradient(to top, ${s.base} 72%, transparent)`,
      }}>
        {last && (
          <div onClick={undo} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, minHeight: 36, marginBottom: 6,
            fontSize: fs(12), color: s.text, fontWeight: 600, cursor: 'pointer',
          }}><Undo2 size={fs(14)} /> Отменить: {last.item.name}</div>
        )}
        {!cur.is_self && (
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => setMerge(true)}>Склеить с…</Btn>
            {confirmRej
              ? <Btn s={s} kind="danger" style={{ flex: 1, background: `${s.red}22` }} disabled={busy || !card} onClick={notMine}>Точно отклонить?</Btn>
              : <Btn s={s} kind="danger" style={{ flex: 1 }} disabled={busy || !card} onClick={() => setConfirmRej(true)}>Не мой чел</Btn>}
          </div>
        )}
        <Btn s={s} kind="primary" style={{ width: '100%' }} disabled={busy || !card} onClick={accept}>
          {busy ? '…' : pend.length ? `✓ Принять отмеченные (${nChecked}/${pend.length})` : '✓ Подтвердить человека'}
        </Btn>
      </div>

      {card && <PersonEdit s={s} Sheet={Sheet} person={card} open={edit} onClose={() => setEdit(false)}
        onSaved={async (p) => { setEdit(false); setQueue((q) => [{ ...q[0], name: p.name, aliases: p.aliases, birthday: p.birthday }, ...q.slice(1)]); await reloadCard() }} />}
      {cur && <MergePicker s={s} Sheet={Sheet} person={cur} open={merge} onClose={() => setMerge(false)}
        onMerged={(target) => { setMerge(false); cache.current.delete(target.id); cache.current.delete(cur.id); setLast(null); setQueue((q) => q.slice(1)) }} />}
    </div>
  )
}
