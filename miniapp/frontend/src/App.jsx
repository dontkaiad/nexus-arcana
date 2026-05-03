import React, { useState, useRef, useMemo, useEffect } from "react";
import { createPortal } from "react-dom";
import DOMPurify from "dompurify";
import './newdesign.css'
import { useApi } from "./hooks/useApi";
import {
  adaptToday, adaptArcanaToday,
  adaptTasks, adaptFinanceToday, adaptFinanceMonth, adaptFinanceLimits, adaptFinanceGoals,
  adaptLists, adaptMemory, adaptAdhd, adaptCalendar,
  adaptSessions, adaptSessionDetail, adaptSessionGroup,
  adaptClients, adaptClientDossier,
  adaptRituals, adaptRitualDetail,
  adaptGrimoire, adaptGrimoireDetail,
  formatMonth, formatDate, formatShortDate,
} from "./adapters";
import { apiGet, apiPost } from "./api";
import { SelfListCard, SelfDetailHeader } from "./components/self/SelfClientCard.jsx";
import {
  Sun, Moon as LucideMoon, Check, Coins, List as ListIcon, Brain, Calendar,
  Sparkles as LucideSparkles, Users, Flame as LucideFlame, BookOpen as LucideBookOpen,
  Plus, Search,
  Bell, RefreshCw, X, Camera, Mic, Pencil, ChevronRight, ChevronDown,
  Wallet, HeartPulse, StickyNote, Candy, Trash2, Clock,
} from "lucide-react";
import {
  Moon as PhMoon,
  Sparkle as PhSparkle,
  Flame as PhFlame,
  BookOpen as PhBookOpen,
} from "@phosphor-icons/react";

const Moon = ({ strokeWidth, stroke, ...rest }) => <PhMoon {...rest} weight="duotone" />;
const Sparkles = ({ strokeWidth, stroke, ...rest }) => <PhSparkle {...rest} weight="duotone" />;
const Flame = ({ strokeWidth, stroke, ...rest }) => <PhFlame {...rest} weight="duotone" />;
const BookOpen = ({ strokeWidth, stroke, ...rest }) => <PhBookOpen {...rest} weight="duotone" />;

// ═══════════════════════════════════════════════════════════════
// NEXUS × ARCANA — MINI APP v7
// Изменения vs v6:
//   #1 FAB меню: grid 2×4 с большими иконками
//   #2 Контраст pills поднят, tM осветлён, текст контрастнее
//   #3 Календарь дефолт=месяц. Фаза луны в хедере Nexus Cal неформально,
//       в Арканском Мой день — большим блоком
//   #4 FAB разделён Nexus/Arcana по составу действий
//   #5 Иконки → lucide-react (`npm i lucide-react`)
//   #6 Arcana: таб «Мой день», Клиент-досье как отдельный sheet (по v2)
//   #7 Шапка — PNG-аватарки из /public/nexus.png и /public/arcana.png
//   #8 Стекло — отложено до v8
// ═══════════════════════════════════════════════════════════════

const lerp = (a, b, t) => a + (b - a) * t;
const lerpC = (c1, c2, t) => {
  const p = (c, i) => parseInt(c.replace("#", "").slice(i, i + 2), 16);
  return `#${[0, 2, 4]
    .map((i) => Math.round(lerp(p(c1, i), p(c2, i), t)).toString(16).padStart(2, "0"))
    .join("")}`;
};
// wave8: трактовки таро хранятся в нормализованном HTML. Разрешённый
// allowlist — h3/b/i/p/br. Никаких атрибутов, классов, инлайн-стилей.
function sanitizeHtml(raw) {
  if (!raw) return "";
  return DOMPurify.sanitize(String(raw), {
    ALLOWED_TAGS: ["h3", "b", "i", "p", "br"],
    ALLOWED_ATTR: [],
  });
}

const getOrb = (progress, isSun) => {
  const t = isSun ? progress : 1 - progress;
  const a = Math.PI * 0.15 + t * Math.PI * 0.7;
  // wave5.6: коэффициент сужен c 0.58 до 0.42, чтобы луна/солнце не уезжали за край
  const x = (0.5 + 0.42 * Math.cos(a)) * 100;
  const y = (0.92 - 0.82 * Math.sin(a)) * 100;
  return { x: Math.max(8, Math.min(92, x)), y };
};

function getSky(p) {
  let d, m, w, g, b, card, text, tS, tM, acc, brd, red, amber, good;
  red = lerpC("#bf5a4a", "#c45a5a", p);
  amber = lerpC("#b8822a", "#d4a458", p);
  good = lerpC("#6b8f71", "#5a9a78", p);
  if (p < 0.3) {
    const t = p / 0.3;
    d = lerpC("#4a7a78", "#3a6a72", t);
    m = lerpC("#5a8a7a", "#5a7a88", t);
    w = lerpC("#8ab4a0", "#c4a060", t);
    g = lerpC("#c4c898", "#d4884a", t);
    b = lerpC("#dce8dc", "#e8dcc8", t);
    // Nexus day: согласовано с CSS --nx-text (#1f4a3a)
    text = "#1f4a3a";
    tS = "#2d6a50";
    tM = "#4a7a62";
    acc = lerpC("#6b8f71", "#7a9068", t);
    brd = "rgba(160,154,142,0.35)";
    // wave8.0.1: glass — баланс между стеклом и читаемостью (0.28–0.34)
    card = `rgba(255,255,255,${(0.28 + t * 0.06).toFixed(2)})`;
    brd = "rgba(255,255,255,0.32)";
  } else if (p < 0.6) {
    const t = (p - 0.3) / 0.3;
    d = lerpC("#3a6a72", "#2a3a5a", t);
    m = lerpC("#5a7a88", "#3a4a6a", t);
    w = lerpC("#c4a060", "#c46040", t);
    g = lerpC("#d4884a", "#a04048", t);
    b = lerpC("#e8dcc8", "#2a2838", t);
    text = lerpC("#1f4a3a", "#d4ccc0", t);
    tS = lerpC("#2d6a50", "#b0a898", t);
    tM = lerpC("#4a7a62", "#807868", t);
    acc = lerpC("#7a9068", "#5a8a80", t);
    brd = `rgba(${Math.round(lerp(255, 60, t))},${Math.round(lerp(255, 65, t))},${Math.round(
      lerp(255, 80, t)
    )},${lerp(0.28, 0.42, t).toFixed(2)})`;
    // wave8.0.1: плавный переход от стеклянного (Nexus) к сумеречному (Arcana)
    card = `rgba(${Math.round(lerp(255, 22, t))},${Math.round(lerp(255, 27, t))},${Math.round(
      lerp(255, 40, t)
    )},${lerp(0.34, 0.62, t).toFixed(2)})`;
  } else {
    const t = (p - 0.6) / 0.4;
    d = lerpC("#2a3a5a", "#0a0e18", t);
    m = lerpC("#3a4a6a", "#101828", t);
    w = lerpC("#c46040", "#1a2240", t);
    g = lerpC("#a04048", "#2a1a3a", t);
    b = lerpC("#2a2838", "#0e1119", t);
    text = "#e0d8cc";
    tS = "#b0a898";
    tM = "#807868";
    acc = lerpC("#5a8a80", "#5a9a8a", t);
    brd = "rgba(90,95,110,0.42)";
    card = `rgba(22,27,40,${(0.62 + t * 0.16).toFixed(2)})`;
  }
  return { deep: d, mid: m, warm: w, glow: g, base: b, card, text, tS, tM, acc, brd, red, amber, good };
}

// Фаза луны
const MOON_GLYPHS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"];
const MOON_NAMES = [
  "Новолуние",
  "Растущий серп",
  "Первая четверть",
  "Растущая луна",
  "Полнолуние",
  "Убывающая луна",
  "Последняя четверть",
  "Убывающий серп",
];
function moonPhase(dt = new Date()) {
  const knownNew = new Date(Date.UTC(2000, 0, 6, 18, 14)).getTime();
  const synodic = 29.530588853 * 86400000;
  const diff = dt.getTime() - knownNew;
  const frac = ((diff % synodic) + synodic) % synodic / synodic;
  const idx = Math.floor(frac * 8 + 0.5) % 8;
  const days = Math.round(frac * 29.53);
  const illum = Math.round((1 - Math.cos(frac * 2 * Math.PI)) * 50);
  return { idx, glyph: MOON_GLYPHS[idx], name: MOON_NAMES[idx], days, illum, frac };
}

const H = "'Newsreader', 'Lora', Georgia, serif";
const B = "'Manrope', -apple-system, 'SF Pro Text', system-ui, sans-serif";
const FONT_MONO = "'JetBrains Mono', 'SF Mono', ui-monospace, Menlo, Monaco, monospace";

// wave8.20: глобальный масштаб шрифтов и иконок для читаемости на мобильном.
// Применяется ко всем inline fontSize и size={...} через `fs()`.
// wave8.23: 1.5 оказался слишком крупным (текст съезжал, нав-таб
// обрезался). Понизил до 1.2 — лёгкий но заметный bump.
const FS = 1.2;
const fs = (n) => Math.round(n * FS);

const plural = (n, one, few, many) => {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
};

const PRIO_WEIGHT = (p) => ({ "🔴": 0, "🟡": 1, "⚪": 2 }[p] ?? 3);

// wave8.45: группировка списков по категории. Продукты и Привычки всегда
// сверху, остальные — по алфавиту. Без категории — в самый конец.
const _CAT_PRIORITY = ["продукты", "привычки"];
const _catName = (full) => String(full || "").replace(/^\S+\s*/u, "").trim();
const _catWeight = (name) => {
  const i = _CAT_PRIORITY.indexOf(name.toLowerCase());
  return i >= 0 ? i : 100;
};
function groupByField(items, field) {
  const m = new Map();
  for (const x of items) {
    const k = String(x[field] || "").trim();
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return [...m.entries()].sort(([a], [b]) => {
    if (!a && b) return 1;
    if (a && !b) return -1;
    return a.localeCompare(b, "ru");
  });
}
function groupByCat(items) {
  const m = new Map();
  for (const x of items) {
    const k = _catName(x.catFull) || "";
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return [...m.entries()].sort(([a], [b]) => {
    if (!a && b) return 1;
    if (a && !b) return -1;
    const wa = _catWeight(a), wb = _catWeight(b);
    if (wa !== wb) return wa - wb;
    return a.localeCompare(b, "ru");
  });
}

// ═══════════════════════════════════════════════════════════════
// CORE COMPONENTS
// ═══════════════════════════════════════════════════════════════

const Glass = ({ s, children, style, accent, glow, onClick }) => (
  <div
    className={`glass${glow ? " glow" : ""}${accent ? " accent-l" : ""}${onClick ? " tap" : ""}`}
    onClick={onClick}
    style={accent ? { "--accent": accent, ...style } : style}
  >
    {children}
  </div>
);

const Pill = ({ s, active, children, onClick }) => (
  <div className={`pill${active ? " active" : ""}`} onClick={onClick}>
    {children}
  </div>
);

// wave8.5: пилл-селект для форм вместо <Select> — крупный тапабельный UI.
// options: массив строк, либо {value,label}, либо {k,label}
const PillSelect = ({ s, value, onChange, options }) => (
  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
    {(options || []).map((opt, i) => {
      const v = typeof opt === "string" ? opt : (opt.value ?? opt.k ?? opt.label);
      const label = typeof opt === "string" ? opt : (opt.label ?? opt.value ?? opt.k);
      return (
        <Pill key={i} s={s} active={value === v} onClick={() => onChange(v)}>
          {label}
        </Pill>
      );
    })}
  </div>
);

const Bar = ({ s, pct, color }) => (
  <div className="bar">
    <div style={{ width: `${Math.min(Math.max(pct || 0, 0), 100)}%`, background: color }} />
  </div>
);

const Metric = ({ s, v, sub, unit, accent, icon }) => (
  <div className="metric">
    <div className="v" style={accent ? { color: accent } : undefined}>
      {icon && <span style={{ marginRight: 4, display: "inline-flex", alignItems: "center", verticalAlign: "middle" }}>{icon}</span>}
      {v}{unit && <span className="u">{unit}</span>}
    </div>
    <div className="l">{sub}</div>
  </div>
);

const Chk = ({ s, done, onClick }) => (
  <div className={`chk${done ? " done" : ""}`} onClick={onClick}>
    {done && <Check size={13} strokeWidth={3} />}
  </div>
);

const PRIO_NORM = {
  "Срочно": "🔴", "Важно": "🟡", "Можно потом": "⚪",
  "high": "🔴", "medium": "🟡", "low": "⚪",
};
const normPrio = (p) => PRIO_NORM[p] || (p && p.startsWith("🔴") ? "🔴" : p && p.startsWith("🟡") ? "🟡" : p && p.startsWith("⚪") ? "⚪" : p);
const PrioDot = ({ s, prio }) => {
  const p = normPrio(prio);
  const colors = { "🔴": "var(--nx-red)", "🟡": "var(--nx-amber)", "⚪": "var(--nx-text-mute)" };
  return <span className="prio-dot" style={{ background: colors[p] || "var(--nx-text-mute)" }} />;
};

const SectionLabel = ({ s, children, meta, action }) => (
  <div className="section-h">
    <span>{children}</span>
    {meta && <span className="meta">{meta}</span>}
    {action && <span>{action}</span>}
  </div>
);

const Empty = ({ s, text, chill, emoji, title }) => {
  if (chill || emoji || title) {
    // Wave 5 «чилл»-оформление: plaque/card вместо серого текста
    return (
      <Glass s={s} style={{ padding: "24px 14px", textAlign: "center" }}>
        {emoji && <div style={{ fontSize: fs(36), marginBottom: 6 }}>{emoji}</div>}
        {title && (
          <div style={{ fontFamily: H, fontSize: fs(18), color: s.text }}>{title}</div>
        )}
        <div style={{ fontSize: fs(13), color: s.tM, marginTop: title ? 4 : 0 }}>{text}</div>
      </Glass>
    );
  }
  const isLoading = !text || /загруж/i.test(text);
  if (!isLoading) {
    return (
      <div
        style={{
          textAlign: "center",
          padding: "18px 12px",
          color: s.tS,
          fontSize: fs(12),
          fontStyle: "italic",
        }}
      >
        {text}
      </div>
    );
  }
  const label = text || "Загружаю";
  const cleanLabel = label.replace(/\.+$/, "");
  const size = 56;
  const orbitR = 22;
  // wave8.78: для дневного фона (зелёное небо) спиннер должен быть тёплым/контрастным,
  // иначе сейдж-акцент сливается с фоном. Определяем день по яркости текста.
  const isDayMode = s.text && parseInt(s.text.slice(1, 3), 16) < 0x80;
  const accent = isDayMode ? "#f4c66e" : s.acc;
  const halo   = isDayMode ? "#b07a2e" : s.acc;
  const coreInner = isDayMode ? "#fff2c8" : "#eef4ff";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        padding: "32px 12px",
      }}
    >
      <div style={{ position: "relative", width: size, height: size, animation: "nx-glow 2.4s ease-in-out infinite" }}>
        <div style={{
          position: "absolute", inset: -8, borderRadius: "50%",
          background: `radial-gradient(circle, ${isDayMode ? "#b07a2e30" : "#5a9a8a30"} 0%, transparent 65%)`,
          animation: "nx-pulse 2.4s ease-in-out infinite",
        }} />
        {isDayMode ? (
          <svg width={size} height={size} viewBox="0 0 56 56" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <radialGradient id="spinGrad" cx="35%" cy="30%" r="70%">
                <stop offset="0%" stopColor="#fff2c8" />
                <stop offset="40%" stopColor="#f4c66e" />
                <stop offset="100%" stopColor="#b07a2e" />
              </radialGradient>
            </defs>
            <circle cx="28" cy="28" r="26" fill="#f4c66e" opacity="0.15" />
            <circle cx="28" cy="28" r="20" fill="none" stroke="#f4c66e" strokeWidth="0.8" opacity="0.6" />
            <circle cx="28" cy="28" r="14" fill="url(#spinGrad)" />
            <circle cx="23" cy="24" r="2.2" fill="#8a5a28" opacity="0.5" />
            <circle cx="33" cy="28" r="1.6" fill="#8a5a28" opacity="0.45" />
            <circle cx="25" cy="33" r="1.2" fill="#8a5a28" opacity="0.4" />
            <circle cx="31" cy="21" r="0.9" fill="#8a5a28" opacity="0.45" />
          </svg>
        ) : (
          <svg width={size} height={size} viewBox="0 0 56 56" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <radialGradient id="spinGradN" cx="35%" cy="30%" r="70%">
                <stop offset="0%" stopColor="#ffffff" />
                <stop offset="40%" stopColor="#d4dce8" />
                <stop offset="100%" stopColor="#6b7590" />
              </radialGradient>
            </defs>
            <circle cx="28" cy="28" r="26" fill="#d4dce8" opacity="0.12" />
            <circle cx="28" cy="28" r="20" fill="none" stroke="#d4dce8" strokeWidth="0.8" opacity="0.5" />
            <circle cx="28" cy="28" r="14" fill="url(#spinGradN)" />
            <circle cx="23" cy="24" r="2.2" fill="#4a5470" opacity="0.5" />
            <circle cx="33" cy="28" r="1.6" fill="#4a5470" opacity="0.45" />
            <circle cx="25" cy="33" r="1.2" fill="#4a5470" opacity="0.4" />
            <circle cx="31" cy="21" r="0.9" fill="#4a5470" opacity="0.45" />
          </svg>
        )}
        {/* orbit ring + dots */}
        <div style={{ position: "absolute", inset: 0, borderRadius: "50%", border: `1px dashed ${isDayMode ? "#8a5a28bb" : "#7ac0b0bb"}` }} />
        <div style={{ position: "absolute", inset: 0, animation: "nx-orbit 1.6s linear infinite" }}>
          {[0, 120, 240].map((deg, i) => (
            <div key={i} style={{
              position: "absolute", top: "50%", left: "50%",
              width: 5, height: 5, borderRadius: "50%",
              background: isDayMode ? "#8a5a28" : "#7ac0b0",
              boxShadow: `0 0 6px ${isDayMode ? "#b07a2e" : "#5a9a8a"}`,
              transform: `rotate(${deg}deg) translate(${orbitR}px) rotate(-${deg}deg) translate(-50%, -50%)`,
            }} />
          ))}
        </div>
      </div>
      <div
        style={{
          display: "inline-flex", alignItems: "baseline", gap: 2,
          color: s.text, fontSize: fs(13), fontStyle: "italic", letterSpacing: 0.3,
        }}
      >
        <span>{cleanLabel}</span>
        <span style={{ display: "inline-block", animation: "nx-dot 1.2s ease-in-out infinite", animationDelay: "0s" }}>.</span>
        <span style={{ display: "inline-block", animation: "nx-dot 1.2s ease-in-out infinite", animationDelay: "0.15s" }}>.</span>
        <span style={{ display: "inline-block", animation: "nx-dot 1.2s ease-in-out infinite", animationDelay: "0.3s" }}>.</span>
      </div>
    </div>
  );
};

const ErrorBox = ({ s, error, refetch }) => (
  <div className="glass accent-l" style={{ "--accent": "var(--nx-red)", padding: "14px 16px" }}>
    <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 18, fontWeight: 500, color: "var(--nx-red)", marginBottom: 6 }}>
      Ошибка загрузки
    </div>
    <div style={{ fontSize: 13, opacity: 0.7, marginBottom: 12, wordBreak: "break-word" }}>
      {error?.message || "неизвестная ошибка"}
    </div>
    {refetch && (
      <div
        onClick={refetch}
        className="pill"
        style={{ display: "inline-flex", cursor: "pointer", fontWeight: 600 }}
      >
        <RefreshCw size={14} /> Повторить
      </div>
    )}
  </div>
);

// Финальные логотипы: Satisfy + градиентный диск с кратерами и ореолом
const NexusLogo = ({ color = "#1a3528" }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
    <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="sunGrad" cx="35%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#fff2c8" />
          <stop offset="40%" stopColor="#f4c66e" />
          <stop offset="100%" stopColor="#b07a2e" />
        </radialGradient>
      </defs>
      <circle cx="27" cy="27" r="22" fill="#f4c66e" opacity="0.2" />
      <circle cx="27" cy="27" r="17" fill="none" stroke="#f4c66e" strokeWidth="0.8" opacity="0.6" />
      <circle cx="27" cy="27" r="13" fill="url(#sunGrad)" />
      <circle cx="22" cy="23" r="2" fill="#8a5a28" opacity="0.5" />
      <circle cx="31" cy="27" r="1.5" fill="#8a5a28" opacity="0.45" />
      <circle cx="24" cy="32" r="1.1" fill="#8a5a28" opacity="0.4" />
      <circle cx="30" cy="21" r="0.8" fill="#8a5a28" opacity="0.45" />
    </svg>
    <svg width="110" height="40" viewBox="0 0 110 40" xmlns="http://www.w3.org/2000/svg">
      <text x="0" y="30" fontFamily="Caveat, cursive" fontWeight="500" fontSize="28" fill={color}>nexus</text>
    </svg>
  </div>
);

const ArcanaLogo = () => (
  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
    <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="moonGrad" cx="35%" cy="30%" r="70%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="40%" stopColor="#d4dce8" />
          <stop offset="100%" stopColor="#6b7590" />
        </radialGradient>
      </defs>
      <circle cx="27" cy="27" r="22" fill="#d4dce8" opacity="0.15" />
      <circle cx="27" cy="27" r="17" fill="none" stroke="#d4dce8" strokeWidth="0.8" opacity="0.5" />
      <circle cx="27" cy="27" r="13" fill="url(#moonGrad)" />
      <circle cx="22" cy="23" r="2" fill="#4a5470" opacity="0.5" />
      <circle cx="31" cy="27" r="1.5" fill="#4a5470" opacity="0.45" />
      <circle cx="24" cy="32" r="1.1" fill="#4a5470" opacity="0.4" />
      <circle cx="30" cy="21" r="0.8" fill="#4a5470" opacity="0.45" />
    </svg>
    <svg width="120" height="40" viewBox="0 0 120 40" xmlns="http://www.w3.org/2000/svg">
      <text x="0" y="30" fontFamily="Caveat, cursive" fontWeight="500" fontSize="28" fill="#f0ebe0">arcana</text>
    </svg>
  </div>
);

const FAB = ({ s, onClick }) => (
  <div
    onClick={onClick}
    style={{
      // wave5.4: fixed + safe-area-inset; wave8.7: glass-стиль с акцентной рамкой
      position: "fixed",
      bottom: "calc(env(safe-area-inset-bottom, 0px) + 84px)",
      right: 16,
      width: 56,
      height: 56,
      borderRadius: 28,
      background: `${s.acc}dd`,
      color: "#fff",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      cursor: "pointer",
      zIndex: 50,
      boxShadow: `0 4px 16px ${s.acc}66`,
      backdropFilter: "blur(12px)",
      border: `1px solid ${s.acc}88`,
    }}
  >
    <Plus size={fs(24)} strokeWidth={2.2} />
  </div>
);

const Sheet = ({ s, open, onClose, title, children }) => {
  if (!open) return null;
  const isDayMode = s.text && parseInt(s.text.slice(1, 3), 16) < 0x80;
  const sheetBorder = isDayMode ? "rgba(255,255,255,0.6)" : "rgba(180,188,215,0.3)";
  const sheetShadow = isDayMode ? "0 -8px 48px rgba(60,80,70,0.15)" : "0 -8px 48px rgba(0,0,0,0.6)";
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "transparent",
        zIndex: 20,
        display: "flex",
        alignItems: "flex-end",
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          background: "transparent",
          backdropFilter: "blur(40px)",
          WebkitBackdropFilter: "blur(40px)",
          boxShadow: sheetShadow,
          borderTop: `1px solid ${sheetBorder}`,
          borderTopLeftRadius: 22,
          borderTopRightRadius: 22,
          padding: "14px 16px 96px",
          maxHeight: "86%",
          overflowY: "auto",
        }}
      >
        <div
          style={{
            width: 36,
            height: 4,
            borderRadius: 2,
            background: s.brd,
            margin: "0 auto 14px",
          }}
        />
        {title && (
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 14,
            }}
          >
            <span className="page-title">{title}</span>
            <span
              onClick={onClose}
              style={{ color: s.tS, cursor: "pointer", display: "flex" }}
            >
              <X size={fs(20)} />
            </span>
          </div>
        )}
        {children}
      </div>
    </div>
  );
};

const Stars = ({ op }) => (
  <>
    {Array.from({ length: 30 }, (_, i) => ({
      x: (i * 37 + 13) % 100,
      y: (i * 23 + 7) % 50,
      sz: 1 + (i % 3),
      d: 2 + (i % 4),
    })).map((st, i) => (
      <div
        key={i}
        style={{
          position: "absolute",
          left: `${st.x}%`,
          top: `${st.y}%`,
          width: st.sz,
          height: st.sz,
          borderRadius: "50%",
          background: "#fff",
          opacity: op * (0.2 + (i % 5) * 0.14),
          animation: `tw ${st.d}s ease-in-out infinite alternate`,
          animationDelay: `${i * 0.15}s`,
          pointerEvents: "none",
        }}
      />
    ))}
  </>
);

// wave8.72: анимированные погодные эффекты поверх overlay
const WeatherFx = ({ kind, isDay }) => {
  if (kind === "rain") {
    const drops = Array.from({ length: 45 }, (_, i) => ({
      x: (i * 53 + 7) % 100,
      d: 0.6 + ((i * 13) % 7) * 0.08,
      delay: ((i * 17) % 30) / 10,
      h: 10 + (i % 5) * 3,
    }));
    return (
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1, overflow: "hidden" }}>
        {drops.map((dr, i) => (
          <div key={i} style={{
            position: "absolute",
            left: `${dr.x}%`,
            top: -20,
            width: 1,
            height: dr.h,
            background: "linear-gradient(180deg, rgba(180,200,220,0) 0%, rgba(180,200,220,0.55) 100%)",
            animation: `nx-rain ${dr.d}s linear infinite`,
            animationDelay: `${dr.delay}s`,
          }} />
        ))}
      </div>
    );
  }
  if (kind === "snow") {
    const flakes = Array.from({ length: 35 }, (_, i) => ({
      x: (i * 41 + 11) % 100,
      d: 6 + ((i * 7) % 8),
      delay: ((i * 19) % 60) / 10,
      sz: 2 + (i % 4),
      sway: 3 + (i % 4),
    }));
    return (
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1, overflow: "hidden" }}>
        {flakes.map((fl, i) => (
          <div key={i} style={{
            position: "absolute",
            left: `${fl.x}%`,
            top: -10,
            width: fl.sz,
            height: fl.sz,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.85)",
            boxShadow: "0 0 4px rgba(255,255,255,0.5)",
            animation: `nx-snow ${fl.d}s linear infinite, nx-sway ${fl.sway}s ease-in-out infinite alternate`,
            animationDelay: `${fl.delay}s, ${fl.delay / 2}s`,
          }} />
        ))}
      </div>
    );
  }
  if (kind === "fog") {
    return (
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1, overflow: "hidden" }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{
            position: "absolute",
            left: "-30%",
            top: `${15 + i * 28}%`,
            width: "160%",
            height: 90,
            background: "radial-gradient(ellipse at center, rgba(220,225,230,0.55) 0%, rgba(220,225,230,0) 70%)",
            filter: "blur(8px)",
            animation: `nx-fog ${50 + i * 18}s linear infinite`,
            animationDelay: `${-i * 12}s`,
          }} />
        ))}
      </div>
    );
  }
  if (kind === "cloudy") {
    const clouds = [
      { top: 8, scale: 1.0, op: 0.55, dur: 90, delay: 0 },
      { top: 22, scale: 1.4, op: 0.4, dur: 130, delay: -40 },
      { top: 38, scale: 0.8, op: 0.5, dur: 75, delay: -25 },
    ];
    return (
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1 }}>
        {clouds.map((c, i) => (
          <div key={i} style={{
            position: "absolute",
            left: "-25%",
            top: `${c.top}%`,
            width: 240 * c.scale,
            height: 120 * c.scale,
            opacity: c.op,
            background: "radial-gradient(circle at 30% 50%, rgba(255,255,255,0.85) 0%, rgba(255,255,255,0) 50%), radial-gradient(circle at 60% 55%, rgba(255,255,255,0.8) 0%, rgba(255,255,255,0) 55%), radial-gradient(circle at 45% 40%, rgba(255,255,255,0.75) 0%, rgba(255,255,255,0) 60%)",
            filter: "blur(18px)",
            animation: `nx-cloud ${c.dur}s linear infinite`,
            animationDelay: `${c.delay}s`,
          }} />
        ))}
      </div>
    );
  }
  // clear day — мягкие лучи у солнца уже даёт sun-glow; добавим лёгкое мерцание
  if (kind === "clear" && isDay) {
    return (
      <div style={{
        position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1, overflow: "hidden",
        background: "radial-gradient(circle at 75% 18%, rgba(255,235,180,0.18) 0%, transparent 45%)",
        animation: "nx-shine 8s ease-in-out infinite alternate",
      }} />
    );
  }
  return null;
};

// ═══════════════════════════════════════════════════════════════
// MOCK DATA
// ═══════════════════════════════════════════════════════════════

