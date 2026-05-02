// Screen components
const { useState: useStateS } = React;

// ─── Common bits ───────────────────────────────
window.Pill = function Pill({ active, onClick, children }) {
  return (
    <div className={`pill ${active ? "active" : ""}`} onClick={onClick}>
      {children}
    </div>
  );
};

window.Chk = function Chk({ done, onClick }) {
  return (
    <div className={`chk ${done ? "done" : ""}`} onClick={onClick}>
      {done && <Icon name="check" size={13} />}
    </div>
  );
};

window.PrioDot = function PrioDot({ prio, mode }) {
  const colors = mode === "day"
    ? { high: "#b04a3a", medium: "#b8822a", low: "#7a8580" }
    : { high: "#c4654a", medium: "#d4a458", low: "#6e6a62" };
  return <span className="prio-dot" style={{ background: colors[prio] || colors.low }} />;
};

window.Metric = function Metric({ v, u, l, accent }) {
  return (
    <div className="metric">
      <div className="v" style={accent ? { color: accent } : undefined}>
        {v}{u && <span className="u">{u}</span>}
      </div>
      <div className="l">{l}</div>
    </div>
  );
};

window.Bar = function Bar({ pct, color }) {
  return (
    <div className="bar">
      <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: color }} />
    </div>
  );
};

window.SectionH = function SectionH({ children, meta }) {
  return (
    <div className="section-h">
      <span>{children}</span>
      {meta && <span className="meta">{meta}</span>}
    </div>
  );
};

window.Empty = function Empty({ icon, title, sub }) {
  return (
    <div className="glass empty">
      {icon && <div className="e-icon">{icon}</div>}
      {title && <div className="e-title">{title}</div>}
      {sub && <div className="e-sub">{sub}</div>}
    </div>
  );
};

