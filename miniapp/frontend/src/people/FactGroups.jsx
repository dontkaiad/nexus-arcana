import React from 'react'
import { fs, typeMeta } from './ui'

// Группы фактов (important первым — порядок задаёт API). Тап по тексту раскрывает
// цитату-основание; управление слева/справа (галочка, кнопки) приходит рендер-пропсами.
export default function FactGroups({ s, groups, expanded, onToggle, left, right }) {
  return (
    <div>
      {groups.map((g) => {
        const m = typeMeta(g.type)
        return (
          <div key={g.type} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: fs(11), fontWeight: 700, letterSpacing: '0.5px', color: g.type === 'important' ? s.red : s.text, margin: '8px 0 2px' }}>
              {m.icon} {m.label}
            </div>
            {g.items.map((f) => {
              const open = expanded.has(f.id)
              return (
                <div key={f.id} style={{ borderBottom: `0.5px solid ${s.brd}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', minHeight: 44 }}>
                    {left && left(f, g.type)}
                    <div onClick={() => onToggle(f.id)} style={{ flex: 1, minWidth: 0, padding: '8px 4px', cursor: 'pointer' }}>
                      <div style={{ fontSize: fs(14), color: s.text, lineHeight: 1.35, wordBreak: 'break-word' }}>{f.text}</div>
                    </div>
                    {right && right(f, g.type)}
                  </div>
                  {open && (
                    <div style={{
                      margin: '0 4px 8px 44px', padding: '8px 10px', borderRadius: 10, fontSize: fs(12), lineHeight: 1.45,
                      color: s.tS, background: 'rgba(0,0,0,0.06)', wordBreak: 'break-word', fontStyle: f.quote ? 'italic' : 'normal',
                    }}>
                      {f.quote ? `«${f.quote}»` : 'Цитаты нет — факт добавлен вручную или импортирован до сохранения цитат'}
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
