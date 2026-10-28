import React, { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { Btn, Chip, Note, inputStyle } from './parts'
import {
  clientCandidates, errText, linkClient, listPeople, resolveClientBirthday, unlinkClient,
} from './peopleApi'
import { fmtBirthday, fs, openLink, typeMeta } from './ui'

const REASON = { account: 'совпал аккаунт', full_name: 'то же имя', name_birthday: 'имя и ДР', alias: 'другое имя', self: 'это ты' }

// Полоса в досье Арканы: «👥 Человек: Имя · Открыть в Людях» или «Связать с человеком».
// Клиент `self` связан с «Я» автоматически — отвязать/перевязать нельзя.
export function LinkBar({ s, Sheet, client, onChanged, onOpenPerson }) {
  const person = client.person
  const [pick, setPick] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [confirmUnlink, setConfirmUnlink] = useState(false)

  const unlink = async () => {
    setBusy(true); setErr('')
    try { await unlinkClient(client.id); setConfirmUnlink(false); onChanged?.() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div style={{ marginBottom: 12 }}>
      {person ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Chip s={s} color={s.acc}>👥 {person.is_self ? 'Я в Людях' : 'Человек'}</Chip>
          <span style={{ fontSize: fs(13), color: s.text, fontWeight: 500, wordBreak: 'break-word' }}>{person.name}</span>
          <Btn s={s} onClick={() => onOpenPerson?.(person.id)} style={{ marginLeft: 'auto' }}>Открыть в Людях</Btn>
          {!client.self && (confirmUnlink
            ? <Btn s={s} kind="danger" disabled={busy} onClick={unlink}>Отвязать?</Btn>
            : <Btn s={s} onClick={() => setConfirmUnlink(true)}>Отвязать</Btn>)}
        </div>
      ) : !client.self && (
        <Btn s={s} onClick={() => setPick(true)} style={{ width: '100%' }}>👥 Связать с человеком</Btn>
      )}
      {err && <Note s={s} color={s.red}>{err}</Note>}
      {pick && <LinkPicker s={s} Sheet={Sheet} client={client} onClose={() => setPick(false)}
        onLinked={() => { setPick(false); onChanged?.() }} />}
    </div>
  )
}

// Выбор человека для клиента: сверху «Похожие» (по контакту/имени/ДР), ниже поиск по всем.
// Ничего не связывается без подтверждения. После связи, если ДР клиента и человека разные,
// спрашиваем, какая дата верна.
function LinkPicker({ s, Sheet, client, onClose, onLinked }) {
  const [cands, setCands] = useState([])
  const [q, setQ] = useState('')
  const [found, setFound] = useState(null)
  const [pick, setPick] = useState(null)
  const [conflict, setConflict] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    clientCandidates(client.id).then((d) => setCands(d.candidates || [])).catch(() => setCands([]))
  }, [client.id])

  useEffect(() => {
    if (q.trim().length < 2) { setFound(null); return undefined }
    const t = setTimeout(() => {
      listPeople({ q: q.trim() }).then((d) => setFound((d.people || []).filter((x) => !x.is_self && x.status !== 'rejected' && !x.client_id)))
        .catch(() => setFound([]))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const doLink = async () => {
    setBusy(true); setErr('')
    try {
      const r = await linkClient(client.id, pick.id)
      if (r.birthday?.state === 'conflict') setConflict(r.birthday)
      else onLinked()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const choose = async (use) => {
    setBusy(true); setErr('')
    try { await resolveClientBirthday(client.id, use); onLinked() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const row = (p, extra) => (
    <div key={p.id} onClick={() => setPick(p)} style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', minHeight: 48, borderRadius: 12, cursor: 'pointer',
      border: `1px solid ${s.brd}`, marginBottom: 6, background: 'rgba(255,255,255,0.18)',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: fs(14), color: s.text, fontWeight: 500, wordBreak: 'break-word' }}>{p.name}</div>
        <div style={{ fontSize: fs(11), color: s.tS }}>🎂 {fmtBirthday(p.birthday)}</div>
      </div>
      {extra}
    </div>
  )

  return (
    <Sheet s={s} open onClose={onClose} title="Связать с человеком">
      {conflict ? (
        <div>
          <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>
            Связала с <b>{pick.name}</b>. Дни рождения разные — какой верный?
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
            <Btn s={s} disabled={busy} onClick={() => choose('client')}>Из клиента: {fmtBirthday(conflict.client)}</Btn>
            <Btn s={s} disabled={busy} onClick={() => choose('person')}>Из Людей: {fmtBirthday(conflict.person)}</Btn>
          </div>
          {err && <Note s={s} color={s.red}>{err}</Note>}
        </div>
      ) : pick ? (
        <div>
          <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>
            Связать клиента <b>{client.name}</b> с человеком <b>{pick.name}</b>?<br />
            <span style={{ color: s.tS }}>Один человек — один клиент. Подтверждённые факты из Людей попадут в контекст трактовок. День рождения будет храниться в карточке человека.</span>
          </div>
          {err && <Note s={s} color={s.red}>{err}</Note>}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <Btn s={s} style={{ flex: 1 }} onClick={() => setPick(null)}>Назад</Btn>
            <Btn s={s} kind="primary" style={{ flex: 1 }} disabled={busy} onClick={doLink}>{busy ? '…' : 'Связать'}</Btn>
          </div>
        </div>
      ) : (
        <div>
          {cands.length > 0 && (
            <>
              <div style={{ fontSize: fs(10), fontWeight: 600, letterSpacing: '0.4px', color: s.tM, margin: '0 0 6px' }}>ПОХОЖИЕ</div>
              {cands.map((c) => row(c, <Chip s={s} color={s.amber}>{(c.reasons || []).map((r) => REASON[r] || r).join(' · ')}</Chip>))}
            </>
          )}
          <div style={{ position: 'relative', margin: '10px 0' }}>
            <Search size={fs(15)} color={s.tM} style={{ position: 'absolute', left: 12, top: 15 }} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти человека по имени или другому имени"
              style={{ ...inputStyle(s), paddingLeft: 34 }} />
          </div>
          {found && found.length === 0 && <Note s={s}>Никого не нашла</Note>}
          {(found || []).map((p) => row(p))}
          {!cands.length && found === null && <Note s={s}>Подходящих по контакту и имени нет — найди вручную.</Note>}
        </div>
      )}
    </Sheet>
  )
}

// Read-only блок «Из Людей» в досье: ДР, аккаунты, подтверждённые факты (важные первыми).
// Править здесь нельзя — «Открыть в Людях».
export function FromPeopleBlock({ s, person }) {
  if (!person) return null
  const groups = []
  for (const f of person.facts || []) {
    const last = groups[groups.length - 1]
    if (last && last.type === f.type) last.items.push(f)
    else groups.push({ type: f.type, items: [f] })
  }
  return (
    <div className="glass" style={{ padding: '12px 14px', marginBottom: 14 }}>
      <div style={{ fontSize: fs(10), color: s.tS, marginBottom: 6 }}>👥 Из Людей</div>
      {person.birthday && <div style={{ fontSize: fs(13), color: s.text, marginBottom: 4 }}>🎂 {fmtBirthday(person.birthday)}</div>}
      {person.accounts?.length > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: fs(12), marginBottom: 6 }}>
          {person.accounts.map((a, i) => {
            const label = `${a.platform === 'vk' ? 'VK' : 'TG'} · ${a.username ? '@' + a.username : a.external_id}`
            return a.url
              ? <a key={i} href={a.url} onClick={(e) => openLink(e, a.url)} style={{ color: s.text, fontWeight: 600, textDecoration: 'underline', textDecorationStyle: 'dotted' }}>{label}</a>
              : <span key={i}>{label}</span>
          })}
        </div>
      )}
      {groups.length === 0 && <div style={{ fontSize: fs(12), color: s.tM }}>Подтверждённых фактов пока нет</div>}
      {groups.map((g) => {
        const m = typeMeta(g.type)
        return (
          <div key={g.type} style={{ marginTop: 6 }}>
            <div style={{ fontSize: fs(10), fontWeight: 600, letterSpacing: '0.4px', color: s.tM }}>{m.icon} {m.label}</div>
            {g.items.map((f) => (
              <div key={f.id} style={{ fontSize: fs(13), color: s.text, lineHeight: 1.45, padding: '2px 0', wordBreak: 'break-word' }}>{f.text}</div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
