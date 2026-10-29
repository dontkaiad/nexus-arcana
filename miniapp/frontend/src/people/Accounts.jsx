import React, { useState } from 'react'
import { Btn, Chip, Label, Note, inputStyle } from './parts'
import { addAccount, errText, removeAccount, setPrimaryAccount } from './peopleApi'
import { accountAction, accountsOf, copyText, fs, haptic, linksAllowed, openExternal, openTelegramLink, primaryColors, redText } from './ui'

// Аккаунты человека (TG/VK): кликабельные t.me/<username> и vk.com/id<id>, TG только с id — тап копирует id.
// Можно добавить аккаунт, удалить (двумя тапами) и сделать основным (основной на платформу — один).
// У «Удалённого аккаунта» и у «Я» ссылок нет: аккаунты показываются текстом, управление доступно.
export default function Accounts({ s, person, onChanged }) {
  const accounts = accountsOf(person)
  const clickable = linksAllowed(person)
  const [copied, setCopied] = useState(null)
  const [adding, setAdding] = useState(false)
  const [platform, setPlatform] = useState('tg')
  const [extId, setExtId] = useState('')
  const [username, setUsername] = useState('')
  const [confirmDel, setConfirmDel] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const run = async (fn) => {
    setBusy(true); setErr('')
    try { await fn(); haptic(); onChanged?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const press = async (a) => {
    const act = accountAction(a)
    haptic()
    if (act.kind === 'tg') openTelegramLink(act.url)
    else if (act.kind === 'vk') openExternal(act.url)
    else if (await copyText(act.text)) {
      setCopied(a.id)
      setTimeout(() => setCopied((c) => (c === a.id ? null : c)), 1500)
    }
  }

  const submit = () => run(async () => {
    await addAccount(person.id, { platform, external_id: Number(extId), username: platform === 'tg' ? username.trim() || null : null })
    setExtId(''); setUsername(''); setAdding(false)
  })

  const sameCount = (plat) => accounts.filter((a) => a.platform === plat).length

  return (
    <div style={{ marginBottom: 8 }}>
      {accounts.length > 0 && <Label s={s}>Аккаунты</Label>}
      {accounts.map((a) => {
        const act = accountAction(a)
        return (
          <div key={a.id ?? `${a.platform}${a.external_id}`} style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 44, flexWrap: 'wrap' }}>
            <div role={clickable ? 'button' : undefined} onClick={clickable ? () => press(a) : undefined} style={{
              minHeight: 36, padding: '6px 12px', borderRadius: 999, display: 'inline-flex', alignItems: 'center',
              fontSize: fs(12), fontWeight: 600, color: s.text, userSelect: 'none',
              cursor: clickable ? 'pointer' : 'default', WebkitTapHighlightColor: 'transparent',
              border: `1px solid ${s.brd}`, background: clickable ? 'rgba(255,255,255,0.28)' : 'transparent',
            }}>
              {copied === a.id ? '✓ id скопирован' : act.label}
            </div>
            {a.is_primary && sameCount(a.platform) > 1 && <Chip s={s} color={s.acc}>основной</Chip>}
            {!a.is_primary && (
              <div role="button" onClick={() => run(() => setPrimaryAccount(person.id, a.id))}
                style={{ minHeight: 36, padding: '6px 10px', fontSize: fs(11), color: s.text, opacity: busy ? 0.5 : 1, cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted' }}>
                сделать основным
              </div>
            )}
            <div style={{ flex: 1 }} />
            {confirmDel === a.id ? (
              <div role="button" onClick={() => { setConfirmDel(null); run(() => removeAccount(person.id, a.id)) }}
                style={{ minHeight: 36, padding: '6px 12px', borderRadius: 999, fontSize: fs(11), fontWeight: 700, color: redText(s), border: `1px solid ${s.red}66`, cursor: 'pointer' }}>
                Удалить?
              </div>
            ) : (
              <div role="button" aria-label="удалить аккаунт" onClick={() => setConfirmDel(a.id)}
                style={{ width: 40, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', color: redText(s), fontSize: 18, fontWeight: 700, cursor: 'pointer' }}>
                ✕
              </div>
            )}
          </div>
        )
      })}
      {err && <Note s={s} color={redText(s)}>{err}</Note>}
      {adding ? (
        <div style={{ marginTop: 6 }}>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            {['tg', 'vk'].map((p) => (
              <div key={p} role="button" onClick={() => setPlatform(p)} style={{
                padding: '8px 16px', minHeight: 36, borderRadius: 999, fontSize: fs(12), fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${platform === p ? primaryColors(s).background : s.tS}`, background: platform === p ? primaryColors(s).background : 'transparent',
                color: platform === p ? primaryColors(s).color : s.text,
              }}>{p === 'tg' ? 'Telegram' : 'VK'}</div>
            ))}
          </div>
          <input value={extId} onChange={(e) => setExtId(e.target.value.replace(/\D/g, ''))} inputMode="numeric"
            placeholder={platform === 'tg' ? 'Telegram id (число)' : 'VK id (число)'} style={{ ...inputStyle(s), marginBottom: 8 }} />
          {platform === 'tg' && (
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="@username (необязательно)"
              style={{ ...inputStyle(s), marginBottom: 8 }} />
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => { setAdding(false); setErr('') }}>Отмена</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy || !extId} onClick={submit}>Добавить</Btn>
          </div>
        </div>
      ) : (
        <div role="button" onClick={() => setAdding(true)}
          style={{ minHeight: 36, padding: '6px 2px', fontSize: fs(12), fontWeight: 600, color: s.text, cursor: 'pointer' }}>
          ＋ Аккаунт
        </div>
      )}
    </div>
  )
}