const MOCK = {
  today: {
    date: "22 апреля, вт",
    streak: 8,
    budgetDay: 4166,
    spentDay: 2604,
    overdue: [{ id: 4, title: "Оплатить интернет", cat: "💻", prio: "🔴", days: 2 }],
    scheduled: [
      { id: 1, title: "Позвонить врачу", cat: "🏥", prio: "🔴", time: "09:00", rem: "за 1 ч" },
      { id: 2, title: "Менять лоток котам", cat: "🐾", prio: "🟡", time: "21:00", rpt: "🔄 каждые 2 дня", streak: 8 },
    ],
    tasks: [
      { id: 3, title: "Генеральная уборка кухни", cat: "🏠", prio: "🔴", date: "27 апр" },
      { id: 5, title: "Позвонить нотариусу", cat: "👥", prio: "🟡", date: "15 мая" },
    ],
    adhdTip: "Начни с лотка — 2 минуты. Коты будут рады, а ты получишь буст от выполненного.",
  },
  finance: {
    month: { inc: 115000, exp: 26910, fixed: 52284 },
    today: [
      { desc: "Красное&Белое", cat: "🍜 Продукты", amt: 2000 },
      { desc: "OZON", cat: "🏠 Жилье", amt: 241 },
      { desc: "Яндекс Go", cat: "🚕 Транспорт", amt: 450 },
    ],
    cats: [
      { name: "🚬 Привычки", spent: 14200, limit: 17685 },
      { name: "🍜 Продукты", spent: 6700, limit: 8000 },
      { name: "🍱 Кафе", spent: 4100, limit: 5000 },
      { name: "🚕 Транспорт", spent: 900, limit: 2000 },
      { name: "💅 Бьюти", spent: 3000, limit: 3000 },
      { name: "🎲 Импульсивные", spent: 400, limit: 3000 },
    ],
    goals: [
      { n: "Samsung Flip", t: 100000, s: 0, after: "окт '26" },
      { n: "Финподушка", t: 200000, s: 0, after: "окт '26" },
    ],
    debts: [
      { n: "Вика", total: 50000, left: 25000, by: "апрель", note: "рассрочка: 25к + 25к в мае" },
    ],
  },
  tasks: [
    { id: 101, title: "Менять лоток котам", cat: "🐾", prio: "🟡", status: "active", rpt: "🔄 2д" },
    { id: 102, title: "Генеральная уборка кухни", cat: "🏠", prio: "🔴", status: "active", date: "27 апр" },
    { id: 103, title: "Позвонить нотариусу", cat: "👥", prio: "🟡", status: "active", date: "15 мая" },
    { id: 104, title: "Оплатить интернет", cat: "💻", prio: "🔴", status: "overdue", date: "20 апр" },
    { id: 105, title: "Купить корм коту", cat: "🐾", prio: "⚪", status: "done" },
    { id: 106, title: "Записаться к врачу", cat: "🏥", prio: "🟡", status: "done" },
  ],
  lists: {
    buy: [
      { name: "Молоко", cat: "🍜", done: false },
      { name: "Корм для котов", cat: "🐾", done: false },
      { name: "Монстр", cat: "🚬", done: true },
    ],
    inv: [
      { name: "Свечи белые", qty: 12, exp: "15 мая" },
      { name: "Ладан", qty: 3 },
      { name: "Корм Royal Canin", qty: 1, exp: "20 мая" },
    ],
    check: [],
  },
  memory: [
    { text: "Chapman = сигареты → 🚬", cat: "🛒 Предпочтения" },
    { text: "Молоко = Простоквашино 3.2%", cat: "🛒 Предпочтения" },
    { text: "Предпочитает острое", cat: "🍜 Продукты" },
    { text: "Не любит звонки до 12:00", cat: "👥 Люди" },
  ],
  adhdProfile:
    "Гиперфокус на вечер. Утром тяжело стартовать — ставить лёгкие задачи первыми. Работает техника 2 минут.",
  // Arcana
  arcanaDay: {
    sessionsToday: [
      { id: 301, time: "14:00", client: "Анна", type: "✝️ Кельтский крест", area: "Отношения", status: "upcoming" },
      { id: 302, time: "18:30", client: "Мария", type: "🔺 Триплет", area: "Работа", status: "upcoming" },
    ],
    worksToday: [
      { id: 401, title: "Подготовить свечи для ритуала Анны", cat: "🕯️ Расходники", prio: "🟡" },
      { id: 402, title: "Пост в телеграм-канал", cat: "📱 Соцсети", prio: "⚪" },
    ],
    unchecked30d: 4,
    accuracy: 76,
  },
  sessions: [
    {
      id: 201, q: "Что думает Вадим", client: "Кай", area: "Отношения",
      deck: "Уэйт", type: "🔺 Триплет", date: "21 апр 2026", time: "18:00",
      cards: [
        { pos: "Прошлое", name: "Шут", icon: "🃏" },
        { pos: "Настоящее", name: "Маг", icon: "🎩" },
        { pos: "Будущее", name: "Жрица", icon: "🌙" },
      ],
      bottom: null,
      interp: "Триплет показывает, что Вадим находится в точке решения. Шут в прошлом — начало чего-то нового и наивное предвкушение. Маг в настоящем — он сейчас обладает всеми инструментами, но выбирает как их использовать. Жрица в будущем — путь уходит в скрытое, интуитивное. Ясного ответа нет, потому что он сам ещё не определился.",
      done: "⏳ Не проверено", price: 0, paid: 0,
    },
    {
      id: 202, q: "Планы Вадима на будущее", client: "Кай", area: "Отношения",
      deck: "Dark Wood", type: "✝️ Кельтский крест", date: "20 апр 2026",
      cards: [
        { pos: "Суть", name: "Туз Мечей", icon: "⚔️" },
        { pos: "Препятствие", name: "Верховная Жрица", icon: "🌙" },
        { pos: "Основа", name: "10 Пентаклей", icon: "💎" },
        { pos: "Прошлое", name: "6 Кубков", icon: "🏆" },
        { pos: "Возможное", name: "Звезда", icon: "⭐" },
        { pos: "Ближайшее", name: "Рыцарь Кубков", icon: "🐴" },
      ],
      bottom: { name: "Король Кубков", icon: "👑" },
      interp: "Расклад указывает на период нового начала в сфере отношений. Туз Мечей в позиции сути — ясность намерений, готовность к переменам. Верховная Жрица как препятствие говорит о необходимости довериться интуиции. Десятка Пентаклей в основе — глубинное стремление к стабильности и семейному очагу. Шестёрка Кубков напоминает о ностальгии, удерживающей в прошлом. Звезда даёт надежду и вдохновение. Рыцарь Кубков в ближайшем — предложение от сердца. Король Кубков на дне — зрелая эмоциональная сила фонового процесса.",
      done: "⏳ Не проверено", price: 0, paid: 0,
    },
    {
      id: 203, q: "Перспективы на работе", client: "Мария", area: "Работа",
      deck: "Уэйт", type: "🔺 Триплет", date: "19 апр 2026",
      cards: [
        { pos: "Суть ситуации", name: "Туз Пентаклей", icon: "💎" },
        { pos: "Что поможет", name: "Колесо Фортуны", icon: "☸️" },
        { pos: "Исход", name: "Мир", icon: "🌍" },
      ],
      bottom: null,
      interp: "Сильный позитивный расклад для работы. Туз Пентаклей — новые финансовые возможности. Колесо Фортуны — время перемен в лучшую сторону. Мир — завершение этапа с чувством достижения.",
      done: "✅ Сбылось", price: 2000, paid: 2000,
    },
    {
      id: 204, q: "Здоровье котов", client: "Кай", area: "Здоровье",
      deck: "Ленорман", type: "🔺 Триплет", date: "18 апр 2026",
      cards: [
        { pos: "Первая", name: "Собака", icon: "🐕" },
        { pos: "Вторая", name: "Дерево", icon: "🌳" },
        { pos: "Третья", name: "Звёзды", icon: "✨" },
      ],
      bottom: null,
      interp: "Собака + Дерево + Звёзды — верный друг, долгое здоровье, позитивный прогноз. У котов всё хорошо, тревоги напрасны.",
      done: "✅ Сбылось", price: 0, paid: 0,
    },
  ],
  clients: [
    {
      id: 501, name: "Кай", initial: "К", sessions: 24, rituals: 8, debt: 0,
      total: 0, since: "5 января 2026", contact: "@dontkaiad",
      request: "Личная практика", status: "🟢", self: true,
      notes: "Это я сама — личные расклады и ритуалы для себя.",
      history: [
        { date: "21 апр", type: "🃏", desc: "Что думает Вадим — Уэйт", amount: 0, paid: true },
        { date: "20 апр", type: "🃏", desc: "Планы Вадима — Dark Wood", amount: 0, paid: true },
      ],
    },
    {
      id: 502, name: "Анна", initial: "А", sessions: 12, rituals: 3, debt: 3000,
      total: 42000, since: "15 января 2026", contact: "@anna_tarot",
      request: "Отношения, поиск партнёра", status: "🟢",
      notes: "Тревожная, нужен мягкий подход. Предпочитает расклады на отношения. В последний раз интересовалась ритуалами.",
      history: [
        { date: "5 апр", type: "🃏", desc: "Кельтский крест — отношения", amount: 3000, paid: false },
        { date: "18 мар", type: "🕯️", desc: "Ритуал защиты", amount: 5000, paid: true },
        { date: "2 мар", type: "🃏", desc: "Расклад на месяц", amount: 3000, paid: true },
        { date: "15 янв", type: "🃏", desc: "Первый расклад — отношения", amount: 3000, paid: true },
      ],
    },
    {
      id: 503, name: "Мария", initial: "М", sessions: 5, rituals: 1, debt: 0,
      total: 12000, since: "10 февраля 2026", contact: "@maria_m",
      request: "Работа, карьерный рост", status: "🟢",
      notes: "Прагматичная, любит конкретику. Не верит в мистику, но ценит разговор.",
      history: [
        { date: "12 апр", type: "🃏", desc: "Триплет — работа", amount: 2000, paid: true },
      ],
    },
    {
      id: 504, name: "Вадим", initial: "В", sessions: 3, rituals: 0, debt: 1500,
      total: 4500, since: "1 марта 2026", contact: "—",
      request: "Не определено", status: "⏸",
      notes: "Пауза. Не ответил на последнее сообщение.",
      history: [
        { date: "10 мар", type: "🃏", desc: "Первая консультация", amount: 1500, paid: false },
      ],
    },
  ],
  rituals: [
    {
      id: 701, name: "Очищение дома", goal: "🌊 Очищение", place: "🏠 Дома",
      date: "21 апр", type: "🌟", result: "⏳",
      client: null, question: "Очистить пространство после переезда",
      price: 0, paid: 0,
      supplies: [
        { name: "Соль морская", qty: "100 г", price: 50 },
        { name: "Свечи белые", qty: "× 4", price: 160 },
        { name: "Шалфей пучок", qty: "1 шт", price: 120 },
      ],
      time: 30, offerings: "Белые цветы на подоконник",
      powers: "Стихия воздуха — развеивание",
      structure: [
        "Открыть окна во всех комнатах",
        "Обход по часовой стрелке с шалфеем",
        "Соль по углам каждой комнаты",
        "Свечи в 4 углах главной комнаты",
        "Молитва на очищение",
        "Закрытие — подношение цветов",
      ],
    },
    {
      id: 702, name: "Защита для Анны", goal: "🛡️ Защита", place: "🏠 Дома",
      date: "19 апр", type: "🤝", result: "✅",
      client: "Анна", question: "Защита от негатива на работе",
      price: 5000, paid: 5000,
      supplies: [
        { name: "Свечи чёрные", qty: "× 3", price: 180 },
        { name: "Соль чёрная", qty: "50 г", price: 70 },
        { name: "Красная нить", qty: "1 м", price: 40 },
      ],
      time: 45, offerings: "Монеты на перекрёсток — 7 шт",
      powers: "Архангел Михаил — щит",
      structure: [
        "Очищение пространства ладаном",
        "Круг из чёрной соли",
        "Зажечь 3 свечи",
        "Наговор на красную нить",
        "Визуализация щита над клиенткой",
        "Завязать нить на 9 узлов",
        "Закрытие круга",
        "Откуп на перекрёстке",
      ],
    },
    {
      id: 703, name: "Привлечение финансов", goal: "🧲 Привлечение", place: "🛤️ Перекрёсток",
      date: "15 апр", type: "🌟", result: "〰️",
      client: null, question: "Финансовый поток в мой дом",
      price: 0, paid: 0,
      supplies: [
        { name: "Свечи зелёные", qty: "× 2", price: 120 },
        { name: "Корица", qty: "1 ч.л.", price: 30 },
        { name: "Мёд", qty: "1 ст.л.", price: 20 },
      ],
      time: 40, offerings: "Мёд + медные монеты × 7 на перекрёсток",
      powers: "Лакшми — изобилие",
      structure: [
        "Приход на перекрёсток на закате",
        "Круг из свечей",
        "Мёд с корицей в центр",
        "Наговор на привлечение",
        "Подношение",
        "Уход не оглядываясь",
      ],
    },
  ],
  grimoire: [
    { name: "Заговор на деньги", cat: "📿 Заговоры", theme: "💰" },
    { name: "Очищение дома травами", cat: "📝 Заметки", theme: "🌊" },
    { name: "Защитный оберег", cat: "✨ Комбинации", theme: "🛡️" },
    { name: "Мёд + корица (привлечение)", cat: "🧴 Рецепты", theme: "🧲" },
  ],
  stats: {
    months: [
      { name: "Апрель", total: 8, yes: 4, partial: 1, no: 0, pending: 3 },
      { name: "Март", total: 23, yes: 16, partial: 3, no: 1, pending: 3 },
      { name: "Февраль", total: 19, yes: 15, partial: 2, no: 2, pending: 0 },
      { name: "Январь", total: 12, yes: 8, partial: 2, no: 1, pending: 1 },
    ],
    practice: { inc: 45000, exp: 8000 },
    monthBlock: {
      inc: 45000, supplies: 3500, accuracy: 75, sessions: 15,
      label: "Март",
    },
  },
};

// ═══════════════════════════════════════════════════════════════
// TASK LIST ITEM (общий компонент)
// ═══════════════════════════════════════════════════════════════

const TaskRow = ({ s, t, done, onToggle, onOpen, withTime }) => (
  <Glass
    s={s}
    style={{
      padding: "10px 14px",
      marginBottom: 6,
      opacity: done ? 0.45 : 1,
      display: "flex",
      gap: 10,
      alignItems: "center",
    }}
  >
    {withTime && t.time && (
      <span
        style={{
          fontFamily: "'SF Mono', Menlo, monospace",
          fontSize: fs(14),
          color: s.acc,
          fontWeight: 600,
          minWidth: 44,
        }}
      >
        {t.time}
      </span>
    )}
    <Chk s={s} done={done} onClick={onToggle} />
    <div style={{ flex: 1, minWidth: 0 }} onClick={onOpen}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 6,
          textDecoration: done ? "line-through" : "none",
        }}
      >
        {/* wave8.33: категория — после заголовка, как отдельная сущность
            справа. Один стиль бейджа на обеих вкладках (день + задачи). */}
        <span style={{
          flex: 1, minWidth: 0,
          fontSize: fs(16),
          color: s.text,
          fontWeight: 500,
          wordBreak: "break-word",
        }}>
          {t.title}
        </span>
        {t.cat && (
          <span style={{
            display: "inline-flex", alignItems: "center",
            padding: "3px 9px", borderRadius: 10,
            fontSize: fs(13), background: `${s.acc}33`, color: s.text, fontWeight: 500,
            flexShrink: 0, whiteSpace: "nowrap",
          }}>
            {String(t.cat).split(" ")[0]}
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: fs(13),
          color: s.text,
          opacity: 0.72,
          marginTop: 3,
          display: "flex",
          gap: 8,
          alignItems: "center",
        }}
      >
        {t.deadlineTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
            📅 {t.deadlineTime}
          </span>
        )}
        {t.reminderTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
            <Bell size={fs(12)} /> {t.reminderTime}
          </span>
        )}
        {t.rem && !t.reminderTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
            <Bell size={fs(12)} /> {t.rem}
          </span>
        )}
        {t.rpt && <span>{t.rpt}</span>}
        {t.date && !t.deadlineTime && <span>{t.date}</span>}
        {t.streak > 0 && <span>🔥 {t.streak}</span>}
      </div>
    </div>
    <PrioDot s={s} prio={t.prio} />
  </Glass>
);

// ═══════════════════════════════════════════════════════════════
// NEXUS — MY DAY
// ═══════════════════════════════════════════════════════════════

const WEATHER_ICON = {
  clear: "☀️",
  cloudy: "⛅",
  rain: "🌧️",
  snow: "❄️",
  fog: "🌫️",
};

// wave8.24: длинные англ. имена города ломают шапку «Мой день»; рисуем
// короткие RU-формы, неизвестные просто обрезаем по первому слову.
const CITY_SHORT = {
  "Saint Petersburg": "СПб",
  "Moscow": "Москва",
  "New York": "NYC",
  "Los Angeles": "LA",
  "Istanbul": "Стамбул",
  "Tbilisi": "Тбилиси",
  "Yerevan": "Ереван",
  "Bangkok": "Бангкок",
  "Dubai": "Дубай",
  "Berlin": "Берлин",
  "Paris": "Париж",
  "Amsterdam": "Амстердам",
  "Rome": "Рим",
  "Madrid": "Мадрид",
  "London": "Лондон",
  "Tokyo": "Токио",
  "Shanghai": "Шанхай",
};
const shortCity = (c) => CITY_SHORT[c] || (c ? c.split(/[ ,]/)[0] : "");

// wave8.2: поддержка **жирного** в коротких текстах (СДВГ-совет и т.п.)
function renderBoldMd(text) {
  if (!text) return null;
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i} style={{ fontWeight: 700 }}>{p.slice(2, -2)}</strong>
    ) : (
      <span key={i}>{p}</span>
    )
  );
}

// ═══════════════════════════════════════════════════════════════
// STREAK ACHIEVED CARD — holo foil for current_streak >= 3
// ═══════════════════════════════════════════════════════════════

const NX_HOLO = {
  cyan: "#a8e0d2", magenta: "#e1a8c8", gold: "#e8c887",
  violet: "#bfa2dc", amber: "#d4a35a", ember: "#b07a2e",
};

function streakRank(current) {
  if (current >= 100) return "INVICTUS";
  if (current >= 30) return "PHOENIX";
  if (current >= 7) return "HELIOS";
  return "SOLARIS";
}

function CardSunSigil({ size = 60 }) {
  const rays = [];
  for (let i = 0; i < 24; i++) {
    const a = (i * Math.PI * 2) / 24;
    const long = i % 3 === 0;
    const r1 = 22, r2 = long ? 36 : 30;
    rays.push(
      <line key={i}
        x1={50 + Math.cos(a) * r1} y1={50 + Math.sin(a) * r1}
        x2={50 + Math.cos(a) * r2} y2={50 + Math.sin(a) * r2}
        stroke="#5a3c10" strokeWidth="0.9" strokeLinecap="round" strokeOpacity="0.85" />
    );
  }
  const ticks = [];
  for (let i = 0; i < 36; i++) {
    const a = (i * Math.PI * 2) / 36;
    const r1 = 19, r2 = i % 3 === 0 ? 21 : 20.2;
    ticks.push(
      <line key={`t${i}`}
        x1={50 + Math.cos(a) * r1} y1={50 + Math.sin(a) * r1}
        x2={50 + Math.cos(a) * r2} y2={50 + Math.sin(a) * r2}
        stroke="#5a3c10" strokeWidth="0.5" strokeOpacity="0.55" />
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none"
         style={{ filter: "drop-shadow(0 1px 0 rgba(255,250,235,0.5))" }}>
      <circle cx="50" cy="50" r="46" stroke="#5a3c10" strokeWidth="0.5" strokeOpacity="0.45" fill="none" strokeDasharray="1 4" />
      <path d="M50 16 L74 56 L26 56 Z" stroke="#5a3c10" strokeWidth="0.7" strokeOpacity="0.55" fill="none" strokeLinejoin="round" />
      <path d="M50 84 L26 44 L74 44 Z" stroke="#5a3c10" strokeWidth="0.7" strokeOpacity="0.4" fill="none" strokeLinejoin="round" />
      <circle cx="50" cy="50" r="22" stroke="#5a3c10" strokeWidth="0.9" strokeOpacity="0.7" fill="none" />
      {ticks}
      {rays}
      <circle cx="50" cy="50" r="14" fill="#f4e3c1" />
      <circle cx="50" cy="50" r="14" stroke="#5a3c10" strokeWidth="1" fill="none" />
      <path d="M40 50 Q50 42 60 50 Q50 58 40 50 Z" stroke="#5a3c10" strokeWidth="0.9" fill="rgba(255,250,235,0.6)" />
      <circle cx="50" cy="50" r="3.2" fill="#5a3c10" />
      <circle cx="51" cy="49" r="0.9" fill="#f4e3c1" />
      <line x1="50" y1="44" x2="50" y2="40" stroke="#5a3c10" strokeWidth="0.6" strokeLinecap="round" />
      <line x1="50" y1="56" x2="50" y2="60" stroke="#5a3c10" strokeWidth="0.6" strokeLinecap="round" />
    </svg>
  );
}

function StreakAchievedCard({ width = 110, height = 112, current, best, lastDateIso, todayIso, onClick }) {
  const scale = width / 110;
  const sigilSize = Math.min(height - 50, 58) * Math.max(1, scale * 0.95);
  const fsRank = Math.round(13 * Math.max(1, scale));
  const fsBadge = Math.max(7, Math.round(7 * Math.max(1, scale)));
  const fsSerial = Math.max(6.5, Math.round(7 * Math.max(1, scale)));
  const fsSubline = Math.max(6.5, Math.round(7 * Math.max(1, scale)));
  const fsCaption = Math.max(7, Math.round(7.5 * Math.max(1, scale)));
  const pad = Math.max(7, Math.round(8 * Math.max(1, scale)));

  const cur = Math.max(0, current | 0);
  const bst = Math.max(cur, best | 0);
  const pad3 = (n) => String(n).padStart(3, "0");
  const serial = `${pad3(cur)}/${pad3(bst)}`;
  const rank = streakRank(cur);
  const badge = (lastDateIso && todayIso && lastDateIso === todayIso) ? "+1" : "🔥";
  const caption = `${cur} ДН`;

  return (
    <div onClick={onClick} style={{ width, height, position: "relative", perspective: 1000, cursor: onClick ? "pointer" : "default" }}>
      <div style={{
        position: "relative", width: "100%", height: "100%",
        borderRadius: 14, overflow: "hidden",
        animation: "nx-mcard-in 900ms cubic-bezier(.2,.8,.2,1) both",
        boxShadow: "0 1px 0 rgba(255,255,255,0.6) inset, 0 12px 28px -14px rgba(80,55,20,0.55), 0 4px 14px -6px rgba(176,122,46,0.45)",
      }}>
        <div style={{
          position: "absolute", inset: 0, borderRadius: 14,
          background: "linear-gradient(135deg, rgba(255,253,248,0.92) 0%, rgba(244,227,193,0.85) 45%, rgba(212,163,90,0.55) 100%)",
        }} />
        <div aria-hidden style={{ position: "absolute", inset: 0, overflow: "hidden", borderRadius: 14 }}>
          <div style={{
            position: "absolute", inset: 0,
            backgroundImage: "linear-gradient(rgba(90,60,16,0.10) 1px, transparent 1px), linear-gradient(90deg, rgba(90,60,16,0.10) 1px, transparent 1px)",
            backgroundSize: "18px 18px",
            maskImage: "radial-gradient(circle at 50% 48%, black 25%, transparent 75%)",
            WebkitMaskImage: "radial-gradient(circle at 50% 48%, black 25%, transparent 75%)",
          }} />
          <svg style={{
            position: "absolute", left: "50%", top: "50%",
            transform: "translate(-50%, -50%)",
            width: Math.min(height * 1.7, 220), height: Math.min(height * 1.7, 220),
            opacity: 0.35,
          }} viewBox="0 0 320 320">
            <g stroke="#5a3c10" fill="none" strokeWidth="0.9" strokeOpacity="0.5">
              <circle cx="160" cy="160" r="150" />
              <circle cx="160" cy="160" r="118" strokeDasharray="3 5" />
              <polygon points="160,30 272,224 48,224" />
              <polygon points="160,290 48,96 272,96" strokeOpacity="0.32" />
            </g>
          </svg>
        </div>
        <div aria-hidden style={{
          position: "absolute", inset: 0, borderRadius: 14,
          background: `conic-gradient(from var(--nx-mcard-angle, 0deg) at 50% 50%, ${NX_HOLO.cyan}66 0deg, ${NX_HOLO.gold}88 60deg, ${NX_HOLO.amber}aa 120deg, ${NX_HOLO.magenta}66 180deg, ${NX_HOLO.violet}55 240deg, ${NX_HOLO.gold}88 300deg, ${NX_HOLO.cyan}66 360deg)`,
          mixBlendMode: "soft-light",
          animation: "nx-mcard-holo 7s linear infinite",
          opacity: 0.95,
        }} />
        <div aria-hidden style={{
          position: "absolute", inset: 0, borderRadius: 14, padding: 1.4,
          background: `linear-gradient(120deg, ${NX_HOLO.cyan}, ${NX_HOLO.magenta}, ${NX_HOLO.gold}, ${NX_HOLO.amber}, ${NX_HOLO.violet}, ${NX_HOLO.cyan})`,
          backgroundSize: "300% 100%",
          WebkitMask: "linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)",
          WebkitMaskComposite: "xor", maskComposite: "exclude",
          animation: "nx-holo-border 4s linear infinite",
          opacity: 0.95,
        }} />
        <div aria-hidden style={{ position: "absolute", inset: 0, borderRadius: 14, overflow: "hidden", pointerEvents: "none" }}>
          <div style={{
            position: "absolute", top: 0, bottom: 0, width: "40%",
            background: "linear-gradient(115deg, transparent 0%, rgba(255,250,235,0.55) 50%, transparent 100%)",
            mixBlendMode: "overlay",
            animation: "nx-holo-shine 5s ease-in-out infinite",
          }} />
        </div>
        <div style={{ position: "absolute", inset: 0, padding: `${pad}px ${pad + 1}px`, color: "#3d2a10" }}>
          <div style={{
            position: "absolute", top: pad, left: pad + 2,
            fontFamily: 'ui-monospace, "JetBrains Mono", monospace',
            fontSize: fsSerial, letterSpacing: "0.18em",
            textTransform: "uppercase", opacity: 0.78, lineHeight: 1,
          }}>{serial}</div>
          <div style={{
            position: "absolute", top: pad - 2, right: pad - 2,
            display: "inline-flex", alignItems: "center", gap: 3,
            padding: `${Math.max(2, scale * 2)}px ${Math.max(5, scale * 5)}px`,
            border: "0.8px solid rgba(80,55,20,0.55)",
            borderRadius: 999,
            background: `linear-gradient(135deg, rgba(255,250,235,0.9), ${NX_HOLO.gold}55, rgba(255,250,235,0.85))`,
            boxShadow: "0 1px 2px rgba(80,55,20,0.22), inset 0 1px 0 rgba(255,250,235,0.7)",
            fontFamily: "ui-monospace, monospace",
            fontSize: fsBadge, fontWeight: 700,
            letterSpacing: "0.16em", textTransform: "uppercase",
            color: "#5a3c10", lineHeight: 1,
          }}>
            <span style={{
              width: Math.max(3, scale * 4), height: Math.max(3, scale * 4),
              borderRadius: "50%", background: "#5a3c10",
              boxShadow: `0 0 4px ${NX_HOLO.ember}cc`,
              animation: "nx-mcard-pulse 2s ease-in-out infinite",
            }} />
            <span>{badge}</span>
          </div>
          <div style={{
            position: "absolute", inset: 0,
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            paddingTop: pad,
          }}>
            <CardSunSigil size={sigilSize} />
            <div style={{
              fontFamily: '"Lora", Georgia, serif',
              fontSize: fsRank, fontStyle: "italic",
              lineHeight: 1, marginTop: Math.max(2, scale * 3),
              textShadow: "0 1px 0 rgba(255,250,235,0.5)",
            }}>{rank}</div>
            <div style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: fsSubline, letterSpacing: "0.22em",
              textTransform: "uppercase", opacity: 0.65,
              marginTop: Math.max(2, scale * 2), lineHeight: 1,
            }}>СТРИК</div>
          </div>
          <div style={{
            position: "absolute", bottom: pad, left: pad + 2,
            fontFamily: "ui-monospace, monospace",
            fontSize: fsCaption, letterSpacing: "0.14em",
            opacity: 0.7, lineHeight: 1,
          }}>{caption}</div>
        </div>
      </div>
    </div>
  );
}