window.SearchInput = function SearchInput({ value, onChange, placeholder }) {
  return (
    <div className="search">
      <Icon name="search" size={16} style={{ opacity: 0.55 }} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
};

window.TaskRow = function TaskRow({ t, mode, done, onToggle, withTime, accent }) {
  const overdue = t.days != null && t.days > 0;
  return (
    <div className="task glass" style={accent ? { "--accent": accent } : undefined}>
      {withTime && t.time && <span className="time">{t.time}</span>}
      <Chk done={done} onClick={onToggle} />
      <div className="body">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className={`title ${done ? "" : ""}`}>{t.title}</span>
        </div>
        <div className="meta">
          {overdue && <span style={{ color: mode === "day" ? "#b04a3a" : "#c4654a", fontWeight: 600 }}>{t.days} д назад</span>}
          {t.date && !overdue && <span>{t.date}</span>}
          {t.rpt && <span>🔄 {t.rpt}</span>}
          {t.streak > 0 && <span>🔥 {t.streak}</span>}
        </div>
      </div>
      <div className="cat-badge">{t.cat}</div>
      <PrioDot prio={t.prio} mode={mode} />
    </div>
  );
};

// ─── Nexus: Day ────────────────────────────────
window.NxDay = function NxDay({ mode, data, navigate }) {
  const [done, setDone] = useStateS({});
  const t = data.today;
  const totalTasks = t.scheduled.length + t.overdue.length;
  const doneCount = Object.values(done).filter(Boolean).length;
  const leftPct = Math.round((t.spentDay / t.budgetDay) * 100);
  const free = Math.round((t.budgetDay - t.spentDay) / 1000);

  const accents = mode === "day"
    ? { ok: "#4a7a5e", warn: "#b8822a", bad: "#b04a3a" }
    : { ok: "#6fb88e", warn: "#d4a458", bad: "#c4654a" };
  const budgetCol = leftPct > 85 ? accents.bad : leftPct > 60 ? accents.warn : accents.ok;
  const wIco = { rain: "🌧", snow: "❄️", cloudy: "⛅", clear: "☀️", fog: "🌫" }[t.weather.kind] || "☀️";

  return (
    <>
      <div className="hero glass glow">
        <div className="hero-h">
          <div>
            <div className="hero-title">Мой день</div>
            <div style={{ fontSize: 13, opacity: 0.7, marginTop: 4, fontWeight: 500 }}>
              {t.weather.condition.toLowerCase()}, чашка чая не помешает
            </div>
          </div>
          <div className="hero-meta">
            <div>{t.date}</div>
            <div style={{ marginTop: 3 }}>{wIco} {t.weather.temp > 0 ? "+" : ""}{t.weather.temp}° · {t.weather.city}</div>
          </div>
        </div>
        <div className="hero-metrics">
          <Metric v={doneCount} u={`/${totalTasks}`} l="задачи" />
          <Metric v={`${free}к`} u="₽" l="свободно" accent={budgetCol} />
          <Metric v={<span className="streak-v"><svg className="flame" viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M13.5 1.5c.3 2.4-.4 4.4-2.1 6-1.7 1.6-2.6 3.4-2.6 5.4 0 1.4.5 2.6 1.4 3.5-.6-.1-1.2-.4-1.8-.9-1.5-1.3-2.3-3.1-2.3-5.2-1.4 1.5-2.1 3.4-2.1 5.6 0 2.3.8 4.3 2.4 5.9C7.9 23.4 9.8 24 12 24c2.4 0 4.4-.8 6-2.4 1.6-1.6 2.4-3.6 2.4-6 0-3-1.1-5.7-3.4-8.2-2-2.1-3.2-4.1-3.5-5.9z"/></svg>{t.streak}</span>} l="стрик" accent={mode === "day" ? "#c8884a" : "#d4a458"} />
        </div>
        <div className="hero-budget">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
            <span style={{ opacity: 0.75 }}>Бюджет дня</span>
            <span style={{ color: budgetCol, fontWeight: 700 }}>{t.budgetDay.toLocaleString()} ₽ · {leftPct}%</span>
          </div>
          <Bar pct={leftPct} color={budgetCol} />
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
            потрачено {t.spentDay.toLocaleString()} ₽ из {t.budgetDay.toLocaleString()} ₽
          </div>
        </div>
      </div>

      <div className="tip" style={{ marginTop: 10 }}>
        <div className="tip-h">
          <span>🦋 СДВГ-совет</span>
          <Icon name="refresh" size={15} style={{ cursor: "pointer", opacity: 0.6 }} />
        </div>
        <div className="tip-body">
          {t.adhdTip.split(/\*\*(.+?)\*\*/g).map((p, i) =>
            i % 2 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>
          )}
        </div>
      </div>

      <SectionH meta={`${t.overdue.length + t.scheduled.length} пунктов`}>Расписание</SectionH>
      {t.overdue.map((o) => (
        <TaskRow key={o.id} t={o} mode={mode} done={!!done[o.id]}
          onToggle={() => setDone((p) => ({ ...p, [o.id]: !p[o.id] }))}
          accent={accents.bad} />
      ))}
      {t.scheduled.map((sc) => (
        <TaskRow key={sc.id} t={sc} mode={mode} done={!!done[sc.id]}
          onToggle={() => setDone((p) => ({ ...p, [sc.id]: !p[sc.id] }))}
          withTime accent={accents.ok} />
      ))}
    </>
  );
};

// ─── Nexus: Tasks ──────────────────────────────
window.NxTasks = function NxTasks({ mode, data }) {
  const [filter, setFilter] = useStateS("active");
  const [done, setDone] = useStateS({});
  const filtered = data.tasks.filter((t) =>
    filter === "all" ? true :
    filter === "active" ? t.status === "active" :
    filter === "overdue" ? t.status === "overdue" :
    filter === "done" ? t.status === "done" : true
  );
  return (
    <>
      <SectionH>Задачи</SectionH>
      <div className="pills" style={{ marginBottom: 10 }}>
        {[["all","Все"],["active","Активные"],["overdue","Просрочено"],["done","Выполнено"]].map(([k, l]) => (
          <Pill key={k} active={filter === k} onClick={() => setFilter(k)}>{l}</Pill>
        ))}
      </div>
      {filtered.map((t) => (
        <TaskRow key={t.id} t={t} mode={mode} done={!!done[t.id]}
          onToggle={() => setDone((p) => ({ ...p, [t.id]: !p[t.id] }))} />
      ))}
    </>
  );
};

// ─── Nexus: Finance ────────────────────────────
window.NxFin = function NxFin({ mode, data }) {
  const [tab, setTab] = useStateS("today");
  const t = data.today;
  const leftPct = Math.round((t.spentDay / t.budgetDay) * 100);
  return (
    <>
      <SectionH>Финансы</SectionH>
      <div className="pills" style={{ marginBottom: 12 }}>
        {[["today","Сегодня"],["month","Месяц"],["limits","Лимиты"],["goals","Цели"]].map(([k, l]) => (
          <Pill key={k} active={tab === k} onClick={() => setTab(k)}>{l}</Pill>
        ))}
      </div>
      {tab === "today" && (
        <>
          <div className="glass" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 18px" }}>
            <div>
              <div style={{ fontSize: 13, opacity: 0.65, fontWeight: 500 }}>Потрачено сегодня</div>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 36, fontWeight: 500, marginTop: 4 }}>
                {t.spentDay.toLocaleString()} <span style={{ fontSize: 22, opacity: 0.5 }}>₽</span>
              </div>
            </div>
            <div style={{ fontSize: 36, opacity: 0.3 }}>💰</div>
          </div>
          <div className="glass" style={{ padding: "14px 18px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>Бюджет дня</span>
              <span style={{ fontWeight: 700 }}>{t.budgetDay.toLocaleString()} ₽ · {leftPct}%</span>
            </div>
            <Bar pct={leftPct} color={mode === "day" ? "#4a7a5e" : "#8aa8c8"} />
            <div style={{ fontSize: 13, opacity: 0.65, marginTop: 8 }}>
              Потрачено {t.spentDay.toLocaleString()} ₽ · осталось {(t.budgetDay - t.spentDay).toLocaleString()} ₽
            </div>
          </div>
          {t.spentDay === 0 ? (
            <>
              <SectionH>Транзакции</SectionH>
              <Empty icon="💚" title="Пока не тратила" sub="Сегодня без трат — приятно." />
            </>
          ) : (
            <SectionH>Транзакции</SectionH>
          )}
        </>
      )}
      {tab === "limits" && data.finance.cats.map((c, i) => {
        const pct = Math.round((c.spent / c.limit) * 100);
        const col = pct > 90 ? "#b04a3a" : pct > 70 ? "#b8822a" : "#4a7a5e";
        return (
          <div key={i} className="glass" style={{ padding: "12px 16px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontWeight: 600 }}>{c.emoji} {c.name}</span>
              <span style={{ color: col, fontWeight: 600, fontSize: 13 }}>
                {c.spent.toLocaleString()} / {c.limit.toLocaleString()} ₽
              </span>
            </div>
            <Bar pct={pct} color={col} />
          </div>
        );
      })}
      {(tab === "month" || tab === "goals") && (
        <Empty icon={tab === "month" ? "📊" : "🎯"} title="Скоро" sub="Этот раздел в разработке" />
      )}
    </>
  );
};

// ─── Nexus: Lists ──────────────────────────────
window.NxLists = function NxLists({ mode, data }) {
  const [tab, setTab] = useStateS("buy");
  const [q, setQ] = useStateS("");
  const [overrides, setOverrides] = useStateS({});
  const tags = [["buy","🛒 Покупки"],["check","📋 Чеклист"],["inv","📦 Инвентарь"]];
  const groups = [
    { name: "Здоровье", items: data.lists.health },
    { name: "Прочее", items: data.lists.misc },
    { name: "Хобби/Учёба", items: data.lists.hobby },
  ];
  const toggle = (id) => setOverrides((p) => ({ ...p, [id]: !p[id] }));
  return (
    <>
      <SectionH>Списки</SectionH>
      <div className="pills" style={{ marginBottom: 8 }}>
        {tags.map(([k, l]) => <Pill key={k} active={tab === k} onClick={() => setTab(k)}>{l}</Pill>)}
      </div>
      <SearchInput value={q} onChange={setQ} placeholder="Поиск" />
      {groups.map((g) => (
        <React.Fragment key={g.name}>
          <SectionH>{g.name}</SectionH>
          {g.items.map((it) => {
            const isDone = overrides[it.id] !== undefined ? overrides[it.id] : it.done;
            return (
              <div key={it.id} className="glass" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", opacity: isDone ? 0.5 : 1 }} onClick={() => toggle(it.id)}>
                <Chk done={isDone} />
                <span style={{ flex: 1, fontSize: 16, fontWeight: 500, textDecoration: isDone ? "line-through" : "none" }}>{it.name}</span>
                <div className="cat-badge">{it.emoji}</div>
              </div>
            );
          })}
        </React.Fragment>
      ))}
    </>
  );
};

// ─── Nexus: Memory ─────────────────────────────
window.NxMemory = function NxMemory({ mode, data }) {
  const [cat, setCat] = useStateS("all");
  const [q, setQ] = useStateS("");
  const cats = ["all", ...new Set(data.memory.map((m) => m.cat))];
  const filtered = data.memory.filter((m) =>
    (cat === "all" || m.cat === cat) &&
    (!q || m.text.toLowerCase().includes(q.toLowerCase()))
  );
  return (
    <>
      <SectionH>Память</SectionH>
      <div className="glass tip" style={{ marginBottom: 8 }}>
        <div className="tip-h" style={{ marginBottom: 2 }}>🦋 СДВГ-профиль</div>
        <div style={{ fontSize: 14, fontWeight: 500 }}>Персональные паттерны и стратегии</div>
      </div>
      <SearchInput value={q} onChange={setQ} placeholder="Поиск по памяти" />
      <div className="pills" style={{ margin: "10px 0" }}>
        {cats.map((c) => (
          <Pill key={c} active={cat === c} onClick={() => setCat(c)}>{c === "all" ? "Все" : c}</Pill>
        ))}
      </div>
      {filtered.map((m, i) => (
        <div key={i} className="glass" style={{ padding: "12px 16px" }}>
          <div style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.4 }}>{m.text}</div>
          <div style={{ fontSize: 12, opacity: 0.55, marginTop: 4, fontWeight: 500 }}>{m.cat}</div>
        </div>
      ))}
    </>
  );
};

// ─── Nexus: Calendar ───────────────────────────
window.NxCal = function NxCal({ mode, data }) {
  const [view, setView] = useStateS("month");
  const [picked, setPicked] = useStateS(1);
  const days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
  const cells = [];
  // May 2026: 1st = Friday → start offset 4
  const startOffset = 4;
  for (let i = 0; i < startOffset; i++) cells.push(null);
  for (let d = 1; d <= 31; d++) cells.push(d);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "10px 4px 14px" }}>
        <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 28, fontWeight: 400 }}>Май 2026</div>
        <div className="pills">
          <Pill active={view === "week"} onClick={() => setView("week")}>Неделя</Pill>
          <Pill active={view === "month"} onClick={() => setView("month")}>Месяц</Pill>
        </div>
      </div>
      <div className="glass" style={{ padding: "10px 12px" }}>
        <div className="cal-grid">
          {days.map((d) => <div key={d} className="cal-head">{d}</div>)}
          {cells.map((d, i) => {
            if (!d) return <div key={i} className="cal-cell empty" />;
            const isToday = d === 1;
            const isPicked = d === picked;
            return (
              <div key={i} className={`cal-cell ${isToday ? "today" : ""} ${isPicked ? "picked" : ""}`} onClick={() => setPicked(d)}>
                {d}
              </div>
            );
          })}
        </div>
      </div>
      <SectionH>{picked} мая</SectionH>
      <Empty icon="📅" title="В этот день всё свободно" sub="Можно отдыхать или строить планы" />
    </>
  );
};

