import React from 'react'
import { Label, inputStyle } from './parts'
import { primaryColors } from './ui'

// «Кто это»: тип связи (одним тапом, повторный тап снимает) + одна строка пояснения. Коды — как в people.relation.
export const RELATIONS = [
  { k: 'friend', l: 'друг' }, { k: 'family', l: 'семья' }, { k: 'colleague', l: 'коллега' },
  { k: 'neighbor', l: 'сосед' }, { k: 'ex', l: 'бывший' }, { k: 'other', l: 'другое' },
]

export default function RelationPicker({ s, relation, about, onRelation, onAbout }) {
  return (
    <>
      <Label s={s}>Кто это</Label>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {RELATIONS.map((r) => (
          <div key={r.k} className={`pill${relation === r.k ? ' active' : ''}`}
            onClick={() => onRelation(relation === r.k ? null : r.k)}
            style={{ minHeight: 40, color: s.text, ...(relation === r.k ? { ...primaryColors(s), fontWeight: 600 } : {}) }}>{r.l}</div>
        ))}
      </div>
      <input value={about} maxLength={120} onChange={(e) => onAbout(e.target.value)} placeholder="Пара слов: откуда знакома, чем занимается"
        style={{ ...inputStyle(s), marginTop: 8 }} />
    </>
  )
}