function NxDay({ s, openTask, navigate, openStreaks }) {
  const [done, setDone] = useState({});
  const { data, loading, error, refetch } = useApi('/api/today');
  // wave6.5: погода (календарь стриков теперь только в StreaksSheet)
  const weatherApi = useApi('/api/weather');

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;

  const t = adaptToday(data);
  const doneCount = Object.values(done).filter(Boolean).length;
  const total = t.scheduled.length + t.tasks.length + (t.noDate?.length || 0);
  const leftPct = Math.round((t.spentDay / t.budgetDay) * 100);
  // Toggle done ↔ not started. Задача остаётся в списке локально — refetch не делаем,
  // чтобы пользователь мог отменить случайное закрытие повторным тапом.
  const toggle = async (id) => {
    if (!id) return;
    const nowDone = !!done[id];
    setDone((p) => ({ ...p, [id]: !nowDone }));
    try {
      await apiPost(`/api/tasks/${id}/${nowDone ? "reopen" : "done"}`);
    } catch (_) {
      setDone((p) => ({ ...p, [id]: nowDone }));
    }
  };

  return (
    <>
      <div className="hero glass glow">
        <div className="hero-h">
          <div>
            <div className="hero-title">Мой день</div>
            <div style={{ fontSize: 13, opacity: 0.7, marginTop: 4, fontWeight: 500 }}>
              {weatherApi.data?.tip || "хорошего дня"}
            </div>
          </div>
          <div className="hero-meta">
            <div>{t.date}</div>
            {weatherApi.data && <div style={{ marginTop: 3 }}>
              {WEATHER_ICON[weatherApi.data.kind] || "🌤"} {weatherApi.data.temp > 0 ? "+" : ""}{weatherApi.data.temp}° · {shortCity(weatherApi.data.city)}
            </div>}
          </div>
        </div>
        <div className="hero-metrics">
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("tasks")}>
            <Metric s={s} v={doneCount} unit={`/${total}`} sub="задачи" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("fin")}>
            <Metric s={s} v={`${Math.round((t.budgetDay - t.spentDay) / 1000)}к`} unit="₽" sub="свободно" />
          </div>
          {t.streak >= 3 ? (
            <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
              <StreakAchievedCard
                width={110}
                height={112}
                current={t.streak}
                best={t.streakBest || t.streak}
                lastDateIso={t.streakLastDate}
                todayIso={t.todayIso}
                onClick={() => openStreaks?.()}
              />
            </div>
          ) : (
            <div style={{ flex: 1, cursor: "pointer" }} onClick={() => openStreaks?.()}>
              <Metric s={s} v={<span className="streak-v"><LucideFlame size={20} fill="currentColor" style={{ flexShrink: 0, color: s.amber }} className="flame" />{t.streak}</span>} sub="стрик" accent={s.amber} />
            </div>
          )}
        </div>
        <div className="hero-budget">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 13, fontWeight: 500, cursor: "pointer" }} onClick={() => navigate?.("fin")}>
            <span style={{ opacity: 0.75 }}>Бюджет дня</span>
            <span style={{ color: leftPct > 85 ? s.red : leftPct > 60 ? s.amber : undefined, fontWeight: 500 }}>{t.budgetDay.toLocaleString()} ₽ · {leftPct}%</span>
          </div>
          <Bar s={s} pct={leftPct} color={leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.acc} />
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>потрачено {t.spentDay.toLocaleString()} ₽ из {t.budgetDay.toLocaleString()} ₽</div>
        </div>
      </div>

      {t.adhdTip && (
        <div className="tip">
          <div className="tip-h">
            <span>🦋 СДВГ-совет</span>
            <RefreshCw size={15} style={{ cursor: "pointer", opacity: 0.6 }} onClick={async () => { try { await apiPost("/api/today/refresh-tip"); refetch(); } catch (_) {} }} />
          </div>
          <div className="tip-body">{renderBoldMd(t.adhdTip)}</div>
        </div>
      )}

      <div className="glass" style={{ padding: "14px 16px" }}>
        <div className="card-h">
          <span className="card-title">Расписание</span>
          <span className="card-meta">{(t.overdue.length + t.scheduled.length) === 0 ? "пусто" : `${t.overdue.length + t.scheduled.length} на сегодня`}</span>
        </div>
        {t.overdue.length === 0 && t.scheduled.length === 0 && (
          <div style={{ padding: "12px 0", textAlign: "center" }}>
            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>На сегодня пусто — отдыхай ✨</div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>Загляни во вкладку «Задачи».</div>
          </div>
        )}
        {t.overdue.map((o) => (
          <div key={o.id} className="sched-row" style={{ opacity: done[o.id] ? 0.45 : 1, cursor: "pointer" }} onClick={() => openTask(o)}>
            <Chk s={s} done={done[o.id]} onClick={(e) => { e.stopPropagation(); toggle(o.id); }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className={`s-title${done[o.id] ? " done" : ""}`}>{o.title}</div>
              <div className="s-meta">
                <span style={{ color: s.red, fontWeight: 600 }}>{o.days} д назад</span>
                {o.rpt && <span>🔄 {o.rpt}</span>}
              </div>
            </div>
            {o.cat && <div className="s-cat">{String(o.cat).split(" ")[0]}</div>}
            <PrioDot s={s} prio={o.prio} />
          </div>
        ))}
        {t.scheduled.map((x) => (
          <div key={x.id} className="sched-row" style={{ opacity: done[x.id] ? 0.45 : 1, cursor: "pointer" }} onClick={() => openTask(x)}>
            <span className="s-time" style={x.time ? {} : { opacity: 0.4 }}>{x.time || "—"}</span>
            <Chk s={s} done={done[x.id]} onClick={(e) => { e.stopPropagation(); toggle(x.id); }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className={`s-title${done[x.id] ? " done" : ""}`}>{x.title}</div>
              <div className="s-meta">
                {x.date && <span>{x.date}</span>}
                {x.rpt && <span>🔄 {x.rpt}</span>}
                {x.streak > 0 && <span>🔥 {x.streak}</span>}
              </div>
            </div>
            {x.cat && <div className="s-cat">{String(x.cat).split(" ")[0]}</div>}
            <PrioDot s={s} prio={x.prio} />
          </div>
        ))}
      </div>
    </>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — TASKS
// ═══════════════════════════════════════════════════════════════

function NxTasks({ s, openTask }) {
  const [f, setF] = useState("active");
  const { data, loading, error, refetch } = useApi(`/api/tasks?filter=${f}`, [f]);
  const list = loading || error ? [] : adaptTasks(data);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      <div className="page-title" style={{ marginBottom: 10 }}>Задачи</div>
      <div className="pills" style={{ marginBottom: 10 }}>
        {[["all","Все"],["active","Активные"],["overdue","Просрочено"],["done","Выполнено"]].map(([k,l]) => (
          <Pill key={k} s={s} active={f === k} onClick={() => setF(k)}>{l}</Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && list.length === 0 && <Empty s={s} emoji="🌿" title="Чилл" text="На сегодня задач нет." />}
      {!loading && !error && list.map((t) => (
        <div key={t.id} className={`task glass${t.status === "done" ? " done" : ""}`} onClick={() => openTask(t)} style={{ cursor: "pointer" }}>
          <div className="body">
            <div className="title">{t.title}</div>
            <div className="meta">
              {t.date && <span style={{ color: t.status === "overdue" ? s.red : undefined }}>{t.date}</span>}
              {t.rpt && <span>{t.rpt}</span>}
              {t.status === "done" && <span>✓ сделано</span>}
            </div>
          </div>
          {t.cat && <div className="cat-badge">{String(t.cat).split(" ")[0]}</div>}
          <PrioDot s={s} prio={t.prio} />
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — FINANCE
// ═══════════════════════════════════════════════════════════════

function NxFinance({ s }) {
  const [tab, setTab] = useState("today");
  const [drillCat, setDrillCat] = useState(null);  // wave6.1.2
  const [drillDebt, setDrillDebt] = useState(null);  // wave8.51
  const { data, loading, error, refetch } = useApi(`/api/finance?view=${tab}`, [tab]);

  const tabsUi = (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {[
        ["today", "Сегодня"],
        ["month", "Месяц"],
        ["limits", "Лимиты"],
        ["goals", "Цели"],
      ].map(([k, l]) => (
        <Pill key={k} s={s} active={tab === k} onClick={() => setTab(k)}>
          {l}
        </Pill>
      ))}
    </div>
  );

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="page-title">Финансы</div>
        {tabsUi}
        <Empty s={s} text="Загружаю..." />
      </div>
    );
  }
  if (error) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="page-title">Финансы</div>
        {tabsUi}
        <ErrorBox s={s} error={error} refetch={refetch} />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Финансы</div>
      {tabsUi}

      {tab === "today" && (() => {
        const { total, items, budget } = adaptFinanceToday(data);
        return (
          <>
            <Glass s={s}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, opacity: 0.75, marginBottom: 4 }}>Потрачено сегодня</div>
                  <div style={{ fontFamily: H, fontSize: fs(32), fontWeight: 500, lineHeight: 1 }}>
                    {total.toLocaleString()} <span style={{ fontSize: fs(18), fontWeight: 400 }}>₽</span>
                  </div>
                </div>
                <span style={{ fontSize: fs(36) }}>💰</span>
              </div>
            </Glass>
            {budget && (
              <Glass s={s} accent={s.acc} style={{ padding: "12px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, fontWeight: 500, opacity: 0.75, marginBottom: 6 }}>
                  <span>Бюджет дня</span>
                  <span style={{ color: budget.pct > 85 ? s.red : budget.pct > 60 ? s.amber : undefined, fontWeight: 500 }}>{budget.day.toLocaleString()} ₽ · {budget.pct}%</span>
                </div>
                <Bar s={s} pct={budget.pct} color={budget.pct > 85 ? s.red : budget.pct > 60 ? s.amber : s.acc} />
                <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
                  Потрачено {budget.spent.toLocaleString()} ₽ · осталось {budget.left.toLocaleString()} ₽
                </div>
              </Glass>
            )}
            <SectionLabel s={s}>Транзакции</SectionLabel>
            {items.length === 0 && <Empty s={s} emoji="💚" title="Пока не тратила" text="Сегодня без трат — приятно." />}
            {items.map((x) => (
              <Glass key={x.id} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>{x.desc || "без описания"}</div>
                    <div style={{ fontSize: fs(13), color: s.tS, marginTop: 2 }}>{x.cat}</div>
                  </div>
                  <span style={{ fontSize: fs(17), color: s.text, fontWeight: 500, fontFamily: H }}>
                    {x.amt.toLocaleString()} ₽
                  </span>
                </div>
              </Glass>
            ))}
          </>
        );
      })()}

      {tab === "month" && (() => {
        const { inc, exp, balance, cats } = adaptFinanceMonth(data);
        const monthIso = data?.month || "";
        const monthLabel = formatMonth(monthIso) || monthIso;
        return (
          <>
            <Glass s={s} glow>
              <div style={{ fontSize: fs(13), color: s.tS, marginBottom: 6 }}>{monthLabel}</div>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: fs(13), color: s.acc }}>Доход</div>
                  <div style={{ fontFamily: H, fontSize: fs(20), color: s.acc, fontWeight: 500 }}>
                    {inc.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: fs(13), color: s.acc }}>Расход</div>
                  <div style={{ fontFamily: H, fontSize: fs(20), fontWeight: 500 }}>
                    {exp.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: fs(13), color: s.acc }}>Баланс</div>
                  <div
                    style={{
                      fontFamily: H, fontSize: fs(20),
                      color: balance >= 0 ? s.acc : s.red, fontWeight: 500,
                    }}
                  >
                    {balance >= 0 ? "+" : ""}
                    {balance.toLocaleString()} ₽
                  </div>
                </div>
              </div>
            </Glass>
            <SectionLabel s={s}>По категориям</SectionLabel>
            {cats.length === 0 && <Empty s={s} text="За этот месяц расходов нет" />}
            {cats.map((c, i) => {
              const pct = c.pct ?? (c.limit ? Math.round((c.spent / c.limit) * 100) : 0);
              const clr = pct > 85 ? s.red : pct > 60 ? s.amber : s.acc;
              const catFull = c.raw?.full || c.raw?.emoji + " " + c.raw?.name || c.name;
              return (
                <Glass
                  key={i} s={s}
                  style={{ padding: "10px 14px", marginBottom: 4, cursor: "pointer" }}
                  onClick={() => setDrillCat({ full: catFull, display: c.name, month: monthIso })}
                >
                  <div
                    style={{
                      display: "flex", justifyContent: "space-between",
                      fontSize: fs(16), color: s.text, fontWeight: 500, marginBottom: 6,
                    }}
                  >
                    <span>{c.name}</span>
                    <span style={{ color: clr, fontWeight: 500 }}>
                      {c.spent.toLocaleString()} ₽
                    </span>
                  </div>
                  {c.limit != null && <Bar s={s} pct={pct} color={clr} />}
                </Glass>
              );
            })}
          </>
        );
      })()}

      {tab === "limits" && (() => {
        const cats = adaptFinanceLimits(data);
        return (
          <>
            {cats.length === 0 && <Empty s={s} text="Нет активных лимитов" />}
            {cats.map((c, i) => {
              const clr = c.zone === "red" ? s.red : c.zone === "yellow" ? s.amber : s.acc;
              const catFull = c.raw?.full || (c.raw?.emoji ? `${c.raw.emoji} ${c.raw?.name || ""}`.trim() : c.name);
              const monthIso = data?.month || new Date().toISOString().slice(0, 7);
              return (
                <Glass
                  key={i} s={s}
                  style={{ padding: "10px 14px", marginBottom: 4, cursor: "pointer" }}
                  onClick={() => setDrillCat({ full: catFull, display: c.name, month: monthIso })}
                >
                  <div
                    style={{
                      display: "flex", justifyContent: "space-between",
                      fontSize: fs(16), color: s.text, fontWeight: 500, marginBottom: 6,
                    }}
                  >
                    <span>{c.name}</span>
                    <span style={{ color: clr, fontWeight: 500 }}>{c.pct}%</span>
                  </div>
                  <Bar s={s} pct={c.pct} color={clr} />
                  <div style={{ fontSize: fs(13), color: s.tS, marginTop: 5 }}>
                    {c.spent.toLocaleString()} ₽ / {c.limit.toLocaleString()} ₽
                  </div>
                </Glass>
              );
            })}
          </>
        );
      })()}

      {tab === "goals" && (() => {
        const { debts, goals, closedDebts, closedGoals } = adaptFinanceGoals(data);
        const fmtClosed = (iso) => {
          if (!iso) return "";
          const [y, m, d] = iso.split("-");
          const months = ["янв","фев","мар","апр","мая","июня","июля","авг","сен","окт","ноя","дек"];
          return `${parseInt(d,10)} ${months[parseInt(m,10)-1] || m} ${y}`;
        };
        return (
          <>
            <SectionLabel s={s}>Долги</SectionLabel>
            {debts.length === 0 && <Empty s={s} text="Долгов нет 🌿" />}
            {debts.map((d, i) => (
              <Glass
                key={i} s={s} accent={s.amber}
                style={{ padding: "10px 14px", marginBottom: 4, cursor: "pointer" }}
                onClick={() => setDrillDebt(d)}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>{d.n}</span>
                  <span style={{ fontSize: fs(16), color: s.red, fontWeight: 500, fontFamily: H }}>
                    {d.left.toLocaleString()} ₽
                  </span>
                </div>
                <div style={{ fontSize: fs(13), color: s.tS, marginTop: 3 }}>
                  {d.by && d.by !== "—" ? `до ${d.by}` : "без срока"}
                  {d.monthly > 0 ? ` · ${d.monthly.toLocaleString()} ₽/мес` : ""}
                </div>
                {d.total > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <Bar s={s} pct={(1 - d.left / d.total) * 100} color={s.amber} />
                  </div>
                )}
              </Glass>
            ))}
            <SectionLabel s={s}>Цели</SectionLabel>
            {goals.length === 0 && <Empty s={s} text="Целей пока нет" />}
            {goals.map((g, i) => (
              <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>{g.n}</span>
                  <span style={{ fontSize: fs(16), color: s.acc, fontWeight: 500 }}>
                    {g.t.toLocaleString()} ₽
                  </span>
                </div>
                <div style={{ fontSize: fs(13), color: s.tS, marginTop: 3 }}>
                  {g.monthly > 0 ? `откладываю ${g.monthly.toLocaleString()} ₽/мес` : `после ${g.after}`}
                </div>
                {g.t > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <Bar s={s} pct={(g.s / g.t) * 100} color={s.acc} />
                  </div>
                )}
              </Glass>
            ))}
            {(closedDebts.length > 0 || closedGoals.length > 0) && (
              <>
                <SectionLabel s={s}>Закрытые</SectionLabel>
                {closedDebts.map((d, i) => (
                  <Glass key={`cd${i}`} s={s} style={{ padding: "10px 14px", marginBottom: 4, opacity: 0.75 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontSize: fs(15), color: s.text, fontWeight: 500 }}>📋 {d.n}</span>
                      <span style={{ fontSize: fs(14), color: s.tS, fontFamily: H }}>{d.total.toLocaleString()} ₽</span>
                    </div>
                    <div style={{ fontSize: fs(12), color: s.tS, marginTop: 3 }}>
                      закрыт{d.closedAt ? ` · ${fmtClosed(d.closedAt)}` : ""}
                    </div>
                  </Glass>
                ))}
                {closedGoals.map((g, i) => (
                  <Glass key={`cg${i}`} s={s} style={{ padding: "10px 14px", marginBottom: 4, opacity: 0.75 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontSize: fs(15), color: s.text, fontWeight: 500 }}>🎯 {g.n}</span>
                      <span style={{ fontSize: fs(14), color: s.tS, fontFamily: H }}>{g.t.toLocaleString()} ₽</span>
                    </div>
                    <div style={{ fontSize: fs(12), color: s.tS, marginTop: 3 }}>
                      достигнута{g.closedAt ? ` · ${fmtClosed(g.closedAt)}` : ""}
                    </div>
                  </Glass>
                ))}
              </>
            )}
          </>
        );
      })()}

      {/* wave6.1.2: drill-down sheet для категорий */}
      <Sheet
        s={s}
        open={!!drillCat}
        onClose={() => setDrillCat(null)}
        title={drillCat ? `${drillCat.display} · ${formatMonth(drillCat.month)}` : ""}
      >
        {drillCat && <CategoryDrillSheet s={s} cat={drillCat.full} month={drillCat.month} />}
      </Sheet>

      {/* wave8.51: drill-down sheet для долгов — график выплат + заметка */}
      <Sheet
        s={s}
        open={!!drillDebt}
        onClose={() => setDrillDebt(null)}
        title={drillDebt ? drillDebt.n : ""}
      >
        {drillDebt && <DebtDrillSheet s={s} debt={drillDebt} />}
      </Sheet>
    </div>
  );
}

function DebtDrillSheet({ s, debt }) {
  const sched = debt.schedule || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Glass s={s} accent={s.amber} style={{ padding: "12px 14px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontSize: fs(13), color: s.acc }}>Сумма долга</span>
          <span style={{ fontSize: fs(18), color: s.red, fontWeight: 600, fontFamily: H }}>
            {debt.total.toLocaleString()} ₽
          </span>
        </div>
        {debt.takenAt && (
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: fs(13), color: s.tS }}>
            <span>Взят</span><span style={{ color: s.text }}>{formatDate(debt.takenAt, "full")}</span>
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: fs(13), color: s.tS }}>
          <span>Срок</span><span style={{ color: s.text }}>{debt.by && debt.by !== "—" ? debt.by : "—"}</span>
        </div>
        {debt.monthly > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: fs(13), color: s.tS }}>
            <span>Платёж</span><span style={{ color: s.text }}>{debt.monthly.toLocaleString()} ₽/мес</span>
          </div>
        )}
      </Glass>

      {sched.length > 0 ? (
        <>
          <SectionLabel s={s}>График выплат</SectionLabel>
          <Glass s={s} style={{ padding: "10px 14px" }}>
            {sched.map((row, i) => (
              <div
                key={i}
                style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: fs(13), color: s.text, padding: "4px 0",
                  borderTop: i === 0 ? "none" : `1px solid ${s.brd}`,
                }}
              >
                <span style={{ color: s.tS }}>{row.month}</span>
                <span style={{ fontFamily: H }}>{row.amount.toLocaleString()} ₽</span>
              </div>
            ))}
          </Glass>
        </>
      ) : (
        <Empty s={s} chill text="График не задан — нет ежемесячного платежа" />
      )}

      {debt.note && (
        <>
          <SectionLabel s={s}>Заметка</SectionLabel>
          <Glass s={s} style={{ padding: "10px 14px", fontSize: fs(13), color: s.text }}>
            {debt.note}
          </Glass>
        </>
      )}
    </div>
  );
}

