import React, { useState } from 'react'
import { TYPE_CHOICES, fs, mentionLabel, provenanceLine, redText, typeMeta } from './ui'

// Группы фактов (important первым — порядок задаёт API). Тап по тексту раскрывает
// цитату-основание; управление слева/справа (галочка, кнопки) приходит рендер-пропсами.
// onChangeType(fact, newType) — если передан, у каждого факта есть иконка типа: тап раскрывает выбор типа.
export default function FactGroups({ s, groups, expanded, onToggle, left, right, showMentions, textStyle, caption, onChangeType }) {
  const [pickFor, setPickFor] = useState(null)
  return (
    <div>
      {groups.map((g) => {
        const m = typeMeta(g.type)
        return (
          // подложка как у карточек Памяти (.glass): текст фактов не лежит прямо на градиенте
          <div key={g.type} className="glass" style={{ marginBottom: 10, padding: '4px 12px 2px' }}>
            <div style={{ fontSize: fs(11), fontWeight: 700, letterSpacing: '0.5px', color: g.type === 'important' ? redText(s) : s.text, margin: '8px 0 2px' }}>
              {m.icon} {m.label}
            </div>
            {g.items.map((f) => {
              const open = expanded.has(f.id)
              return (
                <div key={f.id} style={{ borderBottom: `0.5px solid ${s.brd}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', minHeight: 44 }}>
                    {left && left(f, g.type)}
                    {onChangeType && (
                      <div role="button" aria-label="сменить тип" onClick={() => setPickFor(pickFor === f.id ? null : f.id)}
                        style={{ width: 32, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, fontSize: fs(15) }}>
                        {m.icon}
                      </div>
                    )}
                    <div onClick={() => onToggle(f.id)} style={{ flex: 1, minWidth: 0, padding: '8px 4px', cursor: 'pointer' }}>
                      <div style={{ fontSize: fs(14), color: s.text, lineHeight: 1.35, wordBreak: 'break-word', ...(textStyle ? textStyle(f) : {}) }}>{f.text}</div>
                      {showMentions && (
                        <div style={{ fontSize: fs(10), color: s.text, marginTop: 2, fontWeight: 600 }}>{mentionLabel(f)}</div>
                      )}
                    </div>
                    {right && right(f, g.type)}
                  </div>
                  {onChangeType && pickFor === f.id && (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '0 4px 8px 36px' }}>
                      {TYPE_CHOICES.map((t) => {
                        const tm = typeMeta(t)
                        const cur = t === g.type
                        return (
                          <div key={t} role="button" onClick={() => { setPickFor(null); if (!cur) onChangeType(f, t) }}
                            style={{ minHeight: 36, padding: '0 10px', display: 'inline-flex', alignItems: 'center', gap: 4, borderRadius: 18, cursor: 'pointer',
                              fontSize: fs(12), fontWeight: 600, color: s.text, border: `1px solid ${cur ? s.acc : s.brd}`, opacity: cur ? 1 : 0.85 }}>
                            {tm.icon} {tm.label.toLowerCase()}
                          </div>
                        )
                      })}
                    </div>
                  )}
                  {caption && caption(f)}
                  {open && (
                    <div style={{
                      margin: '0 4px 8px 44px', padding: '8px 10px', borderRadius: 10, fontSize: fs(12), lineHeight: 1.45,
                      color: s.text, background: 'rgba(0,0,0,0.06)', wordBreak: 'break-word', fontStyle: f.quote ? 'italic' : 'normal',
                    }}>
                      {f.quote ? `«${f.quote}»` : 'Цитаты нет — факт добавлен вручную или импортирован до сохранения цитат'}
                      {provenanceLine(f) && (
                        <div style={{ fontStyle: 'normal', fontWeight: 600, marginTop: 4, fontSize: fs(11) }}>{provenanceLine(f)}</div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
