import React, { useState } from 'react'
import { Btn, Chip, Note } from './parts'
import { fs, typeMeta } from './ui'
import { EMPTY_FILTER, MENTION_BUCKETS, STATUSES, TYPES, applyFilter, facetCounts, yearsOf } from './bulkFilter'

// Какие массовые действия доступны в выбранном статусе (все обратимы).
const actionsFor = (status) => {
  const approve = { kind: 'approve', label: '✓ Принять все', sure: 'Точно принять', style: 'ghost' }
  const reject = { kind: 'reject', label: '✕ Отклонить все', sure: 'Точно отклонить', style: 'danger' }
  const reset = { kind: 'reset', label: '↩︎ Вернуть на проверку', sure: 'Точно вернуть на проверку', style: 'ghost' }
  if (status === 'pending') return [approve, reject]
  if (status === 'confirmed') return [reject, reset]
  if (status === 'rejected') return [reset]
  return [approve, reject]
}

const STATUS_RU = { pending: 'на проверке', confirmed: 'принятые', rejected: 'отклонённые' }

// Панель массового разбора фактов карточки «Я»: счётчик, фильтры (тип / упоминаний / год), действия по фильтру
// с подтверждением. Всё обратимо: отклонённые остаются в фильтре «отклонённые» и возвращаются «Вернуть на проверку».
export default function BulkReview({ s, facts, flt, setFlt, busy, onApprove, onReject, onReset }) {
  const [confirm, setConfirm] = useState(null)               // 'approve' | 'reject' | 'reset' — ждём второй тап
  const total = facts.length
  const pending = facts.filter((f) => f.review_status === 'pending').length
  const set = (key, v) => { setConfirm(null); setFlt({ ...flt, [key]: v }) }
  const inFilter = applyFilter(facts, flt)
  const n = inFilter.length
  const years = yearsOf(facts)
  const filtered = flt.type !== 'all' || flt.mentions !== 'all' || flt.year !== 'all'

  const row = (key, options, label, counts, fmt) => (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', margin: '4px 0' }}>
      <span style={{ fontSize: fs(11), fontWeight: 700, color: s.text, minWidth: 74 }}>{label}</span>
      {['all', ...options].map((o) => {
        const on = flt[key] === o
        const c = counts[o]
        return (
          <span key={o} role="button" onClick={() => set(key, o)} style={{
            minHeight: 34, padding: '6px 10px', borderRadius: 999, fontSize: fs(12), fontWeight: on ? 700 : 500, cursor: 'pointer',
            display: 'inline-flex', alignItems: 'center', color: s.text, border: `1px solid ${on ? s.acc : s.brd}`,
            background: on ? `${s.acc}22` : 'transparent', opacity: c === 0 && !on ? 0.45 : 1,
          }}>{o === 'all' ? 'все' : fmt(o)} <span style={{ opacity: 0.7, marginLeft: 4 }}>{c}</span></span>
        )
      })}
    </div>
  )

  const act = async (kind) => {
    if (confirm !== kind) { setConfirm(kind); return }
    setConfirm(null)
    const ids = inFilter.map((f) => f.id)
    if (kind === 'approve') await onApprove(ids)
    else if (kind === 'reject') await onReject(ids)
    else await onReset(ids)
  }

  return (
    <div className="glass" style={{ padding: '10px 12px', margin: '8px 0 10px' }}>
      <div style={{ fontSize: fs(14), fontWeight: 700, color: s.text }}>
        Осталось разобрать: {pending}
        <span style={{ fontWeight: 500, fontSize: fs(12) }}> · всего фактов {total}</span>
      </div>
      <div style={{ fontSize: fs(12), color: s.text, margin: '2px 0 6px' }}>
        В текущем фильтре ({STATUS_RU[flt.status] || 'все'}{filtered ? ', с фильтрами' : ''}): <b>{n}</b>
      </div>
      {row('status', STATUSES, 'Статус', facetCounts(facts, flt, 'status', STATUSES), (o) => STATUS_RU[o])}
      {row('type', TYPES, 'Тип', facetCounts(facts, flt, 'type', TYPES), (o) => typeMeta(o).icon)}
      {row('mentions', MENTION_BUCKETS, 'Упомин.', facetCounts(facts, flt, 'mentions', MENTION_BUCKETS), (o) => `×${o}`)}
      {row('year', years, 'Год', facetCounts(facts, flt, 'year', years), (o) => o)}

      {n === 0 ? <Note s={s}>В этом фильтре ничего нет</Note> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
          {actionsFor(flt.status).map((a) => (
            <Btn key={a.kind} s={s} kind={confirm === a.kind ? (a.kind === 'reject' ? 'danger' : 'primary') : a.style} disabled={busy}
              onClick={() => act(a.kind)}>
              {confirm === a.kind ? `${a.sure} ${n}? Тап ещё раз` : `${a.label} (${n})`}
            </Btn>
          ))}
          {confirm && <div style={{ fontSize: fs(11), color: s.text }}>Обратимо: отклонённые останутся в «отклонённых», их можно вернуть на проверку.</div>}
        </div>
      )}
    </div>
  )
}

export { EMPTY_FILTER }