// ─── Arcana: Day ───────────────────────────────
window.ArDay = function ArDay({ mode, data }) {
  const a = data.arcanaToday;
  const m = a.moon;
  return (
    <>
      <div className="hero glass glow">
        <div className="hero-h">
          <div>
            <div className="hero-title">Мой день</div>
            <div style={{ fontSize: 13, opacity: 0.65, marginTop: 4, fontWeight: 500 }}>тишина практики</div>
          </div>
          <div className="hero-meta">{a.date}</div>
        </div>
        <div className="hero-metrics">
          <Metric v={a.sessionsToday.length} l="сеансов" />
          <Metric v={a.unchecked30d} l="не провер." accent={a.unchecked30d > 0 ? "#d4a458" : undefined} />
          <Metric v={`${a.accuracy}%`} l="точность" accent="var(--ar-acc)" />
        </div>
      </div>

      <div className="moon-hero glass glow" style={{ marginTop: 10, "--accent": "var(--ar-acc)" }}>
        <div className="glyph">{m.glyph}</div>
        <div className="info">
          <div className="name">{m.name}</div>
          <div className="sub">{m.days} день цикла · освещение {m.illum}%</div>
        </div>
      </div>

      <SectionH>Статистика за {a.monthBlock.label}</SectionH>
      <div className="grid-2">
        {[
          { ico: "💰", v: `${a.monthBlock.inc}₽`, l: "Доход", accent: "var(--ar-acc)" },
          { ico: "🕯️", v: `${a.monthBlock.supplies}₽`, l: "Расходники" },
          { ico: "✨", v: `${a.monthBlock.accuracy}%`, l: "Сбылось", accent: "var(--ar-acc)" },
          { ico: "🃏", v: a.monthBlock.sessions, l: "Сеансов" },
        ].map((s, i) => (
          <div key={i} className="glass" style={{ padding: "16px 12px", textAlign: "center" }}>
            <div style={{ fontSize: 22, marginBottom: 6 }}>{s.ico}</div>
            <div style={{ fontFamily: "var(--f-display)", fontSize: 24, fontWeight: 500, color: s.accent }}>{s.v}</div>
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4, fontWeight: 500 }}>{s.l}</div>
          </div>
        ))}
      </div>
      <div style={{ textAlign: "center", padding: "20px 0 4px", fontStyle: "italic", fontSize: 14, opacity: 0.55, fontFamily: "var(--f-display)" }}>
        Сегодня в практике спокойно 🌙
      </div>
    </>
  );
};