function CategoryDrillSheet({ s, cat, month }) {
  const path = `/api/finance/category?cat=${encodeURIComponent(cat)}&month=${month}`;
  const { data, loading, error, refetch } = useApi(path, [cat, month]);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const items = data?.items || [];
  const byDesc = data?.by_desc || [];
  const showSummary = byDesc.length >= 2;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* wave8.50: краткая сводка перед списком трат — группировка по описанию */}
      {showSummary && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
          <div
            style={{
              display: "flex", justifyContent: "space-between",
              fontSize: fs(13), color: s.text, fontWeight: 600,
              borderBottom: `1px solid ${s.brd}`, paddingBottom: 6, marginBottom: 6,
            }}
          >
            <span>Всего</span>
            <span>{(data?.total || 0).toLocaleString()} ₽ · {data?.count || 0} шт.</span>
          </div>
          {byDesc.map((b, i) => (
            <div
              key={i}
              style={{
                display: "flex", justifyContent: "space-between",
                fontSize: fs(13), color: s.tS, padding: "2px 0",
              }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginRight: 8 }}>
                {b.name}
              </span>
              <span style={{ color: s.text, fontFamily: H, flexShrink: 0 }}>
                {b.amount.toLocaleString()} ₽
              </span>
            </div>
          ))}
        </Glass>
      )}
      {!showSummary && (
        <div style={{ fontSize: fs(13), color: s.tS, marginBottom: 4 }}>
          Всего: <span style={{ color: s.text, fontWeight: 500 }}>{(data?.total || 0).toLocaleString()} ₽</span> · {data?.count || 0} шт.
        </div>
      )}
      {items.length === 0 && <Empty s={s} emoji="🌿" title="Пусто" text="Тут трат нет." />}
      {items.map((it) => (
        <Glass key={it.id} s={s} style={{ padding: "10px 14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>{it.desc || "—"}</span>
            <span style={{ fontSize: fs(16), color: s.text, fontWeight: 500, fontFamily: H }}>
              {it.amount.toLocaleString()} ₽
            </span>
          </div>
          <div style={{ fontSize: fs(13), color: s.tS, marginTop: 3 }}>{formatDate(it.date)}</div>
        </Glass>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — LISTS
// ═══════════════════════════════════════════════════════════════

// wave8.47: шапка секции Чеклиста — карточка родительской задачи без стекла.
function ParentTaskHeader({ s, title, task }) {
  const cat = task?.cat || "";
  const prio = task?.prio || null;
  const overdue = task?.status === "overdue";
  const metaParts = [];
  if (task?.date) metaParts.push(task.date);
  if (task?.time) metaParts.push(task.time);
  if (task?.rpt) metaParts.push(task.rpt);
  return (
    <div style={{ padding: "10px 4px 6px", marginTop: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          flex: 1, minWidth: 0,
          fontFamily: H, fontSize: fs(16), color: s.text, fontWeight: 600,
          wordBreak: "break-word",
        }}>
          {title}
        </span>
        {cat && (
          <span style={{
            display: "inline-flex", alignItems: "center",
            padding: "3px 9px", borderRadius: 10,
            fontSize: fs(13), background: `${s.acc}33`, color: s.text, fontWeight: 500,
            flexShrink: 0, whiteSpace: "nowrap",
          }}>
            {String(cat).split(" ")[0]}
          </span>
        )}
        {prio && <PrioDot s={s} prio={prio} />}
      </div>
      {metaParts.length > 0 && (
        <div style={{
          fontSize: fs(13), color: overdue ? s.red : s.tS,
          marginTop: 3, display: "flex", gap: 8, flexWrap: "wrap",
        }}>
          {metaParts.map((p, i) => <span key={i}>{p}</span>)}
        </div>
      )}
    </div>
  );
}

function NxLists({ s }) {
  const [tab, setTab] = useState("buy");
  const [q, setQ] = useState("");
  const qEnc = encodeURIComponent(q || "");
  const path = q ? `/api/lists?type=${tab}&q=${qEnc}` : `/api/lists?type=${tab}`;
  const { data, loading, error, refetch } = useApi(path, [tab, q]);
  const apiItems = loading || error ? [] : adaptLists(data);

  // wave8.47: для Чеклиста подтягиваем задачи, чтобы отрисовать шапку секции
  // как карточку родительской задачи (cat справа + prio + дата снизу).
  const tasksPath = tab === "check" ? "/api/tasks?filter=all" : null;
  const { data: tasksData } = useApi(tasksPath, [tab]);
  const parentByTitle = useMemo(() => {
    if (!tasksData) return {};
    const map = {};
    for (const t of adaptTasks(tasksData)) {
      if (t.title) map[t.title] = t;
    }
    return map;
  }, [tasksData]);

  // локальные оптимистик-апдейты, сбрасываем при смене tab/q
  const [overrides, setOverrides] = useState({});
  useEffect(() => { setOverrides({}); }, [tab, q]);

  const items = apiItems.map((x) => (x.id in overrides ? { ...x, done: overrides[x.id] } : x));

  const toggleDone = async (item) => {
    if (item.done) return; // повторного снятия нет в API — просто игнор
    setOverrides((prev) => ({ ...prev, [item.id]: true }));
    try {
      await apiPost(`/api/lists/${item.id}/done`);
      setTimeout(refetch, 500);
    } catch (e) {
      setOverrides((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      alert("Не удалось отметить");
    }
  };

  const emptyText = (
    tab === "buy" ? "Списков пока нет 📝" :
    tab === "check" ? "Нет активных чеклистов 📋" :
    "Инвентарь пуст 📦"
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Списки</div>
      <div style={{ display: "flex", gap: 6 }}>
        {[
          ["buy", "🛒 Покупки"],
          ["check", "📋 Чеклист"],
          ["inv", "📦 Инвентарь"],
        ].map(([k, l]) => (
          <Pill key={k} s={s} active={tab === k} onClick={() => setTab(k)}>
            {l}
          </Pill>
        ))}
      </div>
      <SearchInput s={s} value={q} onChange={setQ} placeholder="Поиск" />
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && items.length === 0 && (
        <Glass s={s} style={{ padding: "24px 14px", textAlign: "center" }}>
          <div style={{ fontSize: fs(14), color: s.text }}>{emptyText}</div>
        </Glass>
      )}
      {!loading && !error && (tab === "check" ? groupByField(items, "group") : groupByCat(items)).map(([catName, group]) => (
        <React.Fragment key={catName || "—"}>
          {catName && tab === "check" ? (
            <ParentTaskHeader s={s} title={catName} task={parentByTitle[catName]} />
          ) : (
            catName && <SectionLabel s={s}>{catName}</SectionLabel>
          )}
          {group.map((x) => (
            tab === "inv" ? (
              <Glass key={x.id} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{
                    flex: 1, fontSize: fs(16), color: s.text, fontWeight: 500,
                    wordBreak: "break-word",
                  }}>
                    {x.name}
                  </span>
                  {x.qty != null && (
                    <span style={{ fontSize: fs(13), color: s.acc, fontWeight: 500, flexShrink: 0 }}>
                      {x.qty} шт
                    </span>
                  )}
                  {x.cat && (
                    <span style={{
                      display: "inline-flex", alignItems: "center",
                      padding: "3px 9px", borderRadius: 10,
                      fontSize: fs(13), background: `${s.acc}33`, color: s.text, fontWeight: 500,
                      flexShrink: 0, whiteSpace: "nowrap",
                    }}>
                      {x.cat}
                    </span>
                  )}
                </div>
                {x.exp && (
                  <div style={{ fontSize: fs(13), color: s.tS, marginTop: 3 }}>до {x.exp}</div>
                )}
              </Glass>
            ) : (
              <Glass
                key={x.id}
                s={s}
                style={{
                  padding: "10px 14px", marginBottom: 4, opacity: x.done ? 0.5 : 1,
                  cursor: "pointer", display: "flex", alignItems: "center", gap: 10,
                }}
                onClick={() => toggleDone(x)}
              >
                <Chk s={s} done={x.done} />
                <span style={{
                  flex: 1, minWidth: 0,
                  fontSize: fs(16), color: s.text, fontWeight: 500,
                  wordBreak: "break-word",
                  textDecoration: x.done ? "line-through" : "none",
                }}>
                  {x.name}
                </span>
                {x.cat && (
                  <span style={{
                    display: "inline-flex", alignItems: "center",
                    padding: "3px 9px", borderRadius: 10,
                    fontSize: fs(13), background: `${s.acc}33`, color: s.text, fontWeight: 500,
                    flexShrink: 0, whiteSpace: "nowrap",
                  }}>
                    {x.cat}
                  </span>
                )}
              </Glass>
            )
          ))}
        </React.Fragment>
      ))}
    </div>
  );
}

function SearchInput({ s, value, onChange, placeholder }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: s.card,
        borderRadius: 12,
        border: `1px solid ${s.brd}`,
        padding: "8px 12px",
        backdropFilter: "blur(10px)",
      }}
    >
      <Search size={fs(14)} color={s.tS} />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          flex: 1,
          background: "transparent",
          border: "none",
          outline: "none",
          color: s.text,
          fontFamily: B,
          fontSize: fs(13),
        }}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — MEMORY
// ═══════════════════════════════════════════════════════════════

function NxMemory({ s, openAdhd }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const params = [];
  if (cat !== "all") params.push(`cat=${encodeURIComponent(cat)}`);
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const path = "/api/memory" + (params.length ? "?" + params.join("&") : "");
  const { data, loading, error, refetch } = useApi(path, [cat, q]);
  const view = loading || error ? { items: [], categories: [] } : adaptMemory(data);
  const cats = ["all", ...view.categories];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Память</div>
      <Glass
        s={s}
        accent={s.acc}
        onClick={openAdhd}
        style={{ padding: "10px 14px" }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <div style={{ fontSize: fs(11), color: s.acc, fontWeight: 500 }}>🦋 СДВГ-профиль</div>
            <div style={{ fontSize: fs(12), color: s.text, marginTop: 2 }}>
              Персональные паттерны и стратегии
            </div>
          </div>
          <ChevronRight size={fs(16)} color={s.tS} />
        </div>
      </Glass>
      <SearchInput s={s} value={q} onChange={setQ} placeholder="Поиск по памяти" />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {cats.map((c) => (
          <Pill key={c} s={s} active={cat === c} onClick={() => setCat(c)}>
            {c === "all" ? "Все" : c}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && view.items.length === 0 && (
        <Empty s={s} emoji="🧠" title="Память пуста" text="Тут будут твои заметки и паттерны." />
      )}
      {!loading && !error && view.items.map((m) => (
        <Glass key={m.id} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
          <div style={{ fontSize: fs(13), color: s.text }}>{m.text}</div>
          <div style={{ fontSize: fs(10), color: s.tS, marginTop: 2 }}>{m.cat}</div>
        </Glass>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — CALENDAR (default=month)
// ═══════════════════════════════════════════════════════════════

const RU_MONTHS_FULL = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];
const RU_MONTHS_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

function NxCal({ s }) {
  const now = new Date();
  const [view, setView] = useState("month");
  const [monthStr, setMonthStr] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`
  );
  const [picked, setPicked] = useState(now.getDate());
  const [doneIds, setDoneIds] = useState({});
  const daysShort = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  const { data, loading, error, refetch } = useApi(`/api/calendar?month=${monthStr}`, [monthStr]);
  const { tasksByDay } = loading || error
    ? { tasksByDay: {} }
    : adaptCalendar(data);

  const toggleCalTask = async (id) => {
    if (!id || doneIds[id]) return;
    setDoneIds((p) => ({ ...p, [id]: true }));
    try {
      await apiPost(`/api/tasks/${id}/done`);
      refetch();
    } catch (_) {
      setDoneIds((p) => ({ ...p, [id]: false }));
    }
  };

  const year = parseInt(monthStr.slice(0, 4), 10);
  const month0 = parseInt(monthStr.slice(5, 7), 10) - 1;
  const title = `${RU_MONTHS_FULL[month0]} ${year}`;

  const firstDate = new Date(year, month0, 1);
  // Пн=1 ... Вс=7 (в JS Вс=0). Позиция первого числа (0..6 для Пн..Вс)
  const jsDow = firstDate.getDay();
  const monthStart = (jsDow + 6) % 7; // 0=Пн
  const daysInMonth = new Date(year, month0 + 1, 0).getDate();
  const todayKey = now.getDate();
  const todayMonthMatch =
    now.getFullYear() === year && now.getMonth() === month0;

  const weeks = [];
  let cur = [];
  for (let i = 0; i < monthStart; i++) cur.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    cur.push(d);
    if (cur.length === 7) {
      weeks.push(cur);
      cur = [];
    }
  }
  if (cur.length) {
    while (cur.length < 7) cur.push(null);
    weeks.push(cur);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span className="page-title">{title}</span>
        <div style={{ display: "flex", gap: 6 }}>
          <Pill s={s} active={view === "week"} onClick={() => setView("week")}>
            Неделя
          </Pill>
          <Pill s={s} active={view === "month"} onClick={() => setView("month")}>
            Месяц
          </Pill>
        </div>
      </div>
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {view === "month" && (
        <Glass s={s} style={{ padding: "10px 10px" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(7, 1fr)",
              gap: 3,
              marginBottom: 4,
            }}
          >
            {daysShort.map((d) => (
              <div
                key={d}
                style={{ textAlign: "center", fontSize: fs(10), color: s.tS, padding: 3 }}
              >
                {d}
              </div>
            ))}
          </div>
          {weeks.map((w, wi) => (
            <div
              key={wi}
              style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 3, marginBottom: 3 }}
            >
              {w.map((d, di) => {
                if (!d) return <div key={di} />;
                const isToday = todayMonthMatch && d === todayKey;
                const isPicked = d === picked;
                const has = tasksByDay[d];
                const count = Array.isArray(has) ? has.length : 0;
                return (
                  <div
                    key={di}
                    onClick={() => setPicked(d)}
                    style={{
                      textAlign: "center",
                      padding: "6px 2px",
                      borderRadius: 8,
                      background: isPicked
                        ? `${s.acc}30`
                        : isToday
                        ? `${s.acc}14`
                        : "transparent",
                      border: isToday ? `1px solid ${s.acc}55` : "1px solid transparent",
                      cursor: "pointer",
                      minHeight: 36,
                      position: "relative",
                    }}
                  >
                    <div
                      style={{
                        fontSize: fs(13),
                        fontWeight: isToday || isPicked ? 500 : 400,
                        color: isToday || isPicked ? s.acc : s.text,
                        fontFamily: H,
                      }}
                    >
                      {d}
                    </div>
                    {count > 0 && (
                      <>
                        <div
                          style={{
                            width: 4, height: 4, borderRadius: 2,
                            background: s.acc,
                            margin: "3px auto 0",
                          }}
                        />
                        {count > 1 && (
                          <span style={{
                            position: "absolute", top: 2, right: 3,
                            fontSize: fs(8), color: s.tS,
                          }}>{count}</span>
                        )}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </Glass>
      )}

      {view === "week" && (() => {
        // Wave6.6.2: простой Week-вид — 7 дней подряд с задачами списком
        const today = new Date();
        const startWeekDate = new Date(today);
        const dow = (today.getDay() + 6) % 7;  // Пн=0
        startWeekDate.setDate(today.getDate() - dow);
        const weekDays = [];
        for (let i = 0; i < 7; i++) {
          const d = new Date(startWeekDate);
          d.setDate(startWeekDate.getDate() + i);
          const dayNum = d.getDate();
          const isSameMonth =
            d.getFullYear() === year && d.getMonth() === month0;
          weekDays.push({
            dayNum, isSameMonth, date: d,
            label: `${daysShort[i]}, ${dayNum}`,
            tasks: isSameMonth ? (tasksByDay[dayNum] || []) : [],
            isToday: d.toDateString() === today.toDateString(),
          });
        }
        return (
          <Glass s={s} style={{ padding: "10px 12px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {weekDays.map((wd, i) => {
                const isPicked = wd.isSameMonth && wd.dayNum === picked;
                return (
                  <div
                    key={i}
                    onClick={() => { if (wd.isSameMonth) setPicked(wd.dayNum); }}
                    style={{
                      padding: "8px 10px", borderRadius: 8,
                      cursor: wd.isSameMonth ? "pointer" : "default",
                      opacity: wd.isSameMonth ? 1 : 0.4,
                      background: isPicked
                        ? `${s.acc}30`
                        : wd.isToday ? `${s.acc}18` : "transparent",
                      border: `1px solid ${isPicked ? s.acc + "99" : wd.isToday ? s.acc + "55" : s.brd}`,
                    }}>
                    <div style={{
                      display: "flex", justifyContent: "space-between",
                      fontSize: fs(12),
                      color: isPicked || wd.isToday ? s.acc : s.text, fontWeight: 500,
                    }}>
                      <span>{wd.label}</span>
                      <span style={{ color: s.tS }}>{wd.tasks.length > 0 ? `${wd.tasks.length} шт.` : ""}</span>
                    </div>
                    {wd.tasks.length > 0 && (
                      <div style={{ marginTop: 4 }}>
                        {wd.tasks.slice(0, 3).map((t, j) => (
                          <div key={j} style={{ fontSize: fs(11), color: s.tS, marginTop: 2 }}>
                            • {t.time ? `${t.time} ` : ''}{t.title}
                          </div>
                        ))}
                        {wd.tasks.length > 3 && (
                          <div style={{ fontSize: fs(10), color: s.tS, marginTop: 2 }}>
                            и ещё {wd.tasks.length - 3}…
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Glass>
        );
      })()}
      <SectionLabel s={s}>
        {picked} {RU_MONTHS_GEN[month0]}
      </SectionLabel>
      {loading && <Empty s={s} text="Загружаю..." />}
      {!loading && !tasksByDay[picked] && (
        <Empty s={s} chill emoji="📅" text="В этот день всё свободно" />
      )}
      {!loading && (tasksByDay[picked] || []).map((t, i) => (
        <TaskRow
          key={t.id || i}
          s={s}
          t={t}
          done={!!doneIds[t.id]}
          onToggle={() => toggleCalTask(t.id)}
          onOpen={() => {}}
          withTime={!!t.time}
        />
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — Accuracy bottom sheet
// ═══════════════════════════════════════════════════════════════

function CashSheet({ s, pnl, onClose, onPaid }) {
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [warn, setWarn] = useState(null);
  const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";

  if (!pnl) return null;

  const submit = async (force = false) => {
    const a = parseFloat(amount);
    if (!a || a <= 0) return;
    setBusy(true); setWarn(null);
    try {
      const r = await fetch("/api/arcana/finance/pay_salary", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": initData,
        },
        body: JSON.stringify({ amount: a, force }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert("Не получилось: " + (data.detail || r.status)); return; }
      if (data.ok === false && data.warning === "low_cash") {
        setWarn(data.message || "В кассе меньше суммы. Всё равно?");
        return;
      }
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.("success"); } catch (_) {}
      onPaid?.();
    } catch (e) { alert("Ошибка: " + e.message); }
    finally { setBusy(false); }
  };

  const inc = pnl.income_breakdown || {};
  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <div className="card-h">
          <span className="card-title" style={{ fontSize: 22 }}>🏦 Касса · {pnl.period?.month}/{pnl.period?.year}</span>
        </div>
        <div style={{ fontSize: fs(28), fontFamily: H, fontWeight: 500, marginTop: 4 }}>
          {(pnl.cash_balance ?? 0).toLocaleString()}₽
        </div>
        <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 14 }}>остаток</div>

        <div style={{ fontFamily: H, fontSize: fs(15), marginBottom: 4 }}>📥 Доход: {(pnl.income_month ?? 0).toLocaleString()}₽</div>
        <div style={{ fontSize: fs(12), color: s.tS, marginBottom: 8, lineHeight: 1.5 }}>
          {inc.sessions?.amount > 0 && <div>Сеансы: {inc.sessions.amount.toLocaleString()}₽ ({inc.sessions.count} шт)</div>}
          {inc.rituals?.amount > 0 && <div>Ритуалы: {inc.rituals.amount.toLocaleString()}₽ ({inc.rituals.count} шт)</div>}
        </div>
        <div style={{ fontFamily: H, fontSize: fs(15), marginBottom: 4 }}>📤 Расход: {(pnl.expenses_month ?? 0).toLocaleString()}₽</div>
        <div style={{ fontSize: fs(12), color: s.tS, marginBottom: 8, lineHeight: 1.5 }}>
          {(pnl.expenses_by_category || []).map((c, i) => (
            <div key={i}>{c.name}: {c.amount.toLocaleString()}₽</div>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
          <Glass s={s} style={{ padding: "10px 12px" }}>
            <div style={{ fontSize: fs(10), color: s.tS }}>Прибыль месяца</div>
            <div style={{ fontSize: fs(16), fontFamily: H, color: pnl.profit_month >= 0 ? s.acc : s.red }}>
              {pnl.profit_month.toLocaleString()}₽
            </div>
          </Glass>
          <Glass s={s} style={{ padding: "10px 12px" }}>
            <div style={{ fontSize: fs(10), color: s.tS }}>Выплачено себе (мес)</div>
            <div style={{ fontSize: fs(16), fontFamily: H }}>
              {(pnl.salary_month ?? 0).toLocaleString()}₽
            </div>
          </Glass>
        </div>
        {(pnl.debt_money > 0 || pnl.barter_open_count > 0) && (
          <div style={{ fontSize: fs(12), color: s.tS, marginTop: 10 }}>
            📋 Должны: {[
              pnl.debt_money > 0 && `${pnl.debt_money.toLocaleString()}₽`,
              pnl.barter_open_count > 0 && `${pnl.barter_open_count} бартер`,
            ].filter(Boolean).join(" + ")}
          </div>
        )}

        <div style={{ marginTop: 14, fontSize: fs(11), color: s.tS }}>💸 Выплатить себе</div>
        <Input s={s} value={amount} onChange={setAmount} placeholder="20000" type="number" />
        {warn && <div style={{ fontSize: fs(12), color: s.amber, marginTop: 8 }}>{warn}</div>}
        <SubmitBtn
          s={s}
          disabled={!amount || busy}
          label={busy ? "Выплачиваю..." : (warn ? "Всё равно выплатить" : "💸 Выплатить")}
          onClick={() => submit(!!warn)}
        />
      </div>
    </>
  ), document.body);
}


function StatsSheet({ s, onClose }) {
  const [scope, setScope] = useState("all");
  const [openMonth, setOpenMonth] = useState(null);
  const { data, loading } = useApi("/api/arcana/stats");

  const pct = data?.[`accuracy_pct_${scope === "all" ? "overall" : scope}`] ?? 0;
  const breakdown = data?.[`breakdown_${scope === "all" ? "overall" : scope}`]
    || { yes: 0, half: 0, no: 0 };
  const total = breakdown.yes + breakdown.half + breakdown.no;
  const pendingS = data?.pending_sessions_count ?? 0;
  const pendingR = data?.pending_rituals_count ?? 0;
  const pendingTotal = pendingS + pendingR;
  const months = data?.by_month || [];
  const cats = data?.by_category || [];
  const avgS = data?.avg_check_delay_sessions_days;
  const avgR = data?.avg_check_delay_rituals_days;
  const byClientType = data?.by_client_type || {};
  const byPaymentSource = data?.by_payment_source || {};

  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <div className="card-h">
          <span className="card-title" style={{ fontSize: 22 }}>Статистика практики</span>
          <span className="card-meta">за всё время</span>
        </div>
        <div className="acc-tabs">
          {[["all", "Всё"], ["sessions", "🃏 Расклады"], ["rituals", "🕯 Ритуалы"]].map(([k, l]) => (
            <div key={k} className={`acc-tab${scope === k ? " active" : ""}`} onClick={() => setScope(k)}>{l}</div>
          ))}
        </div>
        {loading ? (
          <Empty s={s} text="Загружаю..." />
        ) : (
          <>
            <div className="acc-big">{pct}%</div>
            <div className="acc-big-sub">{total} проверено</div>
            <div className="acc-break">
              <div className="acc-break-cell yes"><div className="v">{breakdown.yes}</div><div className="l">✅ да</div></div>
              <div className="acc-break-cell half"><div className="v">{breakdown.half}</div><div className="l">〰️ част.</div></div>
              <div className="acc-break-cell no"><div className="v">{breakdown.no}</div><div className="l">❌ нет</div></div>
            </div>

            {pendingTotal > 0 && (
              <div className="glass" style={{ padding: "12px 14px", marginTop: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 14 }}>⏳</span>
                  <span style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 16 }}>
                    Ждут вердикта
                  </span>
                </div>
                <div style={{ fontSize: 13, opacity: 0.8, lineHeight: 1.5 }}>
                  {pendingS > 0 && (
                    <div>🃏 {pendingS} {plural(pendingS, "расклад", "расклада", "раскладов")}</div>
                  )}
                  {pendingR > 0 && (
                    <div>🕯 {pendingR} {plural(pendingR, "ритуал", "ритуала", "ритуалов")}</div>
                  )}
                </div>
                <div style={{ fontSize: 11, opacity: 0.55, marginTop: 8, fontStyle: "italic" }}>
                  Открой в Раскладах или Ритуалах чтобы поставить вердикт →
                </div>
              </div>
            )}

            {months.length > 0 && (
              <div className="glass" style={{ padding: "12px 14px", marginTop: 10 }}>
                <div className="card-h">
                  <span className="card-title">По месяцам</span>
                </div>
                {months.map((m) => {
                  const isOpen = openMonth === m.month;
                  const showSess = scope !== "rituals";
                  const showRit = scope !== "sessions";
                  const total = (showSess ? m.sessions_total : 0) + (showRit ? m.rituals_total : 0);
                  if (total === 0) return null;
                  const checked = (showSess ? m.sessions_checked : 0) + (showRit ? m.rituals_checked : 0);
                  const pctVal = checked
                    ? Math.round(((showSess ? m.sessions_pct * m.sessions_checked : 0)
                                 + (showRit ? m.rituals_pct * m.rituals_checked : 0)) / checked)
                    : 0;
                  return (
                    <div key={m.month}
                         onClick={() => setOpenMonth(isOpen ? null : m.month)}
                         style={{ cursor: "pointer", padding: "8px 0", borderBottom: `1px solid ${s.brd}` }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                        <span style={{ fontWeight: 500 }}>{m.label}</span>
                        <span style={{ color: s.acc }}>
                          {pctVal}%
                          {showSess && m.sessions_total > 0 && ` · ${m.sessions_total} трип.`}
                          {showRit && m.rituals_total > 0 && ` · ${m.rituals_total} рит.`}
                        </span>
                      </div>
                      <Bar s={s} pct={pctVal} color={s.acc} />
                      {isOpen && (
                        <div style={{ marginTop: 6, fontSize: 11, color: s.tM, display: "flex", gap: 12 }}>
                          {showSess && (
                            <span>🃏 ✅{m.sessions_yes} 〰️{m.sessions_half} ❌{m.sessions_no}</span>
                          )}
                          {showRit && (
                            <span>🕯 ✅{m.rituals_yes} 〰️{m.rituals_half} ❌{m.rituals_no}</span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {cats.length > 0 && scope !== "rituals" && (
              <div className="glass" style={{ padding: "12px 14px", marginTop: 10 }}>
                <div className="card-h">
                  <span className="card-title">По темам сессий</span>
                </div>
                {cats.map((c) => (
                  <div key={c.category} style={{ padding: "6px 0" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                      <span>{c.category}</span>
                      <span style={{ color: s.tM }}>
                        {c.total} трип. · {c.checked} проверено · {c.pct}%
                      </span>
                    </div>
                    <Bar s={s} pct={c.pct} color={s.acc} />
                  </div>
                ))}
              </div>
            )}

            {scope !== "rituals" && Object.keys(byClientType).length > 0 && (
              <div className="glass" style={{ padding: "12px 14px", marginTop: 10 }}>
                <div className="card-h">
                  <span className="card-title">По типу клиента</span>
                </div>
                {["🌟 Self", "🤝 Платный", "🎁 Бесплатный"].map((t) => {
                  const b = byClientType[t];
                  if (!b || b.sessions === 0) return null;
                  return (
                    <div key={t} style={{ padding: "6px 0" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                        <span>{t}</span>
                        <span style={{ color: s.tM }}>
                          {b.sessions} {plural(b.sessions, "сессия", "сессии", "сессий")}
                          {b.checked > 0 && ` · ${b.pct}%`}
                        </span>
                      </div>
                      {b.checked > 0 && <Bar s={s} pct={b.pct} color={s.acc} />}
                    </div>
                  );
                })}
              </div>
            )}

            {Object.keys(byPaymentSource).length > 0 && (() => {
              const rows = [
                ["💵 Наличные", byPaymentSource["💵 Наличные"]],
                ["💳 Карта",    byPaymentSource["💳 Карта"]],
                ["🔄 Бартер",   byPaymentSource["🔄 Бартер"]],
                ["🎁 Подарок",  byPaymentSource["🎁 Подарок"]],
              ].filter(([, b]) => b && ((b.sessions || 0) + (b.rituals || 0) > 0));
              if (rows.length === 0) return null;
              return (
                <div className="glass" style={{ padding: "12px 14px", marginTop: 10 }}>
                  <div className="card-h">
                    <span className="card-title">Способы оплаты</span>
                  </div>
                  {rows.map(([label, b]) => {
                    const ns = b.sessions || 0;
                    const nr = b.rituals || 0;
                    const isBarter = label === "🔄 Бартер";
                    const isGift = label === "🎁 Подарок";
                    return (
                      <div key={label} style={{ padding: "6px 0", fontSize: 13 }}>
                        <div style={{ display: "flex", justifyContent: "space-between" }}>
                          <span>{label}</span>
                          <span style={{ color: s.tM }}>
                            {ns > 0 && `${ns} с.`}{ns > 0 && nr > 0 && " · "}{nr > 0 && `${nr} р.`}
                            {!isBarter && !isGift && b.total_rub > 0 &&
                              ` · ${b.total_rub.toLocaleString("ru-RU")}₽`}
                          </span>
                        </div>
                        {isBarter && b.items && b.items.length > 0 && (
                          <div style={{ fontSize: 11, color: s.tM, marginTop: 2, fontStyle: "italic" }}>
                            {b.items.slice(0, 5).join(", ")}
                            {b.items.length > 5 && ` … +${b.items.length - 5}`}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })()}

            {(avgS != null || avgR != null) && (
              <div className="glass" style={{ padding: "12px 14px", marginTop: 10 }}>
                <div className="card-h">
                  <span className="card-title">⏱ Скорость проверки</span>
                </div>
                {avgS != null && (
                  <div style={{ fontSize: 12, color: s.text, marginTop: 4 }}>
                    Расклады: проверяешь в среднем через {avgS} {avgS === 1 ? "день" : "дн."}
                  </div>
                )}
                {avgR != null && (
                  <div style={{ fontSize: 12, color: s.text, marginTop: 4 }}>
                    Ритуалы: проверяешь в среднем через {avgR} {avgR === 1 ? "день" : "дн."}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  ), document.body);
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — MY DAY (с фазой луны)
// ═══════════════════════════════════════════════════════════════

function ArDay({ s, openClient, navigate, openMoonPhases }) {
  const [done, setDone] = useState({});
  const [accSheet, setAccSheet] = useState(false);
  const [cashSheet, setCashSheet] = useState(false);
  const pnlApi = useApi('/api/arcana/finance/pnl');
  const { data, loading, error, refetch } = useApi('/api/arcana/today');
  const weatherApi = useApi('/api/weather');
  const rawSessions = data?.sessions_today?.length || 0;
  const rawWorks = data?.works_today?.length || 0;
  const tipApi = useApi(data ? `/api/arcana/tip?sessions=${rawSessions}&works=${rawWorks}` : null, [rawSessions, rawWorks]);

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;

  const a = adaptArcanaToday(data);
  const moon = a.moon;
  const worksTotal = a.worksToday.length + a.worksOverdue.length;
  const worksDoneToday = (data?.works_done_today ?? Object.values(done).filter(Boolean).length);
  const worksTotalToday = data?.works_total_today ?? worksTotal;
  const incomeMonth = data?.income_month ?? a.monthBlock.inc;
  const accPct = data?.accuracy_pct ?? a.accuracy;
  const accChecked = data?.accuracy_checked ?? 0;
  const accTotal = data?.accuracy_total ?? 0;
  const pendingSessions = data?.pending_sessions ?? 0;
  const pendingRituals = data?.pending_rituals ?? 0;
  const pendingTotal = pendingSessions + pendingRituals;

  const allRows = [
    ...a.worksOverdue.map((o) => ({ ...o, _over: true })),
    ...a.worksToday.map((w) => ({ ...w, _over: false })),
  ];

  return (
    <>
      <div className="hero glass glow">
        <div className="hero-h">
          <div>
            <div className="hero-title">Мой день</div>
            <div style={{ fontSize: 13, opacity: 0.65, marginTop: 4, fontWeight: 500 }}>{tipApi.data?.tip ? `${tipApi.data.tip} 🌙` : "сегодня в практике спокойно 🌙"}</div>
          </div>
          <div className="hero-meta">
            <div>{a.date}</div>
            {weatherApi.data && <div style={{ marginTop: 3 }}>
              {WEATHER_ICON[weatherApi.data.kind] || "🌤"} {weatherApi.data.temp > 0 ? "+" : ""}{weatherApi.data.temp}° · {shortCity(weatherApi.data.city)}
            </div>}
          </div>
        </div>
        <div className="hero-metrics">
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("work")}>
            <Metric s={s} v={worksDoneToday} unit={`/${worksTotalToday}`} sub="работы" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => setCashSheet(true)}>
            <Metric s={s} v={incomeMonth >= 1000 ? `${Math.round(incomeMonth / 1000)}к` : incomeMonth} unit="₽" sub="доход" accent={s.acc} />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => setAccSheet(true)}>
            <Metric s={s} v={`${accPct}%`} sub="точность" accent={s.amber} />
          </div>
        </div>
        <div className="hero-budget">
          <div
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, fontSize: 13, fontWeight: 500, cursor: "pointer" }}
            onClick={() => navigate?.("sess", { filter: "wait" })}
          >
            <span style={{ opacity: 0.75 }}>Сбылось в практике</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontWeight: 500 }}>{accChecked} из {accTotal} проверено</span>
              <span
                title="Статистика"
                onClick={(e) => { e.stopPropagation(); setAccSheet(true); }}
                style={{ cursor: "pointer", opacity: 0.7, fontSize: 14, padding: "2px 4px" }}
              >📊</span>
            </span>
          </div>
          <Bar s={s} pct={accPct} color={s.amber} />
          {(pendingSessions > 0 || pendingRituals > 0) && (
            <div
              style={{ fontSize: 12, opacity: 0.6, marginTop: 6, cursor: "pointer" }}
              onClick={() => navigate?.("sess", { filter: "wait" })}
            >
              ⏳ {[
                pendingSessions > 0 && `${pendingSessions} ${plural(pendingSessions, "расклад", "расклада", "раскладов")}`,
                pendingRituals > 0 && `${pendingRituals} ${plural(pendingRituals, "ритуал", "ритуала", "ритуалов")}`,
              ].filter(Boolean).join(" · ")} {plural(pendingSessions + pendingRituals, "ждёт", "ждут", "ждут")} проверки
            </div>
          )}
        </div>
      </div>

      <div className="glass" onClick={() => openMoonPhases?.()} style={{ cursor: "pointer" }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <div style={{ fontSize: 46, lineHeight: 1, filter: "drop-shadow(0 0 12px rgba(255,240,220,0.45))" }}>{moon.glyph}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: "var(--f-display)", fontStyle: "italic", fontSize: 18, lineHeight: 1.1 }}>{moon.name}</div>
            <div style={{ fontSize: 11, opacity: 0.7, margin: "3px 0 7px" }}>{moon.note || `${moon.days} день цикла · освещение ${moon.illum}%`}</div>
            <Bar s={s} pct={moon.illum} color={s.acc} />
          </div>
        </div>
      </div>

      <div className="glass" style={{ padding: "14px 16px" }}>
        <div className="card-h">
          <span className="card-title">Расписание</span>
          <span className="card-meta">{allRows.length === 0 ? "пусто" : `${allRows.length} на сегодня`}</span>
        </div>
        {allRows.length === 0 && (
          <div style={{ padding: "12px 0", textAlign: "center" }}>
            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4, fontFamily: H }}>Сегодня в практике спокойно 🌙</div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>Загляни во вкладку «Работы».</div>
          </div>
        )}
        {allRows.map((r) => (
          <div key={r.id} className="sched-row" style={{ cursor: "pointer" }} onClick={() => navigate?.("work")}>
            {!r._over && <span className="s-time" style={r.time ? {} : { opacity: 0.4 }}>{r.time || "—"}</span>}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="s-title">{r.title}</div>
              {r._over && (
                <div className="s-meta">
                  <span style={{ color: s.red, fontWeight: 600 }}>{r.days_ago} д назад</span>
                </div>
              )}
            </div>
            {r.cat && <div className="s-cat">{String(r.cat).split(" ")[0]}</div>}
            <PrioDot s={s} prio={r.prio} />
          </div>
        ))}
      </div>

      {accSheet && (
        <StatsSheet
          s={s}
          onClose={() => setAccSheet(false)}
        />
      )}
      {cashSheet && (
        <CashSheet
          s={s}
          pnl={pnlApi.data}
          onClose={() => setCashSheet(false)}
          onPaid={() => { setCashSheet(false); pnlApi.refetch?.(); refetch?.(); }}
        />
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — SESSIONS
// ═══════════════════════════════════════════════════════════════

// wave8: статус сессии → glyph
const SESS_STATUS_GLYPH = { wait: "⏳", proc: "🔄", part: "〰️", done: "✅" };
const SESS_STATUS_LABEL = { wait: "ждёт", proc: "в работе", part: "частично", done: "сбылось" };

function StatusTag({ s, status }) {
  const g = SESS_STATUS_GLYPH[status] || "⏳";
  const lbl = SESS_STATUS_LABEL[status] || "ждёт";
  const c = status === "done" ? "#22c55e" : status === "part" ? "#f59e0b" : status === "proc" ? s.acc : s.tM;
  return (
    <span style={{
      fontSize: fs(11), color: c, padding: "2px 8px", borderRadius: 999,
      background: `${c}1a`, border: `1px solid ${c}33`, whiteSpace: "nowrap",
    }}>{g} {lbl}</span>
  );
}

function BreakdownChips({ s, breakdown }) {
  const items = [
    { k: "yes",  ic: "✅", c: "#22c55e" },
    { k: "half", ic: "〰️", c: "#f59e0b" },
    { k: "no",   ic: "❌", c: "#ef4444" },
    { k: "wait", ic: "⏳", c: s.tM },
  ];
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
      {items.filter((it) => (breakdown?.[it.k] ?? 0) > 0).map((it) => (
        <span key={it.k} style={{
          fontSize: fs(11), color: it.c, padding: "1px 6px", borderRadius: 8,
          background: `${it.c}14`, border: `1px solid ${it.c}33`,
        }}>{it.ic} {breakdown[it.k]}</span>
      ))}
    </div>
  );
}

function ArSessions({ s, openSession, sessFilterRequest, consumeSessFilter }) {
  const [f, setF] = useState("all");
  // Внешний триггер из ArDay → выставить wait и сбросить запрос.
  useEffect(() => {
    if (sessFilterRequest && consumeSessFilter) {
      setF(sessFilterRequest);
      consumeSessFilter();
    }
  }, [sessFilterRequest, consumeSessFilter]);
  let path = "/api/arcana/sessions";
  if (f === "wait") path += "?filter=status:wait";
  else if (f === "done") path += "?filter=status:done";
  const { data, loading, error, refetch } = useApi(path, [f]);
  const list = loading || error ? [] : adaptSessions(data);

  const pinned = list.find((x) => x.status === "wait" || x.status === "proc");
  const filters = [
    { k: "all",  l: "Все" },
    { k: "wait", l: "⏳ Непроверенные" },
    { k: "done", l: "Сбылось" },
  ];
  const total = list.length;
  const waitCount = list.filter((x) => x.status === "wait" || x.status === "proc").length;

  const handleOpen = (x) => openSession({ slug: x.slug, isSolo: x.isSolo });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="page-title">Сессии</span>
        {waitCount > 0 && (
          <span style={{ fontSize: fs(11), color: s.amber }}>
            ⏳ {waitCount} в работе · {total} всего
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {filters.map((it) => (
          <Pill key={it.k} s={s} active={f === it.k} onClick={() => setF(it.k)}>
            {it.l}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && list.length === 0 && (
        <Empty s={s} emoji="🔮" title="Сессий нет" text="Пока тишина — карты ждут." />
      )}
      {!loading && !error && pinned && f === "all" && (
        <div className="glass tap" style={{
          padding: "14px 16px", marginBottom: 6, border: `1px solid ${s.acc}55`,
          background: `linear-gradient(180deg, ${s.acc}10, transparent)`,
        }} onClick={() => handleOpen(pinned)}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: fs(10), color: s.acc, marginBottom: 4 }}>📌 в работе</div>
              <div style={{ fontFamily: H, fontSize: fs(17), fontWeight: 500, lineHeight: 1.2 }}>
                {pinned.title}
              </div>
              <div style={{ fontSize: fs(11), opacity: 0.65, marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {[
                  pinned.category,
                  pinned.client && `${pinned.clientType ? pinned.clientType + " " : ""}${pinned.client}`,
                  pinned.firstDate,
                  `${pinned.tripletCount} трип.`,
                  pinned.hasBarter && "🔄 бартер",
                ]
                  .filter(Boolean).map((it, i) => <span key={i}>{it}</span>)}
                {pinned.lastDate && pinned.rawLastDate !== pinned.rawDate && (
                  <span style={{ opacity: 0.5, fontSize: fs(10) }}>· обновлено {pinned.lastDate}</span>
                )}
              </div>
              <BreakdownChips s={s} breakdown={pinned.breakdown} />
            </div>
            <StatusTag s={s} status={pinned.status} />
          </div>
        </div>
      )}
      {!loading && !error && list.filter((x) => f !== "all" || x.slug !== pinned?.slug).map((x) => (
        <div key={x.slug} className="glass tap" style={{ padding: "12px 14px", marginBottom: 6 }} onClick={() => handleOpen(x)}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: H, fontSize: fs(15), fontWeight: 500, lineHeight: 1.25 }}>
                {x.title || "—"}
              </div>
              <div style={{ fontSize: fs(11), opacity: 0.65, marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {[
                  x.category,
                  x.client && `${x.clientType ? x.clientType + " " : ""}${x.client}`,
                  x.firstDate,
                  x.tripletCount > 1 ? `${x.tripletCount} трип.` : null,
                  x.hasBarter && "🔄 бартер",
                ]
                  .filter(Boolean).map((it, i) => <span key={i}>{it}</span>)}
                {x.lastDate && x.rawLastDate !== x.rawDate && (
                  <span style={{ opacity: 0.5, fontSize: fs(10) }}>· обновлено {x.lastDate}</span>
                )}
              </div>
              {x.tripletCount > 1 && <BreakdownChips s={s} breakdown={x.breakdown} />}
            </div>
            <StatusTag s={s} status={x.status} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — CLIENTS
// ═══════════════════════════════════════════════════════════════

function ArClients({ s, openClient }) {
  const { data, loading, error, refetch } = useApi("/api/arcana/clients");
  const barterApi = useApi("/api/arcana/barter?only_open=true");
  const pnlApi = useApi("/api/arcana/finance/pnl");
  const debtsApi = useApi("/api/arcana/debts");
  const [cashSheet, setCashSheet] = useState(false);
  const [debtsSheet, setDebtsSheet] = useState(false);
  const view = loading || error
    ? { clients: [], total: 0, total_debt: 0, total_paid_all: 0 }
    : adaptClients(data);
  const total = view.total || view.clients.length;
  const debt = view.total_debt;
  const earned = view.total_paid_all || 0;
  const barterOpen = barterApi.data?.open_count || 0;
  const fmtK = (v) => v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}к` : `${v}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Glass s={s} glow>
        <span className="page-title">Клиенты</span>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <Metric s={s} v={total} sub="всего" />
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => setCashSheet(true)}>
            <Metric
              s={s}
              v={earned > 0 ? fmtK(earned) : "0"}
              unit="₽"
              sub="заработано"
              accent={earned > 0 ? s.acc : undefined}
            />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => setDebtsSheet(true)}>
            <Metric
              s={s}
              v={debt > 0 ? fmtK(debt) : "0"}
              unit="₽"
              sub="долги"
              accent={debt > 0 ? s.red : undefined}
            />
          </div>
        </div>
        {barterOpen > 0 && (
          <div style={{ fontSize: fs(11), color: s.tS, marginTop: 6 }}>
            🔄 Бартер: {barterOpen} {plural(barterOpen, "открытый", "открытых", "открытых")}
          </div>
        )}
      </Glass>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && view.clients.length === 0 && (
        <Empty s={s} emoji="👥" title="Пока без клиентов" text="Когда придёт первый — появится здесь." />
      )}
      {!loading && !error && view.clients.map((c) => (
        c.self ? (
          <SelfListCard key={c.id} client={c} onClick={() => openClient({ id: c.id })} />
        ) : (
        <Glass
          key={c.id}
          s={s}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openClient({ id: c.id })}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <ClientAvatar s={s} photoUrl={c.photoUrl} initial={c.initial} size={36} />
              <div>
                <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>
                  {c.type && <span title={c.type_full}>{c.type} </span>}
                  {c.name}
                  {c.self && (
                    <span style={{ color: s.tS, fontWeight: 400, fontSize: fs(11) }}> · я</span>
                  )}
                </div>
                <div style={{ fontSize: fs(10), color: s.tM }}>
                  {c.sessions} сеансов · {c.rituals} ритуалов
                  {c.barter_count > 0 && (
                    <> · <span style={{ color: s.acc }}>🔄 {c.barter_count} {c.barter_count === 1 ? "бартер" : "бартера"}</span></>
                  )}
                </div>
              </div>
            </div>
            {c.debt > 0 && (
              <span style={{ fontSize: fs(13), color: s.red, fontWeight: 500 }}>
                {c.debt.toLocaleString()} ₽
              </span>
            )}
          </div>
        </Glass>
        )
      ))}
      {cashSheet && pnlApi.data && (
        <CashSheet
          s={s}
          pnl={pnlApi.data}
          onClose={() => setCashSheet(false)}
          onPaid={() => { setCashSheet(false); pnlApi.refetch?.(); refetch?.(); }}
        />
      )}
      {debtsSheet && (
        <DebtsSheet
          s={s}
          data={debtsApi.data}
          onClose={() => setDebtsSheet(false)}
        />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — DEBTS SHEET
// ═══════════════════════════════════════════════════════════════

function DebtsSheet({ s, data, onClose }) {
  const [openMoney, setOpenMoney] = useState({});
  const [openBarter, setOpenBarter] = useState({});
  const money = data?.money || [];
  const barter = data?.barter || [];
  const totals = data?.totals || { money: 0, barter_items: 0 };
  const allEmpty = money.length === 0 && barter.length === 0;

  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <div className="card-h">
          <span className="card-title" style={{ fontSize: 22 }}>
            Долги · {totals.money.toLocaleString()}₽ · {totals.barter_items} бартер
          </span>
        </div>

        {allEmpty && (
          <div style={{ textAlign: "center", padding: "40px 0", fontSize: fs(15), color: s.tS }}>
            Все чисты ✨
          </div>
        )}

        {!allEmpty && (
          <>
            <div style={{ fontFamily: H, fontSize: fs(15), marginTop: 12, marginBottom: 6 }}>💸 Деньги</div>
            {money.length === 0 && (
              <div style={{ fontSize: fs(12), color: s.tS, marginBottom: 10 }}>Никто не должен ✨</div>
            )}
            {money.map((b) => {
              const opened = !!openMoney[b.client_id];
              return (
                <Glass
                  key={b.client_id || "_orphan"}
                  s={s}
                  style={{ padding: "10px 12px", marginBottom: 6, cursor: "pointer" }}
                  onClick={() => setOpenMoney((o) => ({ ...o, [b.client_id]: !opened }))}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ fontSize: fs(13), fontWeight: 500 }}>
                      {b.client_type && <span>{b.client_type} </span>}{b.client_name}
                    </div>
                    <div style={{ fontSize: fs(14), fontFamily: H, color: s.amber, fontWeight: 500 }}>
                      {b.amount.toLocaleString()}₽
                    </div>
                  </div>
                  {opened && (
                    <div style={{ marginTop: 8, fontSize: fs(12), color: s.tS, lineHeight: 1.6 }}>
                      {b.items.map((it) => (
                        <div key={it.id}>
                          {it.kind === "ritual" ? "🕯" : "🃏"} {it.desc} ·{" "}
                          <span style={{ color: s.text }}>{it.paid.toLocaleString()}/{it.amount.toLocaleString()}₽</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Glass>
              );
            })}

            <div style={{ fontFamily: H, fontSize: fs(15), marginTop: 16, marginBottom: 6 }}>🔄 Бартер</div>
            {barter.length === 0 && (
              <div style={{ fontSize: fs(12), color: s.tS, marginBottom: 10 }}>Все бартеры закрыты ✨</div>
            )}
            {barter.map((b) => {
              const key = b.client_id || "_orphan";
              const opened = !!openBarter[key];
              const isOrphan = !b.client_id;
              return (
                <Glass
                  key={key}
                  s={s}
                  style={{ padding: "10px 12px", marginBottom: 6, cursor: "pointer" }}
                  onClick={() => setOpenBarter((o) => ({ ...o, [key]: !opened }))}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ fontSize: fs(13), fontWeight: 500 }}>
                      {b.client_type && <span>{b.client_type} </span>}{b.client_name}
                      {isOrphan && (
                        <span style={{ color: s.tS, fontWeight: 400, fontSize: fs(11) }}> · без привязки</span>
                      )}
                    </div>
                    <div style={{ fontSize: fs(13), color: s.acc, fontWeight: 500 }}>
                      {b.items.length}
                    </div>
                  </div>
                  {opened && (
                    <div style={{ marginTop: 8, fontSize: fs(12), color: s.tS, lineHeight: 1.6 }}>
                      {b.items.map((it) => (
                        <div key={it.id}>
                          • {it.name}{it.group && <span style={{ opacity: 0.7 }}> · {it.group}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </Glass>
              );
            })}
          </>
        )}
      </div>
    </>
  ), document.body);
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — RITUALS
// ═══════════════════════════════════════════════════════════════

function ArRituals({ s, openRitual }) {
  const [seg, setSeg] = useState("rituals"); // rituals | inv
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Ритуалы</div>
      <div style={{ display: "flex", gap: 6 }}>
        <Pill s={s} active={seg === "rituals"} onClick={() => setSeg("rituals")}>🕯️ Ритуалы</Pill>
        <Pill s={s} active={seg === "inv"} onClick={() => setSeg("inv")}>📦 Инвентарь</Pill>
      </div>
      {seg === "rituals"
        ? <ArRitualsList s={s} openRitual={openRitual} />
        : <ArInventory s={s} />}
    </div>
  );
}

function ArRitualsList({ s, openRitual }) {
  const [goal, setGoal] = useState("all");
  const path = goal === "all"
    ? "/api/arcana/rituals"
    : `/api/arcana/rituals?goal=${encodeURIComponent(goal)}`;
  const { data, loading, error, refetch } = useApi(path, [goal]);
  const list = loading || error ? [] : adaptRituals(data);
  const goals = ["all", ...new Set(list.map((r) => r.goal).filter(Boolean))];

  return (
    <>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {goals.map((g) => (
          <Pill key={g} s={s} active={goal === g} onClick={() => setGoal(g)}>
            {g === "all" ? "Все цели" : g}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && list.length === 0 && (
        <Empty s={s} emoji="🕯️" title="Ритуалов нет" text="Ничего не запланировано." />
      )}
      {!loading && !error && list.map((r) => (
        <Glass
          key={r.id}
          s={s}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openRitual({ id: r.id })}
        >
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <div>
              <div style={{ fontSize: fs(14), color: s.text, fontWeight: 500, fontFamily: H }}>
                {r.name}
              </div>
              <div style={{ fontSize: fs(10), color: s.tM, marginTop: 3 }}>
                {[r.goal, r.place, r.type, r.date].filter(Boolean).join(" · ")}
              </div>
            </div>
            <span style={{ fontSize: fs(16) }}>{r.result}</span>
          </div>
        </Glass>
      ))}
    </>
  );
}

const ARCANA_INV_CATS = ["🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды", "💳 Прочее"];

function ArInventory({ s }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const [openItem, setOpenItem] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const params = [];
  if (cat !== "all") params.push(`cat=${encodeURIComponent(cat)}`);
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const path = "/api/arcana/inventory" + (params.length ? "?" + params.join("&") : "");
  const { data, loading, error, refetch } = useApi(path, [cat, q]);
  const items = data?.items || [];
  const cats = data?.categories || [];
  const total = cats.reduce((a, c) => a + (c.count || 0), 0);
  const allCats = [{ name: "all", count: total }, ...cats];
  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <SearchInput s={s} value={q} onChange={setQ} placeholder="Поиск в инвентаре" />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {allCats.map((c) => (
          <Pill key={c.name} s={s} active={cat === c.name} onClick={() => setCat(c.name)}>
            {c.name === "all" ? "Все" : c.name} · {c.count}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && items.length === 0 && (
        <Empty
          s={s}
          emoji="📦"
          title="Инвентарь пуст"
          text='Добавляй через бота: «инвентарь: соль 200г»'
        />
      )}
      {items.map((it) => {
        const expSoon = it.expires && it.expires <= today;
        return (
          <Glass
            key={it.id}
            s={s}
            style={{ padding: "10px 14px", marginBottom: 4 }}
            onClick={() => setOpenItem(it)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: fs(15), flexShrink: 0 }}>
                {(it.cat || "📦").split(" ")[0]}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: fs(14), color: s.text, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {it.name}
                </div>
                <div style={{ fontSize: fs(10), color: s.tM, marginTop: 2 }}>
                  {it.qty != null && <span>{it.qty} </span>}
                  {it.expires && (
                    <span style={{ color: expSoon ? s.red : s.tM }}>
                      · до {it.expires}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </Glass>
        );
      })}
      {openItem && (
        <InventoryItemSheet
          s={s}
          item={openItem}
          onClose={() => setOpenItem(null)}
          onChanged={() => { setOpenItem(null); refetch(); }}
        />
      )}
      {addOpen && (
        <InventoryAddSheet
          s={s}
          onClose={() => setAddOpen(false)}
          onAdded={() => { setAddOpen(false); refetch(); }}
        />
      )}
      {/* Локальный FAB «+» внутри сегмента инвентаря */}
      <div
        onClick={() => setAddOpen(true)}
        style={{
          position: "fixed", right: 18, bottom: 86, zIndex: 9,
          width: 44, height: 44, borderRadius: 22,
          background: s.acc, color: "#fff",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 22, cursor: "pointer",
          boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
        }}
      >+</div>
    </>
  );
}

function InventoryAddSheet({ s, onClose, onAdded }) {
  const [name, setName] = useState("");
  const [cat, setCat] = useState(ARCANA_INV_CATS[0]);
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";

  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      // Используем общий /api/lists POST-эндпоинт (тип=inv, бот=arcana)
      const r = await fetch("/api/lists", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": initData,
        },
        body: JSON.stringify({
          type: "inv",
          name: name.trim(),
          cat,
          bot: "arcana",
          qty: qty ? parseFloat(qty) : null,
          note,
          expires: expires || null,
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert("Не получилось: " + (err.detail || r.status));
        return;
      }
      onAdded?.();
    } catch (e) { alert("Ошибка: " + e.message); }
    finally { setBusy(false); }
  };

  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <div className="card-h">
          <span className="card-title" style={{ fontSize: 20 }}>📦 Новый айтем</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <Input s={s} value={name} onChange={setName} placeholder="Название (например: соль)" />
          <div style={{ fontSize: fs(11), color: s.tS }}>Категория</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {ARCANA_INV_CATS.map((c) => (
              <Pill key={c} s={s} active={cat === c} onClick={() => setCat(c)}>{c}</Pill>
            ))}
          </div>
          <div style={{ fontSize: fs(11), color: s.tS }}>Количество</div>
          <Input s={s} value={qty} onChange={setQty} placeholder="200" type="number" />
          <div style={{ fontSize: fs(11), color: s.tS }}>Заметка</div>
          <Input s={s} value={note} onChange={setNote} placeholder="—" />
          <div style={{ fontSize: fs(11), color: s.tS }}>Срок годности</div>
          <input
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            style={{
              width: "100%", padding: "10px 12px", borderRadius: 10,
              border: `1px solid ${s.brd}`, background: s.card, color: s.text,
              fontSize: fs(13), outline: "none",
            }}
          />
          <SubmitBtn
            s={s}
            disabled={!name.trim() || busy}
            label={busy ? "Сохраняю..." : "Добавить в инвентарь"}
            onClick={submit}
          />
        </div>
      </div>
    </>
  ), document.body);
}

function InventoryItemSheet({ s, item, onClose, onChanged }) {
  const [mode, setMode] = useState("view"); // view | edit | purchase
  const [name] = useState(item.name);
  const [qty, setQty] = useState(item.qty ?? "");
  const [note, setNote] = useState(item.note || "");
  const [expires, setExpires] = useState(item.expires || "");
  const [price, setPrice] = useState("");
  const [qtyAdded, setQtyAdded] = useState("");
  const [busy, setBusy] = useState(null);
  const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";

  const call = async (path, method, body) => {
    const r = await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": initData,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    return r.json();
  };

  const save = async () => {
    setBusy("save");
    try {
      await call(`/api/arcana/inventory/${item.id}`, "PATCH", {
        qty: qty === "" ? null : parseFloat(qty),
        note,
        expires: expires || "",
      });
      onChanged?.();
    } catch (e) { alert("Не получилось: " + e.message); }
    finally { setBusy(null); }
  };

  const purchase = async () => {
    setBusy("purchase");
    try {
      await call(`/api/arcana/inventory/${item.id}/purchase`, "POST", {
        price: parseFloat(price),
        qty_added: qtyAdded ? parseFloat(qtyAdded) : null,
      });
      onChanged?.();
    } catch (e) { alert("Не получилось: " + e.message); }
    finally { setBusy(null); }
  };

  const depleted = async () => {
    if (!confirm("Закончился? Архивировать.")) return;
    setBusy("depleted");
    try {
      const add = confirm("Добавить в Покупки?");
      await call(`/api/arcana/inventory/${item.id}/depleted`, "POST", { add_to_buy: add });
      onChanged?.();
    } catch (e) { alert("Не получилось: " + e.message); }
    finally { setBusy(null); }
  };

  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <div style={{ fontFamily: H, fontSize: fs(20), color: s.text, marginBottom: 4 }}>
          {(item.cat || "📦").split(" ")[0]} {name}
        </div>
        {item.cat && (
          <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 12 }}>{item.cat}</div>
        )}

        {mode === "purchase" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: fs(11), color: s.tS }}>Цена ₽</div>
            <Input s={s} value={price} onChange={setPrice} placeholder="200" type="number" />
            <div style={{ fontSize: fs(11), color: s.tS }}>Количество (приплюсовать к {item.qty ?? 0})</div>
            <Input s={s} value={qtyAdded} onChange={setQtyAdded} placeholder="100" type="number" />
            <SubmitBtn
              s={s}
              disabled={!price || busy}
              label={busy === "purchase" ? "Сохраняю..." : "💰 Записать покупку"}
              onClick={purchase}
            />
            <div onClick={() => setMode("view")} style={{ textAlign: "center", color: s.tS, fontSize: fs(12), padding: "6px", cursor: "pointer" }}>
              ← назад
            </div>
          </div>
        ) : mode === "edit" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: fs(11), color: s.tS }}>Количество</div>
            <Input s={s} value={qty} onChange={setQty} placeholder="200" type="number" />
            <div style={{ fontSize: fs(11), color: s.tS }}>Заметка</div>
            <Input s={s} value={note} onChange={setNote} placeholder="—" />
            <div style={{ fontSize: fs(11), color: s.tS }}>Срок годности</div>
            <input
              type="date"
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
              style={{
                width: "100%", padding: "10px 12px", borderRadius: 10,
                border: `1px solid ${s.brd}`, background: s.card, color: s.text,
                fontSize: fs(13), outline: "none",
              }}
            />
            <SubmitBtn
              s={s}
              disabled={!!busy}
              label={busy === "save" ? "Сохраняю..." : "Сохранить"}
              onClick={save}
            />
            <div onClick={() => setMode("view")} style={{ textAlign: "center", color: s.tS, fontSize: fs(12), padding: "6px", cursor: "pointer" }}>
              ← назад
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>
              {item.qty != null && <div>Количество: <b>{item.qty}</b></div>}
              {item.note && <div>Заметка: {item.note}</div>}
              {item.expires && <div>Срок до: {item.expires}</div>}
            </div>
            <div style={{ height: 8 }} />
            <ActionRow
              s={s}
              icon={<span>💰</span>}
              label="Купила"
              onClick={() => setMode("purchase")}
            />
            <ActionRow
              s={s}
              icon={<span>🗑️</span>}
              label="Закончился"
              onClick={depleted}
              destructive
            />
            <ActionRow
              s={s}
              icon={<span>✏️</span>}
              label="Редактировать"
              onClick={() => setMode("edit")}
            />
          </div>
        )}
      </div>
    </>
  ), document.body);
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — GRIMOIRE
// ═══════════════════════════════════════════════════════════════

const GRIM_GOLD = "#d4a843";
const GRIM_GOLD_BG = "rgba(212,168,67,0.10)";
const GRIM_SAGE = "#5a9a8a";

// «2 окт · 25 лет» из ISO YYYY-MM-DD
const RU_MONTH_SHORT_LOCAL = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
function formatBirthday(iso) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const y = +m[1], mo = +m[2], d = +m[3];
  const today = new Date();
  let age = today.getFullYear() - y;
  const beforeBday = today.getMonth() + 1 < mo || (today.getMonth() + 1 === mo && today.getDate() < d);
  if (beforeBday) age -= 1;
  const ageWord = age === 1 ? "год" : (age >= 2 && age <= 4) ? "года" : "лет";
  return `${d} ${RU_MONTH_SHORT_LOCAL[mo - 1]}${age > 0 ? ` · ${age} ${ageWord}` : ""}`;
}

// Avatar клиента — фото из Cloudinary, fallback на инициал.
function ClientAvatar({ s, photoUrl, initial, size = 36, radius = "50%", textColor }) {
  const [broken, setBroken] = useState(false);
  if (photoUrl && !broken) {
    return (
      <img
        src={photoUrl}
        alt={initial || "?"}
        onError={() => setBroken(true)}
        style={{
          width: size, height: size, borderRadius: radius,
          objectFit: "cover", flexShrink: 0,
          background: `${s.acc}22`,
        }}
      />
    );
  }
  return (
    <div
      style={{
        width: size, height: size, borderRadius: radius,
        background: `${s.acc}22`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: fs(size >= 60 ? 28 : 14),
        color: textColor || s.acc, fontWeight: 500, fontFamily: H,
        flexShrink: 0,
      }}
    >
      {initial || "?"}
    </div>
  );
}

// Галерея фото-объектов с inline-просмотром, edit заметки и delete.
function ClientBarter({ s, clientName }) {
  const { data, refetch } = useApi('/api/arcana/barter?only_open=true');
  const groups = data?.by_group || [];
  // Эвристика: показываем группы, где имя клиента встречается в Группа (например
  // «Расклад на работу — Маша», «Ритуал очищения · Маша»).
  const matched = groups.filter((g) =>
    clientName && (g.group || "").toLowerCase().includes((clientName || "").toLowerCase())
  );
  if (matched.length === 0) return null;

  const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";
  const toggle = async (id) => {
    try {
      await fetch(`/api/lists/${id}/done`, {
        method: "POST",
        headers: { "X-Telegram-Init-Data": initData },
      });
      refetch?.();
    } catch (_) {}
  };

  return (
    <>
      <SectionLabel s={s}>🔄 Бартер</SectionLabel>
      {matched.map((g, i) => (
        <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 6 }}>
          <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 4 }}>{g.group}</div>
          {g.items.map((it) => (
            <div
              key={it.id}
              onClick={() => !it.done && toggle(it.id)}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "4px 0",
                opacity: it.done ? 0.5 : 1, cursor: it.done ? "default" : "pointer",
                fontSize: fs(13),
              }}
            >
              <span>{it.done ? "☑️" : "◻️"}</span>
              <span style={{ textDecoration: it.done ? "line-through" : "none", color: s.text }}>
                {it.name}
              </span>
            </div>
          ))}
        </Glass>
      ))}
    </>
  );
}

function ObjectPhotosGallery({ s, clientId, photos, onChanged }) {
  const [openIdx, setOpenIdx] = useState(null);
  return (
    <>
      <SectionLabel s={s}>📷 Фото для гадания</SectionLabel>
      <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 6, marginBottom: 12 }}>
        {photos.map((p, i) => (
          <div
            key={i}
            onClick={() => setOpenIdx(i)}
            style={{
              flexShrink: 0, position: "relative",
              width: 60, height: 60, cursor: "pointer",
            }}
          >
            <img
              src={p.url}
              alt={`object-${i}`}
              style={{
                width: 60, height: 60, borderRadius: 8,
                objectFit: "cover",
                border: `1px solid ${s.brd}`,
                display: "block",
              }}
            />
            {p.note && (
              <span style={{
                position: "absolute", bottom: 2, right: 2,
                fontSize: 10, lineHeight: 1, padding: "2px 3px",
                background: "rgba(0,0,0,0.55)", borderRadius: 6,
                color: "#fff",
              }}>📝</span>
            )}
          </div>
        ))}
      </div>
      {openIdx !== null && photos[openIdx] && (
        <ObjectPhotoSheet
          s={s}
          clientId={clientId}
          index={openIdx}
          photo={photos[openIdx]}
          onClose={() => setOpenIdx(null)}
          onChanged={() => { setOpenIdx(null); onChanged?.(); }}
        />
      )}
    </>
  );
}

function ObjectPhotoSheet({ s, clientId, index, photo, onClose, onChanged }) {
  const [note, setNote] = useState(photo.note || "");
  const [busy, setBusy] = useState(null);
  const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";

  const save = async () => {
    setBusy("save");
    try {
      const r = await fetch(`/api/arcana/clients/${clientId}/object_photo/${index}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": initData,
        },
        body: JSON.stringify({ note }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
      onChanged?.();
    } catch (e) {
      alert("Не получилось: " + e.message);
    } finally { setBusy(null); }
  };

  const remove = async () => {
    if (!confirm("Удалить фото?")) return;
    setBusy("delete");
    try {
      const r = await fetch(`/api/arcana/clients/${clientId}/object_photo/${index}`, {
        method: "DELETE",
        headers: { "X-Telegram-Init-Data": initData },
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
      onChanged?.();
    } catch (e) {
      alert("Не получилось: " + e.message);
    } finally { setBusy(null); }
  };

  return createPortal((
    <>
      <div className="acc-sheet-overlay" onClick={onClose} />
      <div className="acc-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="acc-grip" />
        <Glass s={s} style={{ padding: 4, marginBottom: 12 }}>
          <img
            src={photo.url}
            alt="object"
            style={{ width: "100%", borderRadius: 8, display: "block" }}
          />
        </Glass>
        <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 6 }}>Заметка</div>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Добавить заметку…"
          rows={3}
          style={{
            width: "100%", padding: "10px 12px", borderRadius: 10,
            border: `1px solid ${s.brd}`, background: s.card, color: s.text,
            fontSize: fs(13), fontFamily: "inherit", resize: "vertical",
            marginBottom: 10,
          }}
        />
        <div style={{ display: "flex", gap: 8 }}>
          <div style={{ flex: 1 }}>
            <SubmitBtn
              s={s}
              disabled={!!busy}
              label={busy === "save" ? "Сохраняю..." : "Сохранить"}
              onClick={save}
            />
          </div>
          <div
            onClick={busy ? undefined : remove}
            style={{
              padding: "12px 14px", borderRadius: 12,
              background: `${s.red}1f`, border: `1px solid ${s.red}55`,
              color: s.red, cursor: busy ? "not-allowed" : "pointer",
              fontSize: fs(14), fontWeight: 500, display: "flex", alignItems: "center",
              marginTop: 12, opacity: busy ? 0.6 : 1,
            }}
          >
            <Trash2 size={fs(15)} />
          </div>
        </div>
      </div>
    </>
  ), document.body);
}

function GrimoireThemeChip({ theme }) {
  return (
    <span style={{
      fontSize: 10, padding: "2px 7px", borderRadius: 8,
      color: GRIM_GOLD, background: GRIM_GOLD_BG,
      whiteSpace: "nowrap",
    }}>{theme}</span>
  );
}

function ArGrimoire({ s, openGrimoire }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const params = [];
  if (cat !== "all") params.push(`cat=${encodeURIComponent(cat)}`);
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const path = "/api/arcana/grimoire" + (params.length ? "?" + params.join("&") : "");
  const { data, loading, error, refetch } = useApi(path, [cat, q]);
  const view = loading || error ? { items: [], categories: [] } : adaptGrimoire(data);
  const totalAll = view.categories.reduce((a, c) => a + (c.count || 0), 0);
  const cats = [{ name: "all", count: totalAll }, ...view.categories];

  // Префикс: иконка категории — первое emoji-слово (📿 / 🧴 / ✨ / 📝)
  const catIcon = (name) => {
    if (!name || name === "all") return null;
    const first = name.split(" ")[0];
    return first;
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Гримуар</div>
      <SearchInput s={s} value={q} onChange={setQ} placeholder="Поиск в гримуаре" />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {cats.map((c) => (
          <Pill key={c.name} s={s} active={cat === c.name} onClick={() => setCat(c.name)}>
            {c.name === "all" ? "Все" : c.name} · {c.count}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && view.items.length === 0 && (
        <Empty
          s={s}
          emoji="📖"
          title={cat === "all" ? "Гримуар пуст" : "В этой категории пусто"}
          text={
            cat === "all"
              ? 'Добавляй через бота: «запиши в гримуар: …»'
              : 'В этой категории пока ничего. Добавь через бота: «запиши в гримуар: …»'
          }
        />
      )}
      {!loading && !error && view.items.map((g) => (
        <Glass
          key={g.id}
          s={s}
          style={{ padding: "12px 14px", marginBottom: 6 }}
          onClick={openGrimoire ? () => openGrimoire({ id: g.id }) : undefined}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {catIcon(g.cat) && (
              <span style={{ fontSize: fs(15), flexShrink: 0 }}>{catIcon(g.cat)}</span>
            )}
            <span style={{
              fontFamily: H, fontSize: fs(16), fontWeight: 500,
              color: s.text, flex: 1, minWidth: 0,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {g.name || "—"}
            </span>
            {g.verified && (
              <span style={{ color: GRIM_SAGE, fontSize: fs(13), flexShrink: 0 }}>✓</span>
            )}
          </div>
          {g.themes.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
              {g.themes.map((t, i) => <GrimoireThemeChip key={i} theme={t} />)}
            </div>
          )}
          {g.preview && (
            <div style={{ fontSize: fs(12), color: s.tS, marginTop: 6, lineHeight: 1.5 }}>
              {g.preview}
            </div>
          )}
          {g.source && (
            <div style={{ fontSize: fs(10), color: s.tM, marginTop: 6 }}>
              📚 {g.source}
            </div>
          )}
        </Glass>
      ))}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
// ARCANA — WORK (работы)
// ═══════════════════════════════════════════════════════════════

function ArWork({ s, openWork }) {
  const { data, loading, error, refetch } = useApi('/api/arcana/works');
  const [expanded, setExpanded] = useState({});
  const [subOverrides, setSubOverrides] = useState({});

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const works = data?.works || [];

  const toggleExpand = (id) => setExpanded((e) => ({ ...e, [id]: !e[id] }));
  const toggleSub = async (subId) => {
    setSubOverrides((o) => ({ ...o, [subId]: !o[subId] }));
    try {
      const initData =
        window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";
      await fetch(`/api/lists/${subId}/done`, {
        method: "POST",
        headers: { "X-Telegram-Init-Data": initData },
      });
    } catch (_) { /* оптимистично */ }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      <div className="page-title" style={{ marginBottom: 10 }}>Работы</div>
      {works.length === 0 && (
        <Empty s={s} emoji="🌙" title="Работ нет" text="Передохни." />
      )}
      {works.map((w) => {
        const subs = w.subtasks || [];
        const open = expanded[w.id];
        const total = subs.length;
        const done = subs.filter((x) => (subOverrides[x.id] ?? x.done)).length;
        return (
          <div
            key={w.id}
            className="task glass"
            style={{ flexDirection: "column", alignItems: "stretch", cursor: openWork ? "pointer" : "default" }}
            onClick={openWork ? () => openWork({ id: w.id, payload: w }) : undefined}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="body" style={{ flex: 1 }}>
                <div className="title">{w.title}</div>
                <div className="meta">
                  {w.is_overdue && (
                    <span style={{ color: s.red, fontWeight: 600 }}>просрочена</span>
                  )}
                  {w.deadline_label && !w.is_overdue && (
                    <span>{w.deadline_label}</span>
                  )}
                  {w.client?.name && <span> · 👤 {w.client.name}</span>}
                  {total > 0 && (
                    <span
                      onClick={(e) => { e.stopPropagation(); toggleExpand(w.id); }}
                      style={{ cursor: "pointer", marginLeft: 8 }}
                    >
                      📋 {done}/{total} {open ? "▾" : "▸"}
                    </span>
                  )}
                </div>
              </div>
              {w.category && <div className="cat-badge">{String(w.category).split(" ")[0]}</div>}
              <PrioDot s={s} prio={w.priority} />
            </div>
            {open && total > 0 && (
              <div style={{ marginTop: 8, paddingLeft: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                {subs.map((sub) => {
                  const isDone = subOverrides[sub.id] ?? sub.done;
                  return (
                    <div
                      key={sub.id}
                      onClick={(e) => { e.stopPropagation(); if (!isDone) toggleSub(sub.id); }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        cursor: isDone ? "default" : "pointer",
                        opacity: isDone ? 0.55 : 1,
                        fontSize: fs(12),
                      }}
                    >
                      <span>{isDone ? "✅" : "◻️"}</span>
                      <span style={{ textDecoration: isDone ? "line-through" : "none" }}>
                        {sub.name}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// SESSION DETAIL SHEET
// ═══════════════════════════════════════════════════════════════

function SessionPhoto({ s, id, url, onUploaded, uploadPath }) {
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [localUrl, setLocalUrl] = useState(url || null);
  const path = uploadPath || `/api/arcana/sessions/${id}/photo`;

  const onPick = () => {
    if (fileRef.current && !busy) fileRef.current.click();
  };
  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) {
      alert("Файл > 5 МБ");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(path, {
        method: "POST",
        headers: { "X-Telegram-Init-Data": (window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "") },
        body: fd,
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "error" }));
        alert("Не получилось: " + (err.detail || r.status));
        return;
      }
      const d = await r.json();
      setLocalUrl(d.url);
      onUploaded && onUploaded();
    } catch (err) {
      alert("Ошибка: " + err.message);
    } finally {
      setBusy(false);
    }
  };

  if (localUrl) {
    return (
      <Glass s={s} style={{ padding: 4, marginBottom: 12 }}>
        <img src={localUrl} alt="Фото расклада" style={{
          width: "100%", borderRadius: 8, display: "block",
        }} />
      </Glass>
    );
  }

  return (
    <>
      <Glass
        s={s}
        onClick={onPick}
        style={{
          padding: "22px 14px", marginBottom: 12, textAlign: "center",
          border: `1.5px dashed ${s.brd}`, cursor: busy ? "wait" : "pointer",
        }}
      >
        <Camera size={fs(26)} color={s.tM} style={{ margin: "0 auto 6px", display: "block" }} />
        <div style={{ fontSize: fs(11), color: s.tS }}>
          {busy ? "Загружаю..." : "Нажми чтобы загрузить фото расклада"}
        </div>
      </Glass>
      <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={onFile} />
    </>
  );
}

function SessionSummary({ s, id, interp }) {
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(true);

  const load = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await apiPost(`/api/arcana/sessions/${id}/summarize`);
      setSummary(r.summary || "");
    } catch (e) {
      alert("Не получилось: " + e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!interp) return null;

  return (
    <Glass s={s} style={{ padding: "10px 14px", marginBottom: 8 }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ display: "flex", justifyContent: "space-between", cursor: "pointer", fontSize: fs(12), color: s.acc, fontWeight: 500 }}
      >
        <span>⚡ Короткая суть</span>
        <span>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div style={{ marginTop: 6, fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>
          {summary ? (
            summary
          ) : (
            <div
              onClick={load}
              style={{
                display: "inline-block", padding: "4px 10px", borderRadius: 6,
                background: `${s.acc}22`, color: s.acc, cursor: busy ? "wait" : "pointer",
                fontSize: fs(12),
              }}
            >
              {busy ? "Генерирую..." : "Сгенерировать саммари"}
            </div>
          )}
        </div>
      )}
    </Glass>
  );
}

function TarotCardTile({ s, card, deckId }) {
  const [imgOk, setImgOk] = useState(true);
  const hasFile = card.file && imgOk;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
      {hasFile ? (
        <img
          src={`/decks/${deckId}/${card.file}`}
          alt={card.en}
          onError={() => setImgOk(false)}
          style={{
            width: "100%", height: "auto", display: "block",
            borderRadius: 6, boxShadow: `0 2px 8px ${s.brd}`,
          }}
        />
      ) : (
        <div style={{
          width: "100%", aspectRatio: "2/3", borderRadius: 6,
          background: s.card, border: `1px solid ${s.brd}`,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: fs(32),
        }}>🃏</div>
      )}
      <div style={{ textAlign: "center", width: "100%" }}>
        <div style={{ fontSize: fs(11), color: s.text, fontWeight: 500, lineHeight: 1.2 }}>
          {card.en || card.raw || "—"}
        </div>
      </div>
    </div>
  );
}

// wave8: вердикт триплета → стиль кнопок
const VERDICT_BTN = [
  { v: "yes",  ic: "✅", lbl: "Сбылось",   c: "#22c55e", status: "✅ Да" },
  { v: "half", ic: "〰️", lbl: "Частично", c: "#f59e0b", status: "〰️ Частично" },
  { v: "no",   ic: "❌", lbl: "Нет",       c: "#ef4444", status: "❌ Нет" },
];

function VerdictRow({ s, current, busy, onPick }) {
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {VERDICT_BTN.map((b) => {
        const active = current === b.v;
        return (
          <div key={b.v} onClick={() => !busy && onPick(b)} style={{
            flex: 1, textAlign: "center", padding: "10px 4px", borderRadius: 10,
            background: active ? `${b.c}28` : s.card,
            border: `1px solid ${active ? b.c : b.c + "44"}`,
            color: b.c, fontSize: fs(12), fontWeight: 500,
            cursor: busy ? "progress" : "pointer", opacity: busy ? 0.6 : 1,
            backdropFilter: "blur(10px)",
          }}>{b.ic} {b.lbl}</div>
        );
      })}
    </div>
  );
}

function TripletSlide({ s, t, deckId, onVerdict }) {
  const [accordion, setAccordion] = useState(false);
  const [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState(t.verdict || "wait");
  const cards = (t.cards || []).slice(0, 3);
  const hasBottom = !!t.bottomCard;

  const submit = async (b) => {
    setBusy(true);
    try {
      await apiPost(`/api/arcana/sessions/${t.id}/verify`, { status: b.status });
      setCurrent(b.v);
      onVerdict && onVerdict(t.id, b.v);
    } catch (e) {
      alert("Не получилось: " + e.message);
    } finally { setBusy(false); }
  };

  return (
    <div>
      <Glass s={s} style={{ padding: "12px 14px", marginBottom: 12 }}>
        <div style={{ fontFamily: H, fontSize: fs(17), fontWeight: 500, lineHeight: 1.25 }}>
          {t.q || "—"}
        </div>
        <div style={{ fontSize: fs(11), opacity: 0.65, marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[t.client, t.deck, t.date].filter(Boolean).map((it, i) => <span key={i}>{it}</span>)}
        </div>
      </Glass>

      <SectionLabel s={s}>Карты</SectionLabel>
      <div className={hasBottom ? "cards-grid-4" : "cards-grid-4 no-bottom"}>
        {cards.map((c, i) => (
          <div key={i} className="card-wrap">
            <TarotCardTile s={s} card={c} deckId={deckId} />
          </div>
        ))}
        {hasBottom && (
          <div className="card-wrap card-bottom-wrap">
            <TarotCardTile s={s} card={t.bottomCard} deckId={deckId} />
          </div>
        )}
      </div>

      {t.summary && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
          <div style={{ fontSize: fs(10), color: s.acc, marginBottom: 4 }}>⚡ Саммари</div>
          <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>{t.summary}</div>
        </Glass>
      )}

      {t.interp && (
        <>
          <div onClick={() => setAccordion(!accordion)} style={{
            cursor: "pointer", padding: "10px 14px", borderRadius: 10,
            background: s.card, border: `1px solid ${s.brd}`, marginBottom: 10,
            display: "flex", justifyContent: "space-between", alignItems: "center",
            fontSize: fs(12), color: s.acc, fontWeight: 500,
          }}>
            <span>📝 Полная трактовка</span>
            <span>{accordion ? "▾" : "▸"}</span>
          </div>
          {accordion && (
            <Glass s={s} accent={s.acc} style={{ padding: "12px 14px", marginBottom: 10 }}>
              <div className="trip-interp"
                   style={{ fontSize: fs(13), color: s.text }}
                   dangerouslySetInnerHTML={{ __html: sanitizeHtml(t.interp) }} />
            </Glass>
          )}
        </>
      )}

      <SectionLabel s={s}>Сбылось?</SectionLabel>
      <VerdictRow s={s} current={current} busy={busy} onPick={submit} />
    </div>
  );
}

function SessionPagerOverview({ s, group, onJump, onSummarize, summarizing }) {
  return (
    <div>
      <Glass s={s} style={{ padding: "14px 16px", marginBottom: 12 }}>
        <div style={{ fontSize: fs(11), color: s.acc, marginBottom: 4 }}>
          🃏 {group.category || "Сессия"}
        </div>
        <div style={{ fontFamily: H, fontSize: fs(20), fontWeight: 500, lineHeight: 1.2 }}>
          {group.title}
        </div>
        <div style={{ fontSize: fs(11), opacity: 0.65, marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[group.client, group.firstDate, `${group.triplets.length} триплетов`]
            .filter(Boolean).map((it, i) => <span key={i}>{it}</span>)}
        </div>
      </Glass>

      <SessionPhoto
        s={s}
        url={group.photoUrl}
        uploadPath={`/api/arcana/sessions/by-slug/${group.slug}/photo`}
      />

      <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
        <div style={{ fontSize: fs(10), color: s.acc, marginBottom: 6 }}>⚡ Общее саммари</div>
        {group.summary ? (
          <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.5 }}>{group.summary}</div>
        ) : (
          <div onClick={onSummarize} style={{
            display: "inline-block", padding: "4px 10px", borderRadius: 6,
            background: `${s.acc}22`, color: s.acc,
            cursor: summarizing ? "wait" : "pointer", fontSize: fs(12),
          }}>{summarizing ? "Генерирую..." : "Сгенерировать саммари"}</div>
        )}
      </Glass>

      <SectionLabel s={s}>Вопросы сессии</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {group.triplets.map((t, idx) => {
          const v = t.verdict || "wait";
          const ic = SESS_STATUS_GLYPH[v] || (v === "yes" ? "✅" : v === "half" ? "〰️" : v === "no" ? "❌" : "⏳");
          return (
            <div key={t.id || idx} className="glass tap"
                 style={{ padding: "10px 14px" }}
                 onClick={() => onJump(idx + 1)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>
                    {idx + 1}) {t.q || "—"}
                  </div>
                  {t.summary && (
                    <div style={{ fontSize: fs(11), color: s.tM, marginTop: 2, lineHeight: 1.4 }}>
                      {t.summary.length > 90 ? t.summary.slice(0, 90) + "…" : t.summary}
                    </div>
                  )}
                </div>
                <span style={{ fontSize: fs(16) }}>{ic}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{
        display: "flex", gap: 12, marginTop: 14, padding: "10px 14px",
        borderRadius: 10, background: s.card, border: `1px solid ${s.brd}`,
        fontSize: fs(11), color: s.tM, justifyContent: "space-between",
      }}>
        <span>Всего: <b style={{ color: s.text }}>{group.triplets.length}</b></span>
        {Object.entries({ yes: "✅", half: "〰️", no: "❌", wait: "⏳" }).map(([k, ic]) => {
          const n = group.triplets.filter((t) => (t.verdict || "wait") === k).length;
          return n > 0 ? <span key={k}>{ic} {n}</span> : null;
        })}
      </div>
    </div>
  );
}

function SessionDetail({ s, id, slug }) {
  const useSlug = slug || id;
  const { data, loading, error, refetch } = useApi(
    useSlug ? `/api/arcana/sessions/by-slug/${useSlug}` : null, [useSlug]
  );
  const [page, setPage] = useState(0);
  const [summarizing, setSummarizing] = useState(false);
  const [localSummary, setLocalSummary] = useState(null);
  const touchRef = useRef({ x: 0 });

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const group = adaptSessionGroup(data);
  if (!group) return null;

  const triplets = group.triplets || [];
  const isSolo = group.isSolo || triplets.length <= 1;
  const totalSlides = isSolo ? triplets.length : triplets.length + 1;
  const showOverview = !isSolo && page === 0;
  const tripletIdx = isSolo ? page : page - 1;
  const t = triplets[tripletIdx];
  const deckId = t?.deckId || "rider-waite";

  const summarize = async () => {
    if (summarizing || isSolo) return;
    setSummarizing(true);
    try {
      const r = await apiPost(`/api/arcana/sessions/by-slug/${group.slug}/summarize`);
      setLocalSummary(r.summary || "");
    } catch (e) {
      alert("Не получилось: " + e.message);
    } finally { setSummarizing(false); }
  };

  const groupForRender = { ...group, summary: localSummary || group.summary };

  const onTouchStart = (e) => { touchRef.current.x = e.touches[0].clientX; };
  const onTouchEnd = (e) => {
    const dx = e.changedTouches[0].clientX - touchRef.current.x;
    if (Math.abs(dx) < 40) return;
    if (dx < 0 && page < totalSlides - 1) setPage(page + 1);
    else if (dx > 0 && page > 0) setPage(page - 1);
  };

  const handleVerdict = (tid, newV) => {
    triplets.forEach((tt) => { if (tt.id === tid) tt.verdict = newV; });
  };

  return (
    <div onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
      {!isSolo && (
        <div style={{
          display: "flex", justifyContent: "center", gap: 6, marginBottom: 10,
        }}>
          {Array.from({ length: totalSlides }).map((_, i) => (
            <div key={i} onClick={() => setPage(i)} style={{
              width: i === page ? 22 : 6, height: 6, borderRadius: 3,
              background: i === page ? s.acc : s.brd, cursor: "pointer",
              transition: "width 0.2s",
            }} />
          ))}
        </div>
      )}

      {showOverview ? (
        <SessionPagerOverview
          s={s} group={groupForRender}
          onJump={(slideIdx) => setPage(slideIdx)}
          onSummarize={summarize} summarizing={summarizing}
        />
      ) : t ? (
        <TripletSlide s={s} t={t} deckId={deckId} onVerdict={handleVerdict} />
      ) : null}

      {!isSolo && (
        <div style={{
          display: "flex", justifyContent: "space-between",
          marginTop: 14, gap: 8,
        }}>
          <div onClick={() => page > 0 && setPage(page - 1)} style={{
            flex: 1, textAlign: "center", padding: "10px",
            borderRadius: 10, background: s.card,
            border: `1px solid ${s.brd}`, opacity: page > 0 ? 1 : 0.3,
            cursor: page > 0 ? "pointer" : "default", fontSize: fs(12),
          }}>‹ {page === 1 ? "Все вопросы" : "Назад"}</div>
          <div onClick={() => page < totalSlides - 1 && setPage(page + 1)} style={{
            flex: 1, textAlign: "center", padding: "10px",
            borderRadius: 10, background: s.card,
            border: `1px solid ${s.brd}`, opacity: page < totalSlides - 1 ? 1 : 0.3,
            cursor: page < totalSlides - 1 ? "pointer" : "default", fontSize: fs(12),
          }}>{page < totalSlides - 1 ? "Дальше ›" : "—"}</div>
        </div>
      )}
    </div>
  );
}

function _SessionDetailLegacy({ s, id }) {
  const { data, loading, error, refetch } = useApi(id ? `/api/arcana/sessions/${id}` : null, [id]);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const x = adaptSessionDetail(data);
  if (!x) return null;
  const doneGlyph = (x.done || "⏳").split(" ")[0];
  return (
    <div>
      {/* Шапка */}
      <Glass s={s} style={{ padding: "12px 14px", marginBottom: 12 }}>
        <div
          style={{
            fontFamily: H,
            fontSize: fs(20),
            color: s.text,
            fontWeight: 500,
            marginBottom: 8,
          }}
        >
          {x.q}
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr auto 1fr",
            gap: "4px 10px",
            fontSize: fs(12),
          }}
        >
          <span style={{ color: s.tS }}>👤 Клиент</span>
          <span style={{ color: s.text, fontWeight: 500 }}>{x.client}</span>
          <span style={{ color: s.tS }}>📅 Дата</span>
          <span style={{ color: s.text }}>{x.date}</span>
          <span style={{ color: s.tS }}>🂠 Тип</span>
          <span style={{ color: s.text }}>{x.type}</span>
          {x.deck && (
            <>
              <span style={{ color: s.tS }}>🎴 Колода</span>
              <span style={{ color: s.text }}>{x.deck}</span>
            </>
          )}
          <span style={{ color: s.tS }}>❓ Вопрос</span>
          <span style={{ color: s.text, fontSize: fs(11) }}>
            {x.q.length > 18 ? x.q.slice(0, 18) + "…" : x.q}
          </span>
          <span style={{ color: s.tS }}>💳 Оплата</span>
          <span
            style={{
              color: x.price > 0 ? (x.paid >= x.price ? s.acc : s.red) : s.tM,
              fontWeight: 500,
            }}
          >
            {x.price > 0
              ? `${x.price.toLocaleString()} ₽ · ${x.paid >= x.price ? "оплачено" : "долг"}`
              : "—"}
          </span>
          <span style={{ color: s.tS }}>⏳ Проверка</span>
          <span style={{ color: s.text }}>{doneGlyph} {x.done.split(" ").slice(1).join(" ") || "Не проверено"}</span>
        </div>
      </Glass>

      {/* wave6.7: Фото расклада — кликабельный загрузчик */}
      <SessionPhoto s={s} id={x.id} url={x.photo_url} onUploaded={() => { /* refetch */ }} />

      {/* wave6.7: AI-саммари */}
      <SessionSummary s={s} id={x.id} interp={x.interp} />

      {/* wave6.4: Карты в раскладе — grid с картинками */}
      <SectionLabel s={s}>Карты в раскладе</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
        {x.cards.map((c, i) => (
          <TarotCardTile key={i} s={s} card={c} deckId={x.deckId} />
        ))}
      </div>

      {/* Дно колоды */}
      {x.bottomCard && (
        <Glass s={s} accent={s.acc} style={{ padding: "10px 12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: fs(24), flexShrink: 0 }}>🂠</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: fs(10), color: s.tS }}>Дно</div>
              <div style={{ fontSize: fs(14), color: s.text, fontWeight: 500 }}>
                {x.bottomCard.en || x.bottomCard.raw || "—"}
              </div>
              {x.bottomCard.ru && (
                <div style={{ fontSize: fs(11), color: s.tM }}>{x.bottomCard.ru}</div>
              )}
            </div>
            {x.bottomCard.file && (
              <img
                src={`/decks/${x.deckId}/${x.bottomCard.file}`}
                alt={x.bottomCard.en}
                onError={(e) => { e.target.style.display = "none"; }}
                style={{ width: 40, borderRadius: 4 }}
              />
            )}
          </div>
        </Glass>
      )}

      {/* Трактовка */}
      <SectionLabel s={s}>Трактовка</SectionLabel>
      <Glass s={s} accent={s.acc} style={{ padding: "12px 14px" }}>
        <div
          style={{ fontSize: fs(13), color: s.text, lineHeight: 1.6 }}
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(x.interp) }}
        />
      </Glass>

      <VerifyButtons
        s={s}
        id={x.id}
        path="/api/arcana/sessions"
        action="verify"
        onDone={refetch}
        options={[
          { label: "✓ Сбылось", status: "✅ Да", c: "#22c55e" },
          { label: "~ Частично", status: "〰️ Частично", c: "#f59e0b" },
          { label: "✗ Нет", status: "❌ Нет", c: "#ef4444" },
        ]}
      />
    </div>
  );
}

function VerifyButtons({ s, id, path, action, options, onDone }) {
  const [busy, setBusy] = useState(false);
  const click = async (status) => {
    if (busy) return;
    setBusy(true);
    try {
      await apiPost(`${path}/${id}/${action}`, { status });
      if (onDone) onDone();
    } catch (e) {
      alert(`Не получилось: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <SectionLabel s={s}>{action === "verify" ? "Статус сбылось" : "Результат"}</SectionLabel>
      <div style={{ display: "flex", gap: 6 }}>
        {options.map((b, i) => (
          <div
            key={i}
            onClick={() => click(b.status)}
            style={{
              flex: 1,
              textAlign: "center",
              padding: "10px 4px",
              borderRadius: 10,
              background: s.card,
              border: `1px solid ${b.c}44`,
              color: b.c,
              fontSize: fs(12),
              fontWeight: 500,
              cursor: busy ? "progress" : "pointer",
              opacity: busy ? 0.6 : 1,
              backdropFilter: "blur(10px)",
            }}
          >
            {b.label}
          </div>
        ))}
      </div>
    </>
  );
}

// ═══════════════════════════════════════════════════════════════
// RITUAL DETAIL SHEET
// ═══════════════════════════════════════════════════════════════

function RitualDetail({ s, id }) {
  const { data, loading, error, refetch } = useApi(id ? `/api/arcana/rituals/${id}` : null, [id]);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const r = adaptRitualDetail(data);
  if (!r) return null;
  const suppliesTotal = (r.supplies || []).reduce((a, x) => a + (x.price || 0), 0);
  return (
    <div>
      <Glass s={s} style={{ padding: "12px 14px", marginBottom: 12 }}>
        <div style={{ fontFamily: H, fontSize: fs(20), color: s.text, fontWeight: 500, lineHeight: 1.25 }}>
          {r.name}
        </div>
        <div style={{ fontSize: fs(11), opacity: 0.7, marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[
            r.client ? `👤 ${r.client}` : "👤 личный",
            r.date && `📅 ${r.date}`,
            r.goal && `🎯 ${r.goal}`,
            r.place && `📍 ${r.place}`,
          ].filter(Boolean).map((it, i) => (
            <span key={i}>{it}</span>
          ))}
        </div>
        {r.question && (
          <div style={{ fontSize: fs(12), color: s.tS, marginTop: 6 }}>
            <span style={{ opacity: 0.7 }}>❓ </span>{r.question}
          </div>
        )}
        {r.price > 0 && (
          <div style={{
            fontSize: fs(12), marginTop: 6, fontWeight: 500,
            color: r.paid >= r.price ? s.acc : s.red,
          }}>
            💳 {r.price.toLocaleString()} ₽ · {r.paid >= r.price ? "оплачено" : `долг ${(r.price - r.paid).toLocaleString()} ₽`}
          </div>
        )}
      </Glass>

      {r.photo_url && (
        <Glass s={s} style={{ padding: 4, marginBottom: 10 }}>
          <img
            src={r.photo_url}
            alt="Фото ритуала"
            style={{ width: "100%", borderRadius: 8, display: "block" }}
          />
        </Glass>
      )}

      {/* Расходники */}
      <Glass s={s} style={{ padding: "12px 14px", marginBottom: 10 }}>
        <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500, marginBottom: 8 }}>
          🕯️ Расходники
        </div>
        {r.supplies.map((x, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "4px 0",
              fontSize: fs(12),
              color: s.text,
            }}
          >
            <span>{x.name} · {x.qty}</span>
            <span style={{ color: s.tS }}>{x.price} ₽</span>
          </div>
        ))}
        <div
          style={{
            borderTop: `1px solid ${s.brd}`,
            marginTop: 6,
            paddingTop: 6,
            display: "flex",
            justifyContent: "space-between",
            fontSize: fs(13),
            fontWeight: 500,
            color: s.acc,
          }}
        >
          <span>Итого</span>
          <span>{suppliesTotal} ₽</span>
        </div>
      </Glass>

      {/* Время */}
      <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Clock size={fs(14)} color={s.acc} />
          <span style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>Время</span>
          <span style={{ fontSize: fs(13), color: s.tS, marginLeft: "auto" }}>{r.time} мин</span>
        </div>
      </Glass>

      {/* Подношения */}
      {r.offerings && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
          <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500, marginBottom: 4 }}>
            🙏 Подношения / откуп
          </div>
          <div style={{ fontSize: fs(12), color: s.tS }}>{r.offerings}</div>
        </Glass>
      )}

      {/* Силы */}
      {r.powers && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
          <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500, marginBottom: 4 }}>
            ⚡ Силы
          </div>
          <div style={{ fontSize: fs(12), color: s.tS }}>{r.powers}</div>
        </Glass>
      )}

      {/* Структура */}
      <Glass s={s} accent={s.acc} style={{ padding: "10px 14px" }}>
        <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500, marginBottom: 8 }}>
          📕 Структура ритуала
        </div>
        {r.structure.map((step, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              gap: 8,
              padding: "4px 0",
              fontSize: fs(12),
              color: s.text,
            }}
          >
            <span style={{ color: s.tS, minWidth: 16 }}>{i + 1}.</span>
            <span style={{ flex: 1 }}>{step}</span>
          </div>
        ))}
      </Glass>

      <VerifyButtons
        s={s}
        id={r.id}
        path="/api/arcana/rituals"
        action="result"
        onDone={refetch}
        options={[
          { label: "✓ Сработал", status: "✅ Сработало", c: "#22c55e" },
          { label: "~ Частично", status: "〰️ Частично", c: "#f59e0b" },
          { label: "✗ Нет", status: "❌ Не сработало", c: "#ef4444" },
        ]}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// CLIENT DETAIL SHEET (из v2)
// ═══════════════════════════════════════════════════════════════

function ClientDetail({ s, id }) {
  const { data, loading, error, refetch } = useApi(id ? `/api/arcana/clients/${id}` : null, [id]);
  const [editOpen, setEditOpen] = useState(false);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const c = adaptClientDossier(data);
  if (!c) return null;
  const isSelf = c.self;
  const contactItems = (c.contact || "")
    .split(/[,;\n]+/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((raw) => {
      const isHandle = raw.startsWith("@");
      return { raw, icon: isHandle ? "✈️" : "📱" };
    });
  return (
    <div>
      {isSelf ? (
        <div style={{ position: "relative" }}>
          <SelfDetailHeader client={c} />
          <span
            onClick={() => setEditOpen((v) => !v)}
            style={{
              position: "absolute", top: 14, right: 14,
              cursor: "pointer", color: "rgba(220,230,220,0.55)",
              display: "flex", zIndex: 5,
            }}
            title="Редактировать"
          >
            <StickyNote size={fs(15)} />
          </span>
        </div>
      ) : (
      <div
        style={{
          display: "flex", gap: 14, marginBottom: 16,
        }}
      >
        {c.photo_url ? (
          <ClientAvatar s={s} photoUrl={c.photo_url} initial={c.initial} size={64} radius={16} />
        ) : (
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 16,
              background: `linear-gradient(135deg, ${s.acc}, ${s.acc}aa)`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: fs(28),
              color: "#fff",
              fontFamily: H,
              fontWeight: 500,
              flexShrink: 0,
            }}
          >
            {c.initial}
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: H, fontSize: fs(22), fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
            <span>{(c.status || "").split(" ")[0]} {c.name}</span>
            <span
              onClick={() => setEditOpen((v) => !v)}
              style={{ marginLeft: "auto", cursor: "pointer", color: s.tS, display: "flex" }}
              title="Редактировать"
            >
              <StickyNote size={fs(15)} />
            </span>
          </div>
          {contactItems.length > 0 ? (
            <div style={{ fontSize: fs(12), color: s.tS, marginTop: 3, display: "flex", flexDirection: "column", gap: 2 }}>
              {contactItems.map((it, i) => (
                <span key={i}>{it.icon} {it.raw}</span>
              ))}
              <span style={{ opacity: 0.7 }}>с {c.since}</span>
            </div>
          ) : (
            <div style={{ fontSize: fs(12), color: s.tS, marginTop: 3 }}>с {c.since}</div>
          )}
          <div style={{ fontSize: fs(12), color: s.text, marginTop: 5 }}>
            <span style={{ color: s.tS }}>Запрос:</span> {c.request || "—"}
          </div>
          {c.birthday && (
            <div style={{ fontSize: fs(12), color: s.text, marginTop: 5 }}>
              🎂 {formatBirthday(c.birthday)}
            </div>
          )}
        </div>
      </div>
      )}

      {editOpen && (
        <ClientEditForm
          s={s}
          client={c}
          isSelf={isSelf}
          onSaved={() => { setEditOpen(false); refetch(); }}
        />
      )}

      {isSelf ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6, marginBottom: 12 }}>
          <Metric s={s} v={c.sessions} sub="сеансов" />
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 12 }}>
          <Metric s={s} v={`${(c.total / 1000).toFixed(1)}к`} unit="₽" sub="всего" accent={s.acc} />
          <Metric
            s={s}
            v={`${(c.debt / 1000).toFixed(1)}к`}
            unit="₽"
            sub="долг"
            accent={c.debt > 0 ? s.red : s.acc}
          />
          <Metric s={s} v={c.sessions} sub="сеансов" />
        </div>
      )}

      <Glass s={s} accent={s.acc} style={{ marginBottom: 14 }}>
        <div style={{ fontSize: fs(10), color: s.tS, marginBottom: 4, display: "inline-flex", alignItems: "center", gap: 4 }}>
          <StickyNote size={fs(11)} /> Заметки
        </div>
        <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.55 }}>{c.notes || "—"}</div>
      </Glass>

      {c.photos.length > 0 && (
        <ObjectPhotosGallery
          s={s}
          clientId={c.id}
          photos={c.photos}
          onChanged={refetch}
        />
      )}

      <ClientBarter s={s} clientName={c.name} />

      <SectionLabel s={s}>История</SectionLabel>
      {c.history.map((h, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: 10,
            padding: "8px 2px",
            alignItems: "center",
            borderBottom: `1px solid ${s.brd}`,
            fontSize: fs(12),
          }}
        >
          <span style={{ color: s.tM, minWidth: 50 }}>{h.date}</span>
          <span style={{ fontSize: fs(14) }}>{h.type}</span>
          <span style={{ color: s.text, flex: 1 }}>{h.desc}</span>
          {h.amount > 0 && (
            <span style={{ color: s.acc, fontWeight: 500 }}>
              {h.amount.toLocaleString()} ₽
            </span>
          )}
          <span>{h.paid ? "✅" : "⏳"}</span>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// QUICK ADD — grid 2-col с иконками
// Nexus и Arcana — разные наборы
// ═══════════════════════════════════════════════════════════════

const NEXUS_ADD = [
  { key: "expense", icon: Wallet, label: "Финансы" },
  { key: "task", icon: Check, label: "Задача" },
  { key: "note", icon: StickyNote, label: "Заметка" },
  { key: "list", icon: ListIcon, label: "В список" },
  { key: "memory", icon: Brain, label: "В память" },
];

const ARCANA_ADD = [
  { key: "client", icon: Users, label: "Клиент" },
  { key: "expense", icon: Wallet, label: "Финансы" },
  { key: "photo", icon: Camera, label: "Фото расклада" },
  { key: "ritual_photo", icon: Camera, label: "Фото ритуала" },
];

function QuickAdd({ s, actions, onPick }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 8,
      }}
    >
      {actions.map((a) => {
        const Ic = a.icon;
        return (
          <div
            key={a.key}
            onClick={() => onPick(a.key)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              padding: "18px 10px",
              background: s.card,
              border: `1px solid ${s.brd}`,
              borderRadius: 14,
              backdropFilter: "blur(10px)",
              cursor: "pointer",
              minHeight: 80,
              transition: "all 0.15s",
            }}
          >
            <div
              style={{
                width: 38,
                height: 38,
                borderRadius: 19,
                background: `${s.acc}18`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Ic size={fs(20)} color={s.acc} />
            </div>
            <span style={{ fontSize: fs(12), color: s.text, fontFamily: B }}>{a.label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TASK SHEET (с working write actions)
// ═══════════════════════════════════════════════════════════════

function TaskSheet({ s, task, onClose }) {
  const [busy, setBusy] = useState(null);
  const [editOpen, setEditOpen] = useState(false);
  const run = async (label, fn) => {
    setBusy(label);
    try {
      await fn();
      onClose();
    } catch (e) {
      alert(`Не получилось: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };
  // wave8.46: чеклист задачи — items из /api/lists?type=check&group=<title>
  const checklistPath = task.title
    ? `/api/lists?type=check&group=${encodeURIComponent(task.title)}`
    : null;
  const { data: clData, refetch: clRefetch } = useApi(checklistPath, [task.title]);
  const checklist = clData ? adaptLists(clData) : [];
  const [clOverrides, setClOverrides] = useState({});
  const clItems = checklist.map((x) =>
    x.id in clOverrides ? { ...x, done: clOverrides[x.id] } : x,
  );
  const toggleCl = async (item) => {
    if (item.done) return;
    setClOverrides((p) => ({ ...p, [item.id]: true }));
    try {
      await apiPost(`/api/lists/${item.id}/done`);
      setTimeout(clRefetch, 500);
    } catch (e) {
      setClOverrides((p) => { const n = { ...p }; delete n[item.id]; return n; });
      alert("Не удалось отметить");
    }
  };
  const metaCard = (label, value) => (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        padding: "8px 10px",
        background: s.card,
        border: `1px solid ${s.brd}`,
        borderRadius: 10,
        backdropFilter: "blur(10px)",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: fs(10), color: s.tM, marginBottom: 2, textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontSize: fs(13), color: s.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {value}
      </div>
    </div>
  );
  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        {metaCard("Категория", task.cat || "—")}
        {metaCard("Дедлайн", task.date || task.time || task.rpt || "—")}
        {metaCard("Приоритет", task.prio || "—")}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <ActionRow
          s={s}
          icon={<Check size={fs(16)} />}
          label={busy === "done" ? "Сохраняю..." : "Сделано"}
          onClick={() => !busy && task.id && run("done", () => apiPost(`/api/tasks/${task.id}/done`))}
        />
        <ActionRow
          s={s}
          icon={<Trash2 size={fs(16)} />}
          label={busy === "cancel" ? "Сохраняю..." : "Отменить"}
          onClick={() => !busy && task.id && run("cancel", () => apiPost(`/api/tasks/${task.id}/cancel`))}
          destructive
        />
      </div>
      {/* wave8.61: форма «Перенести» скрыта под дизклоужером в стиле
          ActionRow (как «Сделано»/«Отменить»), чтобы не было дубля
          категории/приоритета под мета-шапкой. */}
      <div style={{ marginTop: 6 }}>
        <div
          onClick={() => setEditOpen((v) => !v)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 14px",
            background: s.card,
            border: `1px solid ${s.brd}`,
            borderRadius: 12,
            backdropFilter: "blur(10px)",
            cursor: "pointer",
            color: s.text,
          }}
        >
          <span style={{ display: "flex" }}>
            {editOpen
              ? <ChevronDown size={fs(16)} />
              : <ChevronRight size={fs(16)} />}
          </span>
          <span style={{ fontSize: fs(14) }}>Перенести</span>
        </div>
        {editOpen && (
          <div style={{ marginTop: 8 }}>
            <TaskEditForm
              s={s}
              task={task}
              busy={busy === "edit"}
              onSave={(payload) => run("edit", () => apiPost(`/api/tasks/${task.id}/edit`, payload))}
            />
          </div>
        )}
      </div>
      {clItems.length > 0 && (
        <>
          <div style={{ fontFamily: H, fontSize: fs(15), color: s.text, margin: "16px 0 8px" }}>
            Чеклист
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {clItems.map((it) => (
              <Glass
                key={it.id}
                s={s}
                style={{
                  padding: "10px 14px",
                  display: "flex", alignItems: "center", gap: 10,
                  opacity: it.done ? 0.5 : 1, cursor: "pointer",
                }}
                onClick={() => toggleCl(it)}
              >
                <Chk s={s} done={it.done} />
                <span style={{
                  flex: 1, fontSize: fs(15), color: s.text,
                  textDecoration: it.done ? "line-through" : "none",
                  wordBreak: "break-word",
                }}>
                  {it.name}
                </span>
              </Glass>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// WORK SHEET — детали + действия (Done / Postpone / Cancel)
// ═══════════════════════════════════════════════════════════════

function WorkSheet({ s, work, onClose }) {
  const [busy, setBusy] = useState(null);
  const [editOpen, setEditOpen] = useState(false);
  const [newDate, setNewDate] = useState("");
  const [subOverrides, setSubOverrides] = useState({});

  const run = async (label, fn) => {
    setBusy(label);
    try { await fn(); onClose(); }
    catch (e) { alert(`Не получилось: ${e.message}`); }
    finally { setBusy(null); }
  };

  const toggleSub = async (subId, isDone) => {
    if (isDone) return;
    setSubOverrides((o) => ({ ...o, [subId]: true }));
    try {
      const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";
      await fetch(`/api/lists/${subId}/done`, {
        method: "POST",
        headers: { "X-Telegram-Init-Data": initData },
      });
    } catch (_) { /* оптимистично */ }
  };

  const subs = work.subtasks || [];
  const metaCard = (label, value) => (
    <div style={{
      flex: 1, minWidth: 0, padding: "8px 10px",
      background: s.card, border: `1px solid ${s.brd}`,
      borderRadius: 10, backdropFilter: "blur(10px)", textAlign: "center",
    }}>
      <div style={{ fontSize: fs(10), color: s.tM, marginBottom: 2, textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: fs(13), color: s.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{value}</div>
    </div>
  );

  const fmtDeadline = (iso) => {
    if (!iso) return "";
    const datePart = formatShortDate(iso);
    const m = String(iso).match(/T(\d{2}):(\d{2})/);
    if (!m) return datePart;
    const hhmm = `${m[1]}:${m[2]}`;
    if (hhmm === "23:59" || hhmm === "00:00") return datePart;
    return `${datePart} ${hhmm}`;
  };
  const deadlineFmt = fmtDeadline(work.deadline);

  return (
    <div>
      <div style={{ fontFamily: H, fontSize: fs(18), fontWeight: 500, marginBottom: 4 }}>
        {work.title}
      </div>
      {deadlineFmt && (
        <div style={{
          fontSize: fs(12), color: work.is_overdue ? s.red : s.tS,
          fontWeight: work.is_overdue ? 600 : 400, marginBottom: 10,
        }}>
          📅 {deadlineFmt}{work.is_overdue ? " · просрочена" : ""}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        {metaCard("Категория", work.category || "—")}
        {metaCard(
          "Дедлайн",
          deadlineFmt
            ? <span style={{ color: work.is_overdue ? s.red : s.text }}>{deadlineFmt}</span>
            : "—"
        )}
        {metaCard("Приоритет", normPrio(work.priority) || "—")}
      </div>
      {work.client?.name && (
        <div style={{ fontSize: fs(12), color: s.tS, marginBottom: 10 }}>👤 {work.client.name}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <ActionRow
          s={s}
          icon={<Check size={fs(16)} />}
          label={busy === "done" ? "Сохраняю..." : "Сделано"}
          onClick={() => !busy && run("done", () => apiPost(`/api/arcana/works/${work.id}/done`))}
        />
        <ActionRow
          s={s}
          icon={<Trash2 size={fs(16)} />}
          label={busy === "cancel" ? "Сохраняю..." : "Отменить"}
          onClick={() => !busy && run("cancel", () => apiPost(`/api/arcana/works/${work.id}/cancel`))}
          destructive
        />
      </div>
      <div style={{ marginTop: 6 }}>
        <div
          onClick={() => setEditOpen((v) => !v)}
          style={{
            display: "flex", alignItems: "center", gap: 12, padding: "12px 14px",
            background: s.card, border: `1px solid ${s.brd}`, borderRadius: 12,
            backdropFilter: "blur(10px)", cursor: "pointer", color: s.text,
          }}
        >
          <span style={{ display: "flex" }}>
            {editOpen ? <ChevronDown size={fs(16)} /> : <ChevronRight size={fs(16)} />}
          </span>
          <span style={{ fontSize: fs(14) }}>Перенести</span>
        </div>
        {editOpen && (
          <div style={{ marginTop: 8, padding: "10px 12px", background: s.card, border: `1px solid ${s.brd}`, borderRadius: 12 }}>
            <input
              type="date"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              style={{
                width: "100%", padding: "10px 12px", borderRadius: 8,
                border: `1px solid ${s.brd}`, background: s.card, color: s.text,
                fontSize: fs(14), marginBottom: 8,
              }}
            />
            <div
              onClick={() => newDate && !busy && run("postpone", () => apiPost(`/api/arcana/works/${work.id}/postpone`, { date: newDate }))}
              style={{
                padding: "10px", textAlign: "center", borderRadius: 10,
                background: newDate ? `${s.acc}28` : s.card,
                border: `1px solid ${newDate ? s.acc : s.brd}`,
                color: newDate ? s.acc : s.tM, fontSize: fs(13), fontWeight: 500,
                cursor: newDate && !busy ? "pointer" : "default",
              }}
            >
              {busy === "postpone" ? "Сохраняю..." : "Сохранить дату"}
            </div>
          </div>
        )}
      </div>
      {subs.length > 0 && (
        <>
          <div style={{ fontFamily: H, fontSize: fs(15), color: s.text, margin: "16px 0 8px" }}>
            Подзадачи
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {subs.map((sub) => {
              const isDone = subOverrides[sub.id] ?? sub.done;
              return (
                <Glass
                  key={sub.id}
                  s={s}
                  style={{
                    padding: "10px 14px", display: "flex", alignItems: "center", gap: 10,
                    opacity: isDone ? 0.5 : 1, cursor: isDone ? "default" : "pointer",
                  }}
                  onClick={() => toggleSub(sub.id, isDone)}
                >
                  <Chk s={s} done={isDone} />
                  <span style={{
                    flex: 1, fontSize: fs(14), color: s.text,
                    textDecoration: isDone ? "line-through" : "none",
                  }}>{sub.name}</span>
                </Glass>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// QUICK FORMS (FAB → выбранная категория → форма)
// ═══════════════════════════════════════════════════════════════

const FAB_TITLE = {
  expense: "Финансы",
  task: "Новая задача",
  note: "Новая заметка",
  list: "В список",
  memory: "В память",
  photo: "Фото расклада",
  ritual_photo: "Фото ритуала",
  voice: "Голосом",
  client: "Новый клиент",
  session: "Новый расклад",
  ritual: "Новый ритуал",
  work: "Работа",
  grimoire: "В гримуар",
};

const EXPENSE_CATS = [
  "🍜 Продукты", "🍱 Кафе", "🚕 Транспорт", "🚬 Привычки",
  "💅 Бьюти", "🏠 Жилье", "💻 Подписки", "🐾 Коты",
  "🎲 Импульсивные", "💳 Прочее",
];

const PRIOS = ["🔴", "🟡", "⚪"];

function Input({ s, value, onChange, placeholder, type = "text", step }) {
  return (
    <input
      type={type}
      step={step}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        background: s.card,
        border: `1px solid ${s.brd}`,
        borderRadius: 10,
        padding: "10px 12px",
        color: s.text,
        fontFamily: B,
        fontSize: fs(13),
        outline: "none",
        width: "100%",
      }}
    />
  );
}

function Select({ s, value, onChange, options }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {options.map((o) => (
        <Pill key={o} s={s} active={value === o} onClick={() => onChange(o)}>
          {o}
        </Pill>
      ))}
    </div>
  );
}

function SubmitBtn({ s, onClick, disabled, label = "Сохранить" }) {
  return (
    <div
      onClick={disabled ? undefined : onClick}
      style={{
        textAlign: "center",
        padding: "12px",
        borderRadius: 12,
        background: s.acc,
        color: "#fff",
        fontSize: fs(14),
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
        marginTop: 12,
      }}
    >
      {label}
    </div>
  );
}

function QuickForm({ s, kind, onDone, botType = "nexus" }) {
  const [busy, setBusy] = useState(false);
  const wrap = (fn) => async () => {
    setBusy(true);
    try {
      await fn();
      onDone();
    } catch (e) {
      alert(`Не получилось: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  if (kind === "expense") return <ExpenseForm s={s} onSubmit={wrap} busy={busy} botType={botType} />;
  if (kind === "task") return <TaskForm s={s} onSubmit={wrap} busy={busy} />;
  if (kind === "note" || kind === "memory") return <NoteForm s={s} onSubmit={wrap} busy={busy} />;
  if (kind === "list") return <ListAddForm s={s} onSubmit={wrap} busy={busy} />;
  if (kind === "client") return <ClientForm s={s} onSubmit={wrap} busy={busy} />;
  if (kind === "photo") return <SessionPhotoUpload s={s} onDone={onDone} />;
  if (kind === "ritual_photo") return <SessionPhotoUpload s={s} onDone={onDone} mode="ritual" />;

  return (
    <div style={{ padding: "12px 4px" }}>
      <div style={{ fontSize: fs(13), color: s.text, marginBottom: 8 }}>
        Coming soon 🌱 — добавление через бота.
      </div>
      <SubmitBtn s={s} onClick={onDone} label="Закрыть" />
    </div>
  );
}

const INCOME_CATS = [
  "💼 Зарплата", "💰 Фриланс", "🎁 Подарок", "🏦 Прочее",
];

const NEXUS_FINANCE_TYPES = [
  { k: "expense", label: "💸 Расход" },
  { k: "income", label: "💰 Доход" },
  { k: "debt", label: "📋 Долг" },
];

const ARCANA_FINANCE_TYPES = [
  { k: "expense", label: "💸 Расход" },
  { k: "practice_income", label: "🔮 Доход от практики" },
];

function ExpenseForm({ s, onSubmit, busy, botType = "nexus" }) {
  const financeTypes = botType === "arcana" ? ARCANA_FINANCE_TYPES : NEXUS_FINANCE_TYPES;
  const [type, setType] = useState("expense");
  const [amount, setAmount] = useState("");
  const [cat, setCat] = useState(EXPENSE_CATS[0]);
  const [desc, setDesc] = useState("");
  const [splits, setSplits] = useState([]); // [{cat, amount, desc}]
  // debt-only
  const [debtName, setDebtName] = useState("");
  const [debtDeadline, setDebtDeadline] = useState("");

  const catsForType = type === "income" ? INCOME_CATS : EXPENSE_CATS;

  const changeType = (t) => {
    setType(t);
    setSplits([]);
    if (t === "practice_income") {
      setCat("");
    } else if (t === "income") {
      setCat(INCOME_CATS[0]);
    } else if (t === "debt") {
      setCat("");
    } else {
      setCat(EXPENSE_CATS[0]);
    }
  };

  const addSplit = () => setSplits((x) => [...x, { cat: EXPENSE_CATS[0], amount: "", desc: "" }]);
  const updSplit = (i, patch) => setSplits((x) => x.map((s0, idx) => idx === i ? { ...s0, ...patch } : s0));
  const rmSplit = (i) => setSplits((x) => x.filter((_, idx) => idx !== i));

  const total = parseFloat(amount) || 0;
  const splitSum = splits.reduce((a, r) => a + (parseFloat(r.amount) || 0), 0);
  const remainder = total - splitSum;

  const needsCat = type === "expense";
  let valid = false;
  if (type === "debt") {
    valid = debtName.trim().length > 0 && total > 0;
  } else {
    valid = total > 0 && (!needsCat || !!cat);
    if (type === "expense" && splits.length > 0) {
      const allRowsOk = splits.every((r) => parseFloat(r.amount) > 0 && !!r.cat);
      valid = valid && allRowsOk && remainder > 0;
    }
  }

  const submitLabel = busy
    ? "Сохраняю..."
    : type === "expense"
      ? "Сохранить расход"
      : type === "debt"
        ? "Записать долг"
        : "Сохранить доход";

  const comingSoon = () => alert("Coming soon 🌱");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: fs(11), color: s.tS }}>Тип</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {financeTypes.map((t) => (
          <Pill key={t.k} s={s} active={type === t.k} onClick={() => changeType(t.k)}>
            {t.label}
          </Pill>
        ))}
      </div>

      {type === "debt" ? (
        <>
          <Input s={s} value={debtName} onChange={setDebtName} placeholder="Кому должна (имя)" />
          <Input s={s} value={amount} onChange={setAmount} placeholder="Сумма долга, ₽" type="number" step="1" />
          <Input s={s} value={debtDeadline} onChange={setDebtDeadline} placeholder="Дедлайн (например «до июня»)" />
          <SubmitBtn
            s={s}
            disabled={!valid || busy}
            label={submitLabel}
            onClick={onSubmit(async () => {
              await apiPost("/api/finance/debt", {
                name: debtName.trim(),
                amount: total,
                deadline: debtDeadline.trim(),
              });
            })}
          />
        </>
      ) : (
        <>
          <div style={{ position: "relative" }}>
            <Input s={s} value={amount} onChange={setAmount} placeholder={
              type === "expense" && splits.length > 0 ? "Общая сумма, ₽" : "Сумма, ₽"
            } type="number" step="1" />
            <div onClick={comingSoon} style={{
              position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
              cursor: "pointer", opacity: 0.5, fontSize: fs(16),
            }}>🎤</div>
          </div>
          {type !== "practice_income" && (
            <>
              <div style={{ fontSize: fs(11), color: s.tS }}>
                {type === "expense" && splits.length > 0 ? "Остаток → категория" : "Категория"}
              </div>
              <PillSelect s={s} value={cat} onChange={setCat} options={catsForType} />
            </>
          )}
          <div style={{ position: "relative" }}>
            <Input s={s} value={desc} onChange={setDesc} placeholder={
              type === "practice_income" ? "Клиент / расклад" : "Описание"
            } />
            <div style={{
              position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
              display: "flex", gap: 6, alignItems: "center",
            }}>
              {type === "expense" && (
                <div onClick={addSplit} title="Добавить подкатегорию" style={{
                  cursor: "pointer", color: s.acc, fontSize: fs(20), fontWeight: 600, lineHeight: 1,
                  padding: "0 4px",
                }}>+</div>
              )}
              <div onClick={comingSoon} style={{ cursor: "pointer", opacity: 0.5, fontSize: fs(16) }}>📸</div>
            </div>
          </div>

          {type === "expense" && splits.map((row, i) => (
            <div key={i} style={{
              display: "flex", flexDirection: "column", gap: 6,
              padding: "8px 10px", background: s.card, border: `1px solid ${s.brd}`,
              borderRadius: 10,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: fs(10), color: s.tS }}>Подкатегория #{i + 1}</div>
                <div onClick={() => rmSplit(i)} style={{
                  cursor: "pointer", color: s.tS, fontSize: fs(18), lineHeight: 1,
                }}>×</div>
              </div>
              <Input s={s} value={row.amount} onChange={(v) => updSplit(i, { amount: v })}
                     placeholder="Сумма, ₽" type="number" step="1" />
              <PillSelect s={s} value={row.cat} onChange={(v) => updSplit(i, { cat: v })}
                          options={EXPENSE_CATS} />
              <Input s={s} value={row.desc} onChange={(v) => updSplit(i, { desc: v })}
                     placeholder="Описание" />
            </div>
          ))}

          {type === "expense" && splits.length > 0 && (
            <div style={{ fontSize: fs(11), color: remainder > 0 ? s.tS : s.red }}>
              Остаток «{cat || "—"}»: {remainder.toLocaleString("ru-RU")} ₽
              {remainder <= 0 && " (сумма подкатегорий больше общей)"}
            </div>
          )}

          <SubmitBtn
            s={s}
            disabled={!valid || busy}
            label={submitLabel}
            onClick={onSubmit(async () => {
              const bot = (botType === "arcana" || type === "practice_income") ? "arcana" : "nexus";
              if (type === "expense" && splits.length > 0) {
                for (const row of splits) {
                  await apiPost("/api/finance", {
                    type: "expense",
                    amount: parseFloat(row.amount),
                    cat: row.cat,
                    desc: row.desc || "",
                    bot,
                  });
                }
                await apiPost("/api/finance", {
                  type: "expense",
                  amount: remainder,
                  cat,
                  desc,
                  bot,
                });
              } else {
                await apiPost("/api/finance", {
                  type,
                  amount: total,
                  cat: needsCat ? cat : (cat || null),
                  desc,
                  bot,
                });
              }
            })}
          />
        </>
      )}
    </div>
  );
}

const REMINDER_OPTIONS = [
  { k: "60", label: "За 1 час" },
  { k: "30", label: "За 30 мин" },
  { k: "0", label: "В момент" },
  { k: "custom", label: "Кастом" },
];

function TaskForm({ s, onSubmit, busy }) {
  const [title, setTitle] = useState("");
  const [cat, setCat] = useState("");
  const [prio, setPrio] = useState("⚪");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [wantsReminder, setWantsReminder] = useState(false);
  const [reminderKey, setReminderKey] = useState("30");
  const [reminderCustom, setReminderCustom] = useState("");
  const [cats, setCats] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiGet("/api/categories?type=task");
        if (!cancelled && r?.categories) setCats(r.categories);
      } catch (_) { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const valid = title.trim().length > 0;
  const comingSoon = () => alert("Coming soon 🌱");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ position: "relative" }}>
        <Input s={s} value={title} onChange={setTitle} placeholder="Название задачи" />
        <div onClick={comingSoon} style={{
          position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
          cursor: "pointer", opacity: 0.5, fontSize: fs(16),
        }}>🎤</div>
      </div>
      <div style={{ fontSize: fs(11), color: s.tS }}>Категория</div>
      {cats.length > 0 ? (
        <PillSelect s={s} value={cat} onChange={setCat} options={cats} />
      ) : (
        <Input s={s} value={cat} onChange={setCat} placeholder="🏠 Дом" />
      )}
      <div style={{ fontSize: fs(11), color: s.tS }}>Приоритет</div>
      <PillSelect s={s} value={prio} onChange={setPrio} options={PRIOS} />
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <Input s={s} value={date} onChange={setDate} placeholder="Дата" type="date" />
        </div>
        <div style={{ flex: 1 }}>
          <Input s={s} value={time} onChange={setTime} placeholder="чч:мм" type="time" />
        </div>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: fs(12), color: s.text, cursor: "pointer" }}>
        <input type="checkbox" checked={wantsReminder} onChange={(e) => setWantsReminder(e.target.checked)} />
        🔔 Напоминание
      </label>
      {wantsReminder && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {REMINDER_OPTIONS.map((r) => (
              <Pill key={r.k} s={s} active={reminderKey === r.k} onClick={() => setReminderKey(r.k)}>
                {r.label}
              </Pill>
            ))}
          </div>
          {reminderKey === "custom" && (
            <Input s={s} value={reminderCustom} onChange={setReminderCustom} placeholder="Минут до задачи, например 15" type="number" />
          )}
        </>
      )}
      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={busy ? "Сохраняю..." : "Добавить задачу"}
        onClick={onSubmit(async () => {
          // Собираем дату+время в ISO, если есть
          let dateValue = date || null;
          if (date && time) {
            dateValue = `${date}T${time}:00`;
          }
          await apiPost("/api/tasks", {
            title: title.trim(),
            cat: cat || null,
            prio,
            date: dateValue,
          });
        })}
      />
    </div>
  );
}

function TaskEditForm({ s, task, busy, onSave }) {
  // Разобрать исходный дедлайн в YYYY-MM-DD + HH:MM
  const parseInitial = () => {
    const raw = task?.date || task?.time || "";
    if (!raw) return { d: "", t: "" };
    const m = String(raw).match(/^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (m) return { d: m[1], t: m[2] && m[3] ? `${m[2]}:${m[3]}` : "" };
    return { d: "", t: "" };
  };
  const initial = parseInitial();

  const [title, setTitle] = useState(task?.title || "");
  const [cat, setCat] = useState(task?.cat || "");
  const [prio, setPrio] = useState(task?.prio || "⚪");
  const [date, setDate] = useState(initial.d);
  const [time, setTime] = useState(initial.t);
  const [cats, setCats] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiGet("/api/categories?type=task");
        if (!cancelled && r?.categories) {
          const list = r.categories.slice();
          if (task?.cat && !list.includes(task.cat)) list.unshift(task.cat);
          setCats(list);
        }
      } catch (_) { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [task?.cat]);

  const valid = !!date && title.trim().length > 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Input s={s} value={title} onChange={setTitle} placeholder="Название задачи" />
      <div style={{ fontSize: fs(11), color: s.tS }}>Категория</div>
      {cats.length > 0 ? (
        <PillSelect s={s} value={cat} onChange={setCat} options={cats} />
      ) : (
        <Input s={s} value={cat} onChange={setCat} placeholder="🏠 Дом" />
      )}
      <div style={{ fontSize: fs(11), color: s.tS }}>Приоритет</div>
      <PillSelect s={s} value={prio} onChange={setPrio} options={PRIOS} />
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <Input s={s} value={date} onChange={setDate} placeholder="Дата" type="date" />
        </div>
        <div style={{ flex: 1 }}>
          <Input s={s} value={time} onChange={setTime} placeholder="чч:мм" type="time" />
        </div>
      </div>
      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={busy ? "Сохраняю..." : "Перенести"}
        onClick={() => onSave({
          title: title.trim(),
          cat: cat || null,
          prio,
          date,
          time: time || null,
        })}
      />
    </div>
  );
}

function NoteForm({ s, onSubmit, busy }) {
  const [text, setText] = useState("");
  const [cat, setCat] = useState("");
  const [cats, setCats] = useState([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiGet("/api/categories?type=memory");
        if (!cancelled && r?.categories) setCats(r.categories);
      } catch (_) { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, []);
  const valid = text.trim().length > 0;
  const comingSoon = () => alert("Coming soon 🌱");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ position: "relative" }}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Текст заметки / записи в память"
          rows={4}
          style={{
            background: s.card,
            border: `1px solid ${s.brd}`,
            borderRadius: 10,
            padding: "10px 12px",
            color: s.text,
            fontFamily: B,
            fontSize: fs(13),
            outline: "none",
            width: "100%",
            resize: "vertical",
          }}
        />
        <div onClick={comingSoon} style={{
          position: "absolute", right: 10, top: 10,
          cursor: "pointer", opacity: 0.5, fontSize: fs(16),
        }}>🎤</div>
      </div>
      <div style={{ fontSize: fs(11), color: s.tS }}>Категория</div>
      <PillSelect s={s} value={cat} onChange={setCat} options={cats} />
      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={busy ? "Сохраняю..." : "Сохранить"}
        onClick={onSubmit(async () => {
          await apiPost("/api/memory", {
            text: text.trim(),
            cat: cat || null,
          });
        })}
      />
    </div>
  );
}

function ListAddForm({ s, onSubmit, busy }) {
  const [type, setType] = useState("buy");
  const [names, setNames] = useState([""]);
  const [cat, setCat] = useState("");
  const [qty, setQty] = useState("");
  const [expires, setExpires] = useState("");
  const [location, setLocation] = useState("");
  const [note, setNote] = useState("");
  const [cats, setCats] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiGet("/api/categories?type=list");
        if (!cancelled && r?.categories) setCats(r.categories);
      } catch (_) { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const validNames = names.map((n) => n.trim()).filter((n) => n.length > 0);
  const valid = validNames.length > 0;

  const setNameAt = (i, v) => {
    const next = [...names];
    next[i] = v;
    setNames(next);
  };
  const addRow = () => { if (names.length < 20) setNames([...names, ""]); };
  const removeRow = (i) => { if (names.length > 1) setNames(names.filter((_, idx) => idx !== i)); };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: fs(11), color: s.tS }}>Тип</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Pill s={s} active={type === "buy"} onClick={() => setType("buy")}>🛒 Покупки</Pill>
        <Pill s={s} active={type === "inv"} onClick={() => setType("inv")}>📦 Инвентарь</Pill>
      </div>

      {names.map((nm, i) => (
        <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <Input s={s} value={nm} onChange={(v) => setNameAt(i, v)}
                   placeholder={names.length > 1 ? `Название #${i + 1}` : "Название"} />
          </div>
          {names.length > 1 && (
            <div onClick={() => removeRow(i)} style={{
              cursor: "pointer", color: s.tS, fontSize: fs(18), padding: "0 6px",
            }}>×</div>
          )}
        </div>
      ))}
      {names.length < 20 && (
        <div onClick={addRow} style={{
          fontSize: fs(12), color: s.acc, cursor: "pointer", textAlign: "center", padding: "4px 0",
        }}>+ добавить ещё</div>
      )}

      <div style={{ fontSize: fs(11), color: s.tS }}>Категория</div>
      <PillSelect s={s} value={cat} onChange={setCat} options={cats} />
      {type === "inv" && (
        <>
          <div style={{ display: "flex", gap: 6 }}>
            <div style={{ flex: 1 }}>
              <Input s={s} value={qty} onChange={setQty} placeholder="Количество" type="number" />
            </div>
            <div style={{ flex: 1 }}>
              <Input s={s} value={expires} onChange={setExpires} placeholder="Срок годности" type="date" />
            </div>
          </div>
          <Input s={s} value={location} onChange={setLocation} placeholder="Где хранится" />
        </>
      )}
      <Input s={s} value={note} onChange={setNote} placeholder="Примечание (опционально)" />

      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={busy ? "Сохраняю..." : (validNames.length > 1 ? `Добавить ${validNames.length} шт.` : "Добавить")}
        onClick={onSubmit(async () => {
          const noteCombined = [location && `место: ${location}`, note].filter(Boolean).join(" · ") || null;
          for (const nm of validNames) {
            await apiPost("/api/lists", {
              type,
              name: nm,
              cat: cat || null,
              qty: qty ? parseFloat(qty) : null,
              expires: expires || null,
              note: noteCombined,
            });
          }
        })}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// SessionPhotoUpload — FAB → выбор сеанса → загрузка фото в Cloudinary
// ═══════════════════════════════════════════════════════════════

function SessionPhotoUpload({ s, onDone, mode = "session" }) {
  const isRitual = mode === "ritual";
  const listPath = isRitual ? '/api/arcana/rituals' : '/api/arcana/sessions';
  const uploadPath = (id) => isRitual
    ? `/api/arcana/rituals/${id}/photo`
    : `/api/arcana/sessions/by-slug/${id}/photo`;
  const { data, loading, error } = useApi(listPath);
  const items = useMemo(() => {
    if (loading || error) return [];
    if (isRitual) {
      return adaptRituals(data).map((r) => ({
        id: r.id,
        slug: r.id,
        title: r.name || "—",
        client: r.client || "Личный",
        firstDate: r.date || "",
        firstTripletId: r.id,
      }));
    }
    return adaptSessions(data);
  }, [data, loading, error, isRitual]);
  const sessions = items;
  const [pickedId, setPickedId] = useState(null);
  const [pickedTitle, setPickedTitle] = useState("");
  const [q, setQ] = useState("");
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const top = sessions.slice(0, 30);
    if (!needle) return top.slice(0, 10);
    return top.filter((x) =>
      (x.title || "").toLowerCase().includes(needle) ||
      (x.client || "").toLowerCase().includes(needle)
    ).slice(0, 10);
  }, [sessions, q]);

  const pickFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) {
      alert("Файл > 5 МБ");
      return;
    }
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
  };

  const upload = async () => {
    if (!pickedId || !file || busy) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const initData = window.Telegram?.WebApp?.initData || import.meta.env.VITE_DEV_INIT_DATA || "";
      const r = await fetch(uploadPath(pickedId), {
        method: "POST",
        headers: { "X-Telegram-Init-Data": initData },
        body: fd,
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "error" }));
        alert("Не получилось: " + (err.detail || r.status));
        return;
      }
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.("success"); } catch (_) {}
      onDone?.();
    } catch (err) {
      alert("Ошибка: " + err.message);
    } finally {
      setBusy(false);
    }
  };

  if (pickedId) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: fs(11), color: s.tS }}>{isRitual ? "Ритуал" : "Расклад"}</div>
        <Glass s={s} style={{ padding: "10px 14px" }}>
          <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>{pickedTitle}</div>
          <div
            onClick={() => { setPickedId(null); setPickedTitle(""); setFile(null); setPreviewUrl(null); }}
            style={{ fontSize: fs(11), color: s.tS, marginTop: 4, cursor: "pointer" }}
          >
            ← выбрать другой
          </div>
        </Glass>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          onChange={pickFile}
          style={{ display: "none" }}
        />
        {previewUrl ? (
          <Glass s={s} style={{ padding: 4 }}>
            <img
              src={previewUrl}
              alt="Превью"
              style={{ width: "100%", borderRadius: 8, display: "block" }}
            />
            <div
              onClick={() => fileRef.current?.click()}
              style={{ fontSize: fs(11), color: s.tS, padding: "6px 8px", cursor: "pointer", textAlign: "center" }}
            >
              сменить фото
            </div>
          </Glass>
        ) : (
          <Glass
            s={s}
            onClick={() => fileRef.current?.click()}
            style={{
              padding: "22px 14px", textAlign: "center",
              border: `1.5px dashed ${s.brd}`, cursor: "pointer",
            }}
          >
            <div style={{ fontSize: fs(28), marginBottom: 4 }}>📷</div>
            <div style={{ fontSize: fs(13), color: s.text }}>Выбрать фото</div>
            <div style={{ fontSize: fs(11), color: s.tS, marginTop: 2 }}>JPG/PNG, до 5 МБ</div>
          </Glass>
        )}
        <SubmitBtn
          s={s}
          disabled={!file || busy}
          label={busy ? "Загружаю..." : (isRitual ? "Привязать к ритуалу" : "Привязать к раскладу")}
          onClick={upload}
        />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: fs(11), color: s.tS }}>{isRitual ? "Выбери ритуал" : "Выбери расклад"}</div>
      <SearchInput s={s} value={q} onChange={setQ} placeholder={isRitual ? "Поиск по названию или клиенту" : "Поиск по вопросу или клиенту"} />
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <div style={{ fontSize: fs(12), color: s.red }}>Не удалось загрузить</div>}
      {!loading && !error && filtered.length === 0 && (
        <Empty s={s} emoji={isRitual ? "🕯️" : "🔮"} title={isRitual ? "Нет ритуалов" : "Нет раскладов"} text={isRitual ? "Сначала создай ритуал через бота." : "Сначала создай расклад через бота."} />
      )}
      {filtered.map((x) => {
        // Для сессий используем slug (бэк примет любой триплет сессии),
        // для ритуалов — id страницы.
        const id = isRitual ? (x.firstTripletId || x.id) : (x.slug || x.firstTripletId || x.id);
        if (!id) return null;
        return (
          <Glass
            key={x.slug || id}
            s={s}
            style={{ padding: "10px 14px" }}
            onClick={() => { setPickedId(id); setPickedTitle(x.title || "—"); }}
          >
            <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>{x.title}</div>
            <div style={{ fontSize: fs(10), color: s.tM, marginTop: 2 }}>
              {x.client}{x.firstDate ? ` · ${x.firstDate}` : ""}
            </div>
          </Glass>
        );
      })}
    </div>
  );
}

const CLIENT_TYPES_NEW = ["🤝 Платный", "🎁 Бесплатный"];

function ClientForm({ s, onSubmit, busy }) {
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [request, setRequest] = useState("");
  const [status, setStatus] = useState("🟢 Активный");
  const [ctype, setCtype] = useState("🤝 Платный");
  const [notes, setNotes] = useState("");
  const [birthday, setBirthday] = useState("");
  const valid = name.trim().length > 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Input s={s} value={name} onChange={setName} placeholder="Имя" />
      <Input s={s} value={contact} onChange={setContact} placeholder="Контакт (@telegram или телефон)" />
      <Input s={s} value={request} onChange={setRequest} placeholder="Запрос / тема" />
      <div style={{ fontSize: fs(11), color: s.tS }}>День рождения</div>
      <input
        type="date"
        value={birthday}
        onChange={(e) => setBirthday(e.target.value)}
        style={{
          width: "100%", padding: "10px 12px", borderRadius: 10,
          border: `1px solid ${s.brd}`, background: s.card, color: s.text,
          fontSize: fs(13), outline: "none",
        }}
      />
      <div style={{ fontSize: fs(11), color: s.tS }}>Тип</div>
      <div style={{ display: "flex", gap: 6 }}>
        {CLIENT_TYPES_NEW.map((t) => (
          <Pill key={t} s={s} active={ctype === t} onClick={() => setCtype(t)}>{t}</Pill>
        ))}
      </div>
      <div style={{ fontSize: fs(11), color: s.tS }}>Статус</div>
      <Select
        s={s}
        value={status}
        onChange={setStatus}
        options={["🟢 Активный", "⏸ Пауза", "⛔️ Архив"]}
      />
      <div style={{ fontSize: fs(11), color: s.tS }}>Заметки</div>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="—"
        rows={3}
        style={{
          width: "100%", padding: "10px 12px", borderRadius: 10,
          border: `1px solid ${s.brd}`, background: s.card, color: s.text,
          fontSize: fs(13), fontFamily: "inherit", resize: "vertical",
        }}
      />
      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={busy ? "Сохраняю..." : "Добавить клиента"}
        onClick={onSubmit(async () => {
          await apiPost("/api/arcana/clients", {
            name: name.trim(),
            contact,
            request,
            birthday: birthday || null,
            status,
            type: ctype,
            notes,
          });
        })}
      />
    </div>
  );
}

function ClientEditForm({ s, client, isSelf, onSaved }) {
  const [notes, setNotes] = useState(client.notes || "");
  const [request, setRequest] = useState(client.request || "");
  const [contact, setContact] = useState(client.contact || "");
  const [ctype, setCtype] = useState(client.type_full || "🤝 Платный");
  const [birthday, setBirthday] = useState(client.birthday || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const payload = { notes, request, contact, birthday: birthday || null };
      if (!isSelf) payload.type = ctype;
      await apiPost(`/api/arcana/clients/${client.id}/edit`, payload);
      onSaved?.();
    } catch (e) {
      alert(`Не получилось: ${e.message}`);
    } finally { setBusy(false); }
  };

  return (
    <Glass s={s} style={{ padding: "12px 14px", marginBottom: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: fs(11), color: s.tS }}>Контакт</div>
      <Input s={s} value={contact} onChange={setContact} placeholder="@telegram или телефон" />
      <div style={{ fontSize: fs(11), color: s.tS }}>Запрос</div>
      <Input s={s} value={request} onChange={setRequest} placeholder="—" />
      <div style={{ fontSize: fs(11), color: s.tS }}>День рождения</div>
      <input
        type="date"
        value={birthday}
        onChange={(e) => setBirthday(e.target.value)}
        style={{
          width: "100%", padding: "10px 12px", borderRadius: 10,
          border: `1px solid ${s.brd}`, background: s.card, color: s.text,
          fontSize: fs(13), outline: "none",
        }}
      />
      {!isSelf && (
        <>
          <div style={{ fontSize: fs(11), color: s.tS }}>Тип</div>
          <div style={{ display: "flex", gap: 6 }}>
            {CLIENT_TYPES_NEW.map((t) => (
              <Pill key={t} s={s} active={ctype === t} onClick={() => setCtype(t)}>{t}</Pill>
            ))}
          </div>
        </>
      )}
      <div style={{ fontSize: fs(11), color: s.tS }}>Заметки</div>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={3}
        style={{
          width: "100%", padding: "10px 12px", borderRadius: 10,
          border: `1px solid ${s.brd}`, background: s.card, color: s.text,
          fontSize: fs(13), fontFamily: "inherit", resize: "vertical",
        }}
      />
      <SubmitBtn
        s={s}
        disabled={busy}
        label={busy ? "Сохраняю..." : "Сохранить"}
        onClick={save}
      />
    </Glass>
  );
}

// ═══════════════════════════════════════════════════════════════
// APP
// ═══════════════════════════════════════════════════════════════

const nxTabs = [
  { k: "day", I: Sun, l: "День" },
  { k: "tasks", I: Check, l: "Задачи" },
  { k: "fin", I: Coins, l: "Финансы" },
  { k: "lists", I: ListIcon, l: "Списки" },
  { k: "mem", I: Brain, l: "Память" },
  { k: "cal", I: Calendar, l: "Календарь" },
];
const arTabs = [
  { k: "day", I: Moon, l: "День" },
  { k: "work", I: Sparkles, l: "Работы" },
  { k: "cli", I: Users, l: "Клиенты" },
  { k: "sess", I: LucideSparkles, l: "Расклады" },
  { k: "rit", I: Flame, l: "Ритуалы" },
  { k: "grim", I: BookOpen, l: "Гримуар" },
];

export default function App() {
  const [isN, setIsN] = useState(false);
  const [prog, setProg] = useState(0);
  const [nxP, setNxP] = useState("day");
  const [arP, setArP] = useState("day");
  const [modal, setModal] = useState(null);
  const [fabOpen, setFabOpen] = useState(false);
  const [fabForm, setFabForm] = useState(null);
  const aRef = useRef(null);
  const tX = useRef(null);

  const go = (toN) => {
    if (aRef.current) cancelAnimationFrame(aRef.current);
    const from = prog,
      to = toN ? 1 : 0,
      st = performance.now();
    const an = (now) => {
      const t = Math.min((now - st) / 1200, 1);
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      setProg(lerp(from, to, e));
      if (t < 1) aRef.current = requestAnimationFrame(an);
      else setIsN(toN);
    };
    setIsN(toN);
    aRef.current = requestAnimationFrame(an);
  };

  const sky = useMemo(() => getSky(prog), [prog]);
  // wave7.4: погодный тинт фона для Nexus
  const weatherApi = useApi('/api/weather');
  const weatherKind = weatherApi.data?.kind || 'clear';
  const sun = getOrb(prog, true);
  const moon = getOrb(prog, false);
  const stOp = prog > 0.5 ? (prog - 0.5) * 2 : 0;
  const suOp = prog < 0.7 ? 1 : Math.max(0, 1 - (prog - 0.7) / 0.3);
  const moOp = prog > 0.3 ? Math.min(1, (prog - 0.3) / 0.3) : 0;
  const isDay = prog < 0.5;
  const tabs = isDay ? nxTabs : arTabs;
  const page = isDay ? nxP : arP;
  const setPage = isDay ? setNxP : setArP;

  const openTask = (t) => setModal({ type: "task", payload: t });
  const openAdhd = () => setModal({ type: "adhd" });
  const openClient = (c) => setModal({ type: "client", payload: c });
  const openSession = (x) => setModal({ type: "session", payload: x });
  const openRitual = (r) => setModal({ type: "ritual", payload: r });
  const openGrimoire = (g) => setModal({ type: "grimoire", payload: g });
  const openWork = (w) => setModal({ type: "work", payload: w });
  // wave6.3: модалки для streaks + moon phases
  const openStreaks = () => setModal({ type: "streaks" });
  const openMoonPhases = () => setModal({ type: "moon-phases" });

  // wave9: внешний триггер фильтра для Расклады (тап «Сбылось в практике» → wait)
  const [sessFilterRequest, setSessFilterRequest] = useState(null);
  const consumeSessFilter = () => {
    const v = sessFilterRequest;
    if (v) setSessFilterRequest(null);
    return v;
  };

  const shared = {
    s: sky, openTask, openAdhd, openClient, openSession, openRitual, openGrimoire, openWork,
    openStreaks, openMoonPhases,
    sessFilterRequest, consumeSessFilter,
    // wave6.3: навигация по табам из виджетов
    navigate: (tab, opts) => {
      if (tab === "sess" && opts?.filter) setSessFilterRequest(opts.filter);
      setPage(tab);
    },
  };
  const nxS = { day: NxDay, tasks: NxTasks, fin: NxFinance, lists: NxLists, mem: NxMemory, cal: NxCal };
  const arS = { day: ArDay, work: ArWork, sess: ArSessions, cli: ArClients, rit: ArRituals, grim: ArGrimoire };
  const Scr = (isDay ? nxS : arS)[page];

  return (
    <div
      className={isDay ? "day" : "night"}
      onTouchStart={(e) => {
        tX.current = e.touches[0].clientX;
      }}
      onTouchEnd={(e) => {
        if (!tX.current) return;
        const dx = e.changedTouches[0].clientX - tX.current;
        tX.current = null;
        if (Math.abs(dx) > 60) {
          if (dx < 0 && !isN) go(true);
          if (dx > 0 && isN) go(false);
        }
      }}
      style={{
        position: "relative",
        width: "100%",
        minHeight: "100vh",
        overflow: "hidden",
        background: `linear-gradient(180deg, ${sky.deep} 0%, ${sky.mid} 25%, ${sky.warm} 55%, ${sky.glow} 75%, ${sky.base} 100%)`,
        fontFamily: B,
      }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&family=Caveat:wght@400;500&display=swap');
        @keyframes tw { 0% { opacity: 0.15 } 100% { opacity: 0.7 } }
        @keyframes nx-orbit { to { transform: rotate(360deg) } }
        @keyframes nx-pulse { 0%,100% { transform: scale(1); opacity: 0.55 } 50% { transform: scale(1.22); opacity: 0.95 } }
        @keyframes nx-glow { 0%,100% { opacity: 0.5; transform: scale(1) } 50% { opacity: 1; transform: scale(1.08) } }
        @keyframes nx-dot { 0%,80%,100% { opacity: 0.25; transform: translateY(0) } 40% { opacity: 1; transform: translateY(-2px) } }
        @keyframes nx-shimmer { 0% { background-position: -200% 0 } 100% { background-position: 200% 0 } }
        @keyframes nx-rain { 0% { transform: translateY(0); opacity: 0 } 10% { opacity: 1 } 100% { transform: translateY(105vh); opacity: 0.6 } }
        @keyframes nx-snow { 0% { transform: translateY(0); opacity: 0 } 10% { opacity: 1 } 100% { transform: translateY(105vh); opacity: 0.85 } }
        @keyframes nx-sway { 0% { margin-left: -8px } 100% { margin-left: 8px } }
        @keyframes nx-fog { 0% { transform: translateX(0) } 100% { transform: translateX(40%) } }
        @keyframes nx-cloud { 0% { transform: translateX(0) } 100% { transform: translateX(160vw) } }
        @keyframes nx-shine { 0% { opacity: 0.6 } 100% { opacity: 1 } }
        @property --nx-mcard-angle { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
        @keyframes nx-mcard-holo { from { --nx-mcard-angle: 0deg } to { --nx-mcard-angle: 360deg } }
        @keyframes nx-mcard-in {
          0%   { transform: scale(0.96) rotateX(8deg); opacity: 0; filter: blur(4px) }
          60%  { transform: scale(1.01) rotateX(-2deg); opacity: 1; filter: blur(0) }
          100% { transform: scale(1) rotateX(0); opacity: 1 }
        }
        @keyframes nx-mcard-pulse { 0%,100% { opacity: 0.6; transform: scale(1) } 50% { opacity: 1; transform: scale(1.4) } }
        @keyframes nx-holo-border { 0% { background-position: 0% 50% } 100% { background-position: 300% 50% } }
        @keyframes nx-holo-shine {
          0%   { transform: translateX(-120%) skewX(-14deg); opacity: 0 }
          20%  { opacity: 0.9 }
          60%  { opacity: 0.9 }
          100% { transform: translateX(220%) skewX(-14deg); opacity: 0 }
        }
        * { box-sizing: border-box; margin: 0; padding: 0 }
        body { overflow-x: hidden }
        input::placeholder { color: ${sky.tM}; opacity: 0.75 }
      `}</style>

      {/* wave7.4 + wave8.9: погодный overlay — видно разницу clear/cloudy/rain/snow/fog */}
      {isDay && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            zIndex: 1,
            opacity: 0.65,
            transition: "background 1s ease",
            background:
              weatherKind === "rain"
                ? "linear-gradient(180deg, rgba(60,85,105,0.75) 0%, rgba(100,125,145,0.45) 100%)"
                : weatherKind === "snow"
                ? "linear-gradient(180deg, rgba(220,228,240,0.65) 0%, rgba(255,255,255,0.35) 100%)"
                : weatherKind === "fog"
                ? "linear-gradient(180deg, rgba(170,175,175,0.7) 0%, rgba(200,200,200,0.4) 100%)"
                : weatherKind === "cloudy"
                ? "linear-gradient(180deg, rgba(140,160,170,0.55) 0%, rgba(160,170,180,0.3) 100%)"
                : "linear-gradient(180deg, rgba(255,230,150,0.25) 0%, rgba(255,210,120,0.1) 100%)",
          }}
        />
      )}

      {isDay && <WeatherFx kind={weatherKind} isDay={isDay} />}

      <Stars op={stOp} />

      <div
        style={{
          position: "absolute",
          left: `${sun.x}%`,
          top: `${sun.y}%`,
          width: 70,
          height: 70,
          borderRadius: "50%",
          transform: "translate(-50%,-50%)",
          background:
            "radial-gradient(circle, rgba(255,240,200,0.85) 0%, rgba(255,200,100,0.35) 40%, transparent 70%)",
          opacity: suOp,
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: `${moon.x}%`,
          top: `${moon.y}%`,
          width: 50,
          height: 50,
          borderRadius: "50%",
          transform: "translate(-50%,-50%)",
          background:
            "radial-gradient(circle, rgba(220,230,255,0.8) 0%, rgba(180,200,240,0.25) 50%, transparent 70%)",
          opacity: moOp,
          pointerEvents: "none",
        }}
      />

      {/* HEADER с аватаркой */}
      <div
        style={{
          padding: "14px 16px 6px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          position: "relative",
          zIndex: 2,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          {isDay ? <NexusLogo /> : <ArcanaLogo />}
        </div>
        <div
          className="mode-toggle"
          onClick={() => go(!isN)}
          style={{
            cursor: "pointer",
            fontSize: fs(11),
            color: sky.tS,
            userSelect: "none",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {isDay ? (
            <>
              <Moon size={fs(13)} color={sky.tS} /> →
            </>
          ) : (
            <>
              ← <Sun size={fs(13)} color={sky.tS} />
            </>
          )}
        </div>
      </div>

      {/* BODY */}
      <div style={{ padding: "6px 14px 120px", position: "relative", zIndex: 2 }}>
        {Scr && <Scr {...shared} />}
      </div>

      {/* FAB — wave7.5.5: скрываем когда открыт любой модал */}
      {!fabOpen && !fabForm && !modal && (
        <FAB s={sky} onClick={() => setFabOpen(true)} />
      )}

      {/* BOTTOM NAV */}
      <div
        style={{
          position: "fixed",
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 10,
          padding: "10px 10px 18px",
          display: "flex",
          justifyContent: "center",
          gap: 2,
          background: isDay
            ? "linear-gradient(to top, rgba(240,231,216,0.85) 0%, rgba(240,231,216,0.55) 60%, rgba(240,231,216,0) 100%)"
            : "linear-gradient(to top, rgba(14,18,34,0.85) 0%, rgba(14,18,34,0.55) 60%, rgba(14,18,34,0) 100%)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
        }}
      >
        {tabs.map((t) => {
          const active = page === t.k;
          const Ic = t.I;
          const iconInactiveColor = isDay ? sky.text : sky.tS;
          return (
            <div
              key={t.k}
              onClick={() => setPage(t.k)}
              style={{
                flex: 1,
                maxWidth: 84,
                textAlign: "center",
                padding: "8px 2px",
                borderRadius: 12,
                cursor: "pointer",
                background: active ? `${sky.acc}25` : "transparent",
                color: active ? sky.acc : sky.tS,
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", justifyContent: "center" }}>
                <Ic
                  size={fs(19)}
                  color={active ? sky.acc : iconInactiveColor}
                  strokeWidth={active ? 2.2 : 1.6}
                />
              </div>
              <div
                style={{
                  fontSize: fs(10),
                  marginTop: 3,
                  fontWeight: active ? 500 : 400,
                }}
              >
                {t.l}
              </div>
            </div>
          );
        })}
      </div>

      {/* МОДАЛКИ */}
      <Sheet s={sky} open={modal?.type === "task"} onClose={() => setModal(null)} title={modal?.payload?.title || "Задача"}>
        {modal?.payload && (
          <TaskSheet
            s={sky}
            task={modal.payload}
            onClose={() => setModal(null)}
          />
        )}
      </Sheet>

      <Sheet s={sky} open={modal?.type === "adhd"} onClose={() => setModal(null)} title="🦋 СДВГ-профиль">
        <AdhdSheet s={sky} open={modal?.type === "adhd"} />
      </Sheet>

      <Sheet s={sky} open={modal?.type === "streaks"} onClose={() => setModal(null)} title="🔥 Стрики">
        <StreaksSheet s={sky} open={modal?.type === "streaks"} />
      </Sheet>

      <Sheet s={sky} open={modal?.type === "moon-phases"} onClose={() => setModal(null)} title="Фазы луны">
        <MoonPhasesSheet s={sky} open={modal?.type === "moon-phases"} />
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "client"}
        onClose={() => setModal(null)}
        title="Клиент"
      >
        {modal?.payload?.id && <ClientDetail s={sky} id={modal.payload.id} />}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "session"}
        onClose={() => setModal(null)}
        title="Расклад"
      >
        {(modal?.payload?.slug || modal?.payload?.id) && (
          <SessionDetail
            s={sky}
            id={modal.payload.id}
            slug={modal.payload.slug || modal.payload.id}
          />
        )}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "ritual"}
        onClose={() => setModal(null)}
        title="Ритуал"
      >
        {modal?.payload?.id && <RitualDetail s={sky} id={modal.payload.id} />}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "grimoire"}
        onClose={() => setModal(null)}
        title="Гримуар"
      >
        {modal?.payload?.id && <GrimoireDetail s={sky} id={modal.payload.id} />}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "work"}
        onClose={() => setModal(null)}
        title="Работа"
      >
        {modal?.payload?.payload && (
          <WorkSheet s={sky} work={modal.payload.payload} onClose={() => setModal(null)} />
        )}
      </Sheet>

      <Sheet s={sky} open={fabOpen && !fabForm} onClose={() => setFabOpen(false)} title="Добавить">
        <QuickAdd
          s={sky}
          actions={isDay ? NEXUS_ADD : ARCANA_ADD}
          onPick={(k) => setFabForm(k)}
        />
      </Sheet>

      <Sheet
        s={sky}
        open={!!fabForm}
        onClose={() => { setFabForm(null); setFabOpen(false); }}
        title={FAB_TITLE[fabForm] || ""}
      >
        {fabForm && (
          <QuickForm
            s={sky}
            kind={fabForm}
            botType={isDay ? "nexus" : "arcana"}
            onDone={() => { setFabForm(null); setFabOpen(false); }}
          />
        )}
      </Sheet>
    </div>
  );
}

function StreaksSheet({ s, open }) {
  const { data, loading, error } = useApi(open ? "/api/streaks" : null, [open]);
  const week = useApi(open ? "/api/streaks/week" : null, [open]);
  if (!open) return null;
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} />;

  const weekDays = week?.data?.days || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Glass s={s} accent={s.amber} glow>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ fontSize: fs(42) }}>🔥</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: H, fontSize: fs(22), fontWeight: 500 }}>
              {data?.current || 0} дней
            </div>
            <div style={{ fontSize: fs(12), color: s.tM }}>
              Лучший: {data?.best || 0}
              {data?.last_activity_date ? ` · последний раз ${formatDate(data.last_activity_date)}` : ""}
            </div>
          </div>
        </div>
      </Glass>

      {weekDays.length > 0 && (
        <Glass s={s}>
          <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 8 }}>Последние 7 дней</div>
          <div style={{ display: "flex", gap: 6, justifyContent: "space-between" }}>
            {weekDays.map((d, i) => (
              <div key={i} style={{ flex: 1, textAlign: "center" }}>
                <div style={{
                  aspectRatio: "1/1",
                  borderRadius: 8,
                  border: d.is_today ? `2px solid ${s.amber}` : `1px solid ${s.brd}`,
                  background: d.has_activity ? `${s.amber}44` : "transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: fs(18),
                }}>
                  {d.has_activity ? "🔥" : ""}
                </div>
                <div style={{ fontSize: fs(10), color: s.tS, marginTop: 3 }}>{d.weekday}</div>
              </div>
            ))}
          </div>
        </Glass>
      )}

      <PerTaskStreaks s={s} list={data?.per_task || []} />
    </div>
  );
}

function PerTaskStreaks({ s, list }) {
  const active = list.filter((x) => (x.current || 0) > 0);
  const broken = list.filter((x) => (x.current || 0) === 0 && (x.best || 0) > 0);

  if (list.length === 0) {
    return (
      <div style={{ fontSize: fs(12), color: s.tM, textAlign: "center", padding: "12px 0" }}>
        Закрой повторяющуюся задачу, чтобы запустить счётчик ✨
      </div>
    );
  }

  return (
    <>
      {active.length > 0 && (
        <Glass s={s}>
          <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 8 }}>По задачам</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {active.map((t) => (
              <div key={t.task_id} style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "6px 4px",
              }}>
                <div style={{
                  fontSize: fs(22), fontFamily: H, color: s.amber,
                  fontWeight: 500, minWidth: 44, textAlign: "right",
                }}>
                  🔥{t.current}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500,
                                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {t.title || "—"}
                  </div>
                  <div style={{ fontSize: fs(10), color: s.tS, marginTop: 1 }}>
                    {t.repeat || ""}{t.best > t.current ? ` · лучший ${t.best}` : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Glass>
      )}
      {broken.length > 0 && (
        <Glass s={s} style={{ opacity: 0.7 }}>
          <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 8 }}>💔 Прерванные</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {broken.map((t) => (
              <div key={t.task_id} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                fontSize: fs(12), color: s.tM,
              }}>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.title || "—"}
                </span>
                <span style={{ fontSize: fs(11), color: s.tS }}>лучший {t.best}</span>
              </div>
            ))}
          </div>
        </Glass>
      )}
    </>
  );
}

function MoonPhasesSheet({ s, open }) {
  const { data, loading, error } = useApi(open ? "/api/arcana/moon-phases?count=4" : null, [open]);
  if (!open) return null;
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} />;

  const current = data?.current || {};
  const upcoming = data?.upcoming || [];
  const isRising = current.idx <= 3;  // 0..3 — растущая, 4..7 — убывающая

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Glass s={s} accent={s.acc} glow>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontSize: fs(64), filter: "drop-shadow(0 0 10px rgba(255,255,255,0.3))" }}>
            {current.glyph}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: H, fontStyle: "italic", fontSize: fs(24), fontWeight: 500 }}>
              {current.name}
            </div>
            <div style={{ fontSize: fs(14), color: s.text, opacity: 0.85, marginTop: 4 }}>
              освещённость {current.illum}% · {isRising ? "растущая" : "убывающая"}
            </div>
          </div>
        </div>
      </Glass>

      <div style={{ fontFamily: H, fontStyle: "italic", fontSize: fs(20), fontWeight: 500, marginBottom: 6 }}>Ближайшие фазы</div>
      {upcoming.map((p, i) => {
        const dt = p.date ? new Date(p.date) : null;
        const today = new Date();
        const daysAway = dt ? Math.round((dt - today) / (1000 * 60 * 60 * 24)) : null;
        return (
          <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ fontSize: fs(32) }}>{p.glyph}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: H, fontStyle: "italic", fontSize: fs(16), fontWeight: 500, color: s.text }}>{p.name}</div>
                <div style={{ fontSize: fs(12), color: s.text, opacity: 0.75 }}>
                  {formatDate(p.date)}{daysAway !== null && daysAway > 0 ? ` · через ${daysAway} ${daysAway === 1 ? "день" : "дн."}` : ""}
                </div>
              </div>
            </div>
          </Glass>
        );
      })}
    </div>
  );
}

function AdhdSheet({ s, open }) {
  const { data, loading, error, refetch } = useApi(open ? "/api/memory/adhd" : null, [open]);
  if (!open) return null;
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const view = adaptAdhd(data);
  const sections = [
    { key: "patterns",   title: "Паттерны",    glyph: "🔄", items: view.groups.patterns },
    { key: "strategies", title: "Стратегии",   glyph: "💡", items: view.groups.strategies },
    { key: "triggers",   title: "Триггеры",    glyph: "⚡", items: view.groups.triggers },
    { key: "specifics",  title: "Особенности", glyph: "📌", items: view.groups.specifics },
  ].filter((sec) => sec.items.length > 0);
  return (
    <>
      <Glass s={s} accent={s.acc} style={{ padding: "12px 14px", marginBottom: 12 }}>
        <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
          {view.profile ? renderBoldMd(view.profile) : "Профиль пока не сгенерирован."}
        </div>
      </Glass>
      {sections.map((sec) => (
        <div key={sec.key} style={{ marginBottom: 14 }}>
          <div style={{ fontFamily: H, fontSize: fs(14), color: s.text, marginBottom: 6 }}>
            {sec.glyph} {sec.title} ({sec.items.length})
          </div>
          {sec.items.map((it, i) => (
            <div
              key={i}
              style={{
                fontSize: fs(13),
                color: s.text,
                opacity: 0.85,
                fontStyle: "italic",
                padding: "3px 4px",
                lineHeight: 1.5,
              }}
            >
              • {it}
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

function GrimoireDetail({ s, id }) {
  const { data, loading, error, refetch } = useApi(id ? `/api/arcana/grimoire/${id}` : null, [id]);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const g = adaptGrimoireDetail(data);
  if (!g) return null;
  const catIcon = g.cat ? g.cat.split(" ")[0] : "📖";
  const catLabel = g.cat ? g.cat.split(" ").slice(1).join(" ") || g.cat : "";
  return (
    <div>
      {/* Шапка: иконка + caption uppercase золотым + название Lora */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", marginBottom: 10 }}>
        <div style={{ fontSize: fs(28), lineHeight: 1, marginTop: 2 }}>{catIcon}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontFamily: FONT_MONO, fontSize: fs(10), color: GRIM_GOLD,
            letterSpacing: "0.3em", textTransform: "uppercase", marginBottom: 4,
          }}>
            {catLabel || "запись"}
          </div>
          <div style={{
            fontFamily: H, fontSize: fs(22), fontWeight: 500,
            color: s.text, lineHeight: 1.2,
          }}>
            {g.name}
          </div>
          {g.themes.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 8 }}>
              {g.themes.map((t, i) => <GrimoireThemeChip key={i} theme={t} />)}
            </div>
          )}
        </div>
      </div>

      {/* Текст — Lora 14, накладной border-left золотом */}
      <div style={{
        padding: "14px 16px", marginBottom: 12,
        background: s.card, borderRadius: 12,
        borderLeft: `2px solid ${GRIM_GOLD}`,
        backdropFilter: "blur(10px)",
      }}>
        <div style={{
          fontFamily: FONT_MONO, fontSize: fs(9), color: s.tM,
          letterSpacing: "0.25em", textTransform: "uppercase", marginBottom: 6,
        }}>Текст</div>
        <div style={{
          fontFamily: H, fontSize: fs(14), color: s.text,
          lineHeight: 1.7, whiteSpace: "pre-wrap",
        }}>
          {g.content || "Текст пока не заполнен."}
        </div>
      </div>

      {/* Источник + бэйдж «Проверено» */}
      {(g.source || g.verified) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {g.source && (
            <div style={{
              flex: 1, minWidth: 120,
              padding: "10px 12px", borderRadius: 10,
              background: s.card, border: `1px solid ${s.brd}`,
              fontSize: fs(11), color: s.tS,
            }}>
              <div style={{
                fontFamily: FONT_MONO, fontSize: fs(9), color: s.tM,
                letterSpacing: "0.25em", textTransform: "uppercase", marginBottom: 4,
              }}>Источник</div>
              <div style={{ color: s.text }}>📚 {g.source}</div>
            </div>
          )}
          {g.verified && (
            <div style={{
              padding: "10px 12px", borderRadius: 10,
              background: `${GRIM_SAGE}1f`, border: `1px solid ${GRIM_SAGE}55`,
              fontSize: fs(12), color: GRIM_SAGE, fontWeight: 500,
              display: "flex", alignItems: "center", gap: 6,
            }}>
              ✓ Проверено
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActionRow({ s, icon, label, onClick, destructive }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 14px",
        background: s.card,
        border: `1px solid ${s.brd}`,
        borderRadius: 12,
        backdropFilter: "blur(10px)",
        cursor: "pointer",
        color: destructive ? s.red : s.text,
      }}
    >
      <span style={{ display: "flex" }}>{icon}</span>
      <span style={{ fontSize: fs(14) }}>{label}</span>
    </div>
  );
}
