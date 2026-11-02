import React, { useState } from 'react'
import { fs } from '../people/ui'

// Фото кота или заглушка 🐈 (нет photo_url или картинка не загрузилась).
export default function CatAvatar({ s, cat, size = 48, radius = 14, style }) {
  const [broken, setBroken] = useState(false)
  const box = { width: size, height: size, borderRadius: radius, flexShrink: 0, overflow: 'hidden', ...style }
  if (cat?.photo_url && !broken) {
    return <img src={cat.photo_url} alt={cat.name} onError={() => setBroken(true)}
      style={{ ...box, objectFit: 'cover', display: 'block' }} />
  }
  return (
    <div role="img" aria-label={`${cat?.name || 'Кот'}: фото нет`} data-placeholder="1"
      style={{ ...box, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: fs(Math.round(size * 0.5)),
        background: `${s.acc}22`, border: `1px dashed ${s.brd}` }}>🐈</div>
  )
}