// ─── Arcana: Sessions ──────────────────────────
window.ArSessions = function ArSessions({ mode, data }) {
  const [f, setF] = useStateS("all");
  const list = f === "all" ? data.sessions : data.sessions.filter((s) => s.area === f);
  const areas = ["all", ...new Set(data.sessions.map((s) => s.area))];
  const labels = { all: "Все", "Общая ситуация": "Общая ситуация", "Работа": "Работа", "Отношения": "Отношения" };
  const unchecked = data.sessions.filter((s) => s.status === "unchecked").length;
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", margin: "10px 4px 10px" }}>
        <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 28 }}>Расклады</div>
        {unchecked > 0 && <span style={{ fontSize: 13, color: "#d4a458", fontWeight: 600 }}>⏳ {unchecked} непроверено</span>}
      </div>
      <div className="pills" style={{ marginBottom: 10 }}>
        {areas.map((a) => <Pill key={a} active={f === a} onClick={() => setF(a)}>{labels[a] || a}</Pill>)}
      </div>
      {list.map((x) => (
        <div key={x.id} className="glass tap" style={{ padding: "14px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
            <div className="flex-grow">
              <div style={{ fontFamily: "var(--f-display)", fontSize: 18, fontWeight: 500, lineHeight: 1.2 }}>{x.q}</div>
              <div style={{ fontSize: 12, opacity: 0.65, marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                <span>🔺 {x.type.replace("🔺 ","")}</span>
                <span>·</span>
                <span>{x.deck}</span>
                <span>·</span>
                <span>{x.client}</span>
                <span>·</span>
                <span>{x.date}</span>
              </div>
              <div style={{ fontSize: 13, fontStyle: "italic", marginTop: 6, opacity: 0.75, fontFamily: "var(--f-display)" }}>
                {x.cards.map((c) => c.name).join(", ")}
              </div>
            </div>
            <span style={{ fontSize: 18, marginLeft: 10 }}>⏳</span>
          </div>
        </div>
      ))}
    </>
  );
};

// ─── Arcana: Clients ───────────────────────────
window.ArClients = function ArClients({ mode, data }) {
  return (
    <>
      <div className="hero glass glow">
        <div className="hero-title" style={{ marginBottom: 14 }}>Клиенты</div>
        <div className="hero-metrics">
          <Metric v={data.clients.length} l="всего" />
          <Metric v="0" l="долги" />
        </div>
      </div>
      {data.clients.map((c) => (
        <div key={c.id} className="glass tap" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", marginTop: 8 }}>
          <div style={{ width: 40, height: 40, borderRadius: "50%", background: "rgba(138,168,200,0.2)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--f-display)", fontSize: 17, fontWeight: 500, color: "#8aa8c8" }}>
            {c.initial}
          </div>
          <div className="flex-grow">
            <div style={{ fontSize: 15, fontWeight: 600 }}>🟢 Активный {c.name}</div>
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>{c.sessions} сеансов · {c.rituals} ритуалов</div>
          </div>
          <Icon name="chevron-r" size={18} style={{ opacity: 0.4 }} />
        </div>
      ))}
    </>
  );
};

// ─── Arcana: Rituals ──────────────────────────
window.ArRituals = function ArRituals({ mode, data }) {
  const [g, setG] = useStateS("all");
  return (
    <>
      <SectionH>Ритуалы</SectionH>
      <div className="pills" style={{ marginBottom: 10 }}>
        <Pill active={g === "all"} onClick={() => setG("all")}>Все цели</Pill>
        <Pill active={g === "clean"} onClick={() => setG("clean")}>🌊 Очищение</Pill>
      </div>
      {data.rituals.map((r) => (
        <div key={r.id} className="glass tap" style={{ padding: "14px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
            <div className="flex-grow">
              <div style={{ fontFamily: "var(--f-display)", fontSize: 18, fontWeight: 500 }}>{r.name}</div>
              <div style={{ fontSize: 12, opacity: 0.65, marginTop: 6 }}>
                {r.goal} · {r.place} · {r.type} · {r.date}
              </div>
            </div>
            <span style={{ fontSize: 18 }}>⏳</span>
          </div>
        </div>
      ))}
    </>
  );
};

// ─── Arcana: Grimoire ─────────────────────────
window.ArGrimoire = function ArGrimoire({ mode, data }) {
  const [q, setQ] = useStateS("");
  return (
    <>
      <SectionH>Гримуар</SectionH>
      <SearchInput value={q} onChange={setQ} placeholder="Поиск в гримуаре" />
      <div className="pills" style={{ margin: "10px 0" }}>
        <Pill active>Все</Pill>
      </div>
      <Empty icon="📖" title="Гримуар пуст" sub="Записи о колодах и картах появятся тут." />
    </>
  );
};

// ─── Arcana: Stats ────────────────────────────
window.ArStats = function ArStats({ mode, data }) {
  return (
    <>
      <SectionH>Точность</SectionH>
      <div className="glass tip" style={{ marginBottom: 10 }}>
        <div className="tip-h" style={{ color: "#d4a458" }}>
          <span>❗ Ждут проверки</span>
          <span>{data.stats.unchecked.length}</span>
        </div>
        {data.stats.unchecked.map((s) => (
          <div key={s.id} className="glass" style={{ padding: "10px 12px", marginTop: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{s.q}</div>
            <div style={{ fontSize: 12, opacity: 0.6, margin: "2px 0 8px" }}>{s.client} · {s.date}</div>
            <div style={{ display: "flex", gap: 6 }}>
              <div style={{ flex: 1, padding: "8px", textAlign: "center", borderRadius: 8, background: "rgba(111,184,142,0.18)", color: "#6fb88e", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>✅ Сбылось</div>
              <div style={{ flex: 1, padding: "8px", textAlign: "center", borderRadius: 8, background: "rgba(212,164,88,0.18)", color: "#d4a458", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>〰 Частично</div>
              <div style={{ flex: 1, padding: "8px", textAlign: "center", borderRadius: 8, background: "rgba(196,101,74,0.18)", color: "#c4654a", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>❌ Нет</div>
            </div>
          </div>
        ))}
      </div>

      <div className="glass glow" style={{ textAlign: "center", padding: "26px 16px" }}>
        <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 6, fontWeight: 500, letterSpacing: 0.5, textTransform: "uppercase" }}>
          Общий процент сбывшихся раскладов
        </div>
        <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 64, fontWeight: 500, color: "#8aa8c8", lineHeight: 1 }}>
          {data.stats.pct}%
        </div>
        <div style={{ fontSize: 12, opacity: 0.55, marginTop: 8 }}>
          за всё время · {data.stats.allVer} проверенных
        </div>
      </div>

      <div className="glass" style={{ padding: "14px 16px", marginTop: 8 }}>
        <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 16, opacity: 0.65, marginBottom: 10 }}>
          Финансы практики · апрель
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Metric v="0к" l="доход" accent="#8aa8c8" />
          <Metric v="0к" l="расход" />
          <Metric v="0к" l="прибыль" accent="#8aa8c8" />
        </div>
      </div>

      <SectionH>По месяцам</SectionH>
      {data.stats.months.map((m, i) => (
        <div key={i} className="glass" style={{ padding: "12px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 18, fontWeight: 500 }}>{m.name}</span>
            <span style={{ color: "#8aa8c8", fontWeight: 700, fontSize: 18 }}>{m.total > 0 ? Math.round((m.yes / m.total) * 100) : 0}%</span>
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: 12 }}>
            <span style={{ color: "#6fb88e" }}>✓ {m.yes}</span>
            <span style={{ color: "#d4a458" }}>~ {m.partial}</span>
            <span style={{ color: "#c4654a" }}>✗ {m.no}</span>
            <span style={{ marginLeft: "auto", opacity: 0.55 }}>всего: {m.total}</span>
          </div>
        </div>
      ))}
    </>
  );
};
