import React from 'react'
import { Check } from 'lucide-react'
import { fs, openLink, sourceLinks } from './ui'

// Кнопки/поля раздела «Люди»: цель касания ≥ 44px (Telegram WebView), без жёстких ширин.
export function Btn({ s, kind = 'ghost', onClick, disabled, children, style }) {
  const base = {
    minHeight: 44, padding: '10px 14px', borderRadius: 14, fontSize: fs(13), fontWeight: 600,
    cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1,
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    textAlign: 'center', userSelect: 'none', WebkitTapHighlightColor: 'transparent',
    border: `1px solid ${s.brd}`, background: 'transparent', color: s.text,
  }
  const kinds = {
    primary: { background: s.acc, color: '#fff', border: `1px solid ${s.acc}` },
    danger: { color: s.red, border: `1px solid ${s.red}66` },
    ghost: {},
  }
  return (
    <div role="button" onClick={disabled ? undefined : onClick} style={{ ...base, ...kinds[kind], ...style }}>
      {children}
    </div>
  )
}

// Чекбокс с зоной касания 44×44, визуально 24×24.
export function Tick({ s, on, onClick, color }) {
  const c = color || s.acc
  return (
    <div onClick={onClick} role="checkbox" aria-checked={on}
      style={{ width: 44, height: 44, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
      <div style={{
        width: 24, height: 24, borderRadius: 7, display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `1.5px solid ${on ? c : s.tM}`, background: on ? c : 'transparent', color: '#fff',
      }}>
        {on && <Check size={15} strokeWidth={3} />}
      </div>
    </div>
  )
}

export function Chip({ s, color, children, style }) {
  const c = color || s.text
  const txt = color === s.good ? s.text : c      // зелёный текст на зелёном фоне не читается
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', padding: '3px 9px', borderRadius: 999,
      fontSize: fs(10), fontWeight: 600, color: txt, background: color ? `${c}22` : 'rgba(255,255,255,0.28)', border: `0.5px solid ${c}55`,
      whiteSpace: 'nowrap', ...style,
    }}>{children}</span>
  )
}

export const inputStyle = (s) => ({
  width: '100%', minHeight: 44, padding: '10px 12px', borderRadius: 12, fontSize: 16,   // 16px — iOS не зумит поле
  border: `1px solid ${s.brd}`, background: 'rgba(255,255,255,0.35)', color: s.text, outline: 'none',
  fontFamily: 'inherit', boxSizing: 'border-box',
})

export const Label = ({ s, children }) => (
  <div style={{ fontSize: fs(10), fontWeight: 600, letterSpacing: '0.4px', textTransform: 'uppercase', color: s.text, opacity: 0.75, margin: '10px 0 4px' }}>
    {children}
  </div>
)

export const Note = ({ s, children, color }) => (
  <div style={{ fontSize: fs(12), color: color || s.tS, textAlign: 'center', padding: '24px 8px', lineHeight: 1.45 }}>{children}</div>
)

// «TG · @username» / «VK · id…» — тап открывает источник (чтобы понять, кто это)
export function SourceLinks({ s, p, style }) {
  const links = sourceLinks(p)
  if (p.is_self || links.length === 0) return null
  return (
    <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', ...style }}>
      {links.map((l) => (
        <a key={l.key} href={l.href} onClick={(e) => openLink(e, l.href)}
          style={{ color: s.text, fontWeight: 600, textDecoration: 'underline', textDecorationStyle: 'dotted', textUnderlineOffset: 2 }}>
          {l.label}
        </a>
      ))}
    </span>
  )
}
