import React, { useState, useRef, useMemo, useEffect } from "react";
import './newdesign.css'
import { useApi } from "./hooks/useApi";
import {
  adaptToday, adaptArcanaToday,
  adaptTasks, adaptFinanceToday, adaptFinanceMonth, adaptFinanceLimits, adaptFinanceGoals,
  adaptLists, adaptMemory, adaptAdhd, adaptCalendar,
  adaptSessions, adaptSessionDetail,
  adaptClients, adaptClientDossier,
  adaptRituals, adaptRitualDetail,
  adaptGrimoire, adaptGrimoireDetail,
  adaptArcanaStats,
  formatMonth, formatDate, formatShortDate,
} from "./adapters";
import { apiGet, apiPost } from "./api";
import {
  Sun, Moon as LucideMoon, Check, Coins, List as ListIcon, Brain, Calendar,
  Sparkles as LucideSparkles, Users, Flame as LucideFlame, BookOpen as LucideBookOpen,
  BarChart3, Plus, Search,
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
// wave5.7: минимальная санитизация HTML для трактовок раскладов.
// Разрешаем только безопасные теги форматирования, всё остальное эскейпим.
const SAFE_HTML_TAGS = new Set([
  "b", "strong", "i", "em", "br", "p", "ul", "ol", "li", "h3", "h4", "h5",
]);
function sanitizeHtml(raw) {
  if (!raw) return "";
  // Сначала эскейпим всё
  const escaped = String(raw)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  // Потом разрешаем только whitelisted теги (без атрибутов)
  return escaped.replace(
    /&lt;(\/?)([a-zA-Z][a-zA-Z0-9]*)\s*\/?&gt;/g,
    (m, slash, tag) => {
      if (SAFE_HTML_TAGS.has(tag.toLowerCase())) {
        return `<${slash}${tag}>`;
      }
      return m;
    }
  );
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
    text = "#3a3a2e";
    tS = "#5a564a";
    tM = "#7a756a";
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
    text = lerpC("#3a3a2e", "#d4ccc0", t);
    tS = lerpC("#5a564a", "#b0a898", t);
    tM = lerpC("#7a756a", "#807868", t);
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

// wave8.20: глобальный масштаб шрифтов и иконок для читаемости на мобильном.
// Применяется ко всем inline fontSize и size={...} через `fs()`.
// wave8.23: 1.5 оказался слишком крупным (текст съезжал, нав-таб
// обрезался). Понизил до 1.2 — лёгкий но заметный bump.
const FS = 1.2;
const fs = (n) => Math.round(n * FS);

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

const PrioDot = ({ s, prio }) => {
  const colors = { "🔴": "var(--nx-red)", "🟡": "var(--nx-amber)", "⚪": "var(--nx-text-mute)", "high": "var(--nx-red)", "medium": "var(--nx-amber)", "low": "var(--nx-text-mute)" };
  return <span className="prio-dot" style={{ background: colors[prio] || "var(--nx-text-mute)" }} />;
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
  const accent = isDayMode ? "#d4844e" : s.acc;       // солнце днём, луна-бирюза ночью
  const halo   = isDayMode ? "#7a3a10" : s.acc;       // тёмный ореол днём для контраста
  const coreInner = isDayMode ? "#fff2c8" : "#eef4ff"; // тёплый блик днём, холодный ночью
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
      <div style={{ position: "relative", width: size, height: size }}>
        {/* halo */}
        <div
          style={{
            position: "absolute", inset: -10, borderRadius: "50%",
            background: `radial-gradient(circle, ${halo}88 0%, ${halo}00 65%)`,
            animation: "nx-pulse 2.4s ease-in-out infinite",
          }}
        />
        {/* core orb */}
        <div
          style={{
            position: "absolute", inset: 14, borderRadius: "50%",
            background: `radial-gradient(circle at 35% 30%, ${coreInner} 0%, ${accent} 55%, ${accent}cc 100%)`,
            boxShadow: `0 0 10px ${accent}88`,
            animation: "nx-glow 2.4s ease-in-out infinite",
          }}
        />
        {/* orbit ring */}
        <div
          style={{
            position: "absolute", inset: 0, borderRadius: "50%",
            border: `1px dashed ${halo}88`,
          }}
        />
        {/* orbiting dots */}
        <div
          style={{
            position: "absolute", inset: 0,
            animation: "nx-orbit 1.6s linear infinite",
          }}
        >
          {[0, 120, 240].map((deg, i) => (
            <div
              key={i}
              style={{
                position: "absolute",
                top: "50%", left: "50%",
                width: 6, height: 6, borderRadius: "50%",
                background: accent,
                boxShadow: `0 0 6px ${accent}`,
                transform: `rotate(${deg}deg) translate(${orbitR}px) rotate(-${deg}deg) translate(-50%, -50%)`,
              }}
            />
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
  return (
    <div
      style={{
        // wave7.8.4: fixed — иначе при прокрутке overlay затемняет экран,
        // а содержимое sheet уходит за пределы viewport
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
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
          background: s.base,
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
  // wave8.59: отметка задачи чекбоксом пишет Status=Done в Notion.
  const toggle = async (id) => {
    if (!id || done[id]) return;
    setDone((p) => ({ ...p, [id]: true }));
    try {
      await apiPost(`/api/tasks/${id}/done`);
      refetch();
    } catch (_) {
      setDone((p) => ({ ...p, [id]: false }));
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
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => openStreaks?.()}>
            <Metric s={s} v={<span className="streak-v"><LucideFlame size={20} fill="currentColor" style={{ flexShrink: 0, color: s.amber }} className="flame" />{t.streak}</span>} sub="стрик" accent={s.amber} />
          </div>
        </div>
        <div className="hero-budget">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 13, fontWeight: 500, cursor: "pointer" }} onClick={() => navigate?.("fin")}>
            <span style={{ opacity: 0.75 }}>Бюджет дня</span>
            <span style={{ color: leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.acc, fontWeight: 700 }}>{t.budgetDay.toLocaleString()} ₽ · {leftPct}%</span>
          </div>
          <Bar s={s} pct={leftPct} color={leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.acc} />
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>потрачено {t.spentDay.toLocaleString()} ₽ из {t.budgetDay.toLocaleString()} ₽</div>
        </div>
      </div>

      {t.adhdTip && (
        <div className="tip" style={{ marginTop: 8 }}>
          <div className="tip-h">
            <span>🦋 СДВГ-совет</span>
            <RefreshCw size={15} style={{ cursor: "pointer", opacity: 0.6 }} onClick={async () => { try { await apiPost("/api/today/refresh-tip"); refetch(); } catch (_) {} }} />
          </div>
          <div className="tip-body">{renderBoldMd(t.adhdTip)}</div>
        </div>
      )}

      <SectionLabel s={s}>Расписание</SectionLabel>
      {t.overdue.length === 0 && t.scheduled.length === 0 && (
        <div className="glass" style={{ padding: "16px", textAlign: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 4 }}>На сегодня пусто — отдыхай ✨</div>
          <div style={{ fontSize: 13, opacity: 0.6, lineHeight: 1.5 }}>Если хочется чем-то заняться — загляни во вкладку «Задачи».</div>
        </div>
      )}
      {t.overdue.map((o) => (
        <div key={o.id} className="task glass" style={{ opacity: done[o.id] ? 0.45 : 1 }}>
          <Chk s={s} done={done[o.id]} onClick={() => toggle(o.id)} />
          <div className="body" onClick={() => openTask(o)} style={{ cursor: "pointer" }}>
            <div className="title">{o.title}</div>
            <div className="meta">
              <span style={{ color: s.red, fontWeight: 600 }}>{o.days} д назад</span>
              {o.rpt && <span>{o.rpt}</span>}
            </div>
          </div>
          {o.cat && <div className="cat-badge">{String(o.cat).split(" ")[0]}</div>}
          <PrioDot s={s} prio={o.prio} />
        </div>
      ))}
      {t.scheduled.map((x) => (
        <div key={x.id} className="task glass" style={{ opacity: done[x.id] ? 0.45 : 1 }}>
          {x.time && <span className="time">{x.time}</span>}
          <Chk s={s} done={done[x.id]} onClick={() => toggle(x.id)} />
          <div className="body" onClick={() => openTask(x)} style={{ cursor: "pointer" }}>
            <div className="title">{x.title}</div>
            <div className="meta">
              {x.date && <span>{x.date}</span>}
              {x.rpt && <span>🔄 {x.rpt}</span>}
              {x.streak > 0 && <span>🔥 {x.streak}</span>}
            </div>
          </div>
          {x.cat && <div className="cat-badge">{String(x.cat).split(" ")[0]}</div>}
          <PrioDot s={s} prio={x.prio} />
        </div>
      ))}
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
      <SectionLabel s={s}>Задачи</SectionLabel>
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
                  <div style={{ fontSize: fs(13), color: s.tM, marginBottom: 4 }}>Потрачено сегодня</div>
                  <div style={{ fontFamily: H, fontSize: fs(32), color: s.text, fontWeight: 500, lineHeight: 1 }}>
                    {total.toLocaleString()} <span style={{ fontSize: fs(18), fontWeight: 400 }}>₽</span>
                  </div>
                </div>
                <span style={{ fontSize: 28 }}>💰</span>
              </div>
            </Glass>
            {budget && (
              <Glass s={s} accent={s.acc} style={{ padding: "12px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: fs(15), color: s.text, fontWeight: 500, marginBottom: 6 }}>
                  <span>Бюджет дня</span>
                  <span>{budget.day.toLocaleString()} ₽ · {budget.pct}%</span>
                </div>
                <Bar s={s} pct={budget.pct} color={budget.pct > 85 ? s.red : budget.pct > 60 ? s.amber : s.acc} />
                <div style={{ fontSize: fs(13), color: s.tM, marginTop: 6 }}>
                  Потрачено {budget.spent.toLocaleString()} ₽ · осталось {budget.left.toLocaleString()} ₽
                </div>
              </Glass>
            )}
            <div className="section-h">Транзакции</div>
            {items.length === 0 && <Empty s={s} emoji="💚" title="Пока не тратила" text="Сегодня без трат — приятно." />}
            {items.map((x) => (
              <Glass key={x.id} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>{x.desc || "без описания"}</div>
                    <div style={{ fontSize: fs(13), color: s.tM, marginTop: 2 }}>{x.cat}</div>
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
                  <div style={{ fontSize: fs(13), color: s.tM }}>Доход</div>
                  <div style={{ fontFamily: H, fontSize: fs(20), color: s.acc, fontWeight: 500 }}>
                    {inc.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: fs(13), color: s.tM }}>Расход</div>
                  <div style={{ fontFamily: H, fontSize: fs(20), fontWeight: 500 }}>
                    {exp.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: fs(13), color: s.tM }}>Баланс</div>
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
                  <div style={{ fontSize: fs(13), color: s.tM, marginTop: 5 }}>
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
                  <span style={{ fontSize: fs(16), color: s.text, fontWeight: 500 }}>
                    {d.n}
                  </span>
                  <span style={{ fontSize: fs(16), color: s.red, fontWeight: 500, fontFamily: H }}>
                    {d.left.toLocaleString()} ₽
                  </span>
                </div>
                <div style={{ fontSize: fs(13), color: s.tM, marginTop: 3 }}>
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
                <div style={{ fontSize: fs(13), color: s.tM, marginTop: 3 }}>
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
                      <span style={{ fontSize: fs(14), color: s.tM, fontFamily: H }}>
                        {d.total.toLocaleString()} ₽
                      </span>
                    </div>
                    <div style={{ fontSize: fs(12), color: s.tM, marginTop: 3 }}>
                      закрыт{d.closedAt ? ` · ${fmtClosed(d.closedAt)}` : ""}
                    </div>
                  </Glass>
                ))}
                {closedGoals.map((g, i) => (
                  <Glass key={`cg${i}`} s={s} style={{ padding: "10px 14px", marginBottom: 4, opacity: 0.75 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontSize: fs(15), color: s.text, fontWeight: 500 }}>🎯 {g.n}</span>
                      <span style={{ fontSize: fs(14), color: s.tM, fontFamily: H }}>
                        {g.t.toLocaleString()} ₽
                      </span>
                    </div>
                    <div style={{ fontSize: fs(12), color: s.tM, marginTop: 3 }}>
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
          <span style={{ fontSize: fs(13), color: s.tM }}>Сумма долга</span>
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
          <div style={{ fontSize: fs(13), color: s.tM, marginTop: 3 }}>{formatDate(it.date)}</div>
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
          fontSize: fs(13), color: overdue ? s.red : s.tM,
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
                  <div style={{ fontSize: fs(13), color: s.tM, marginTop: 3 }}>до {x.exp}</div>
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
          <div style={{ fontSize: fs(10), color: s.tM, marginTop: 2 }}>{m.cat}</div>
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
                            fontSize: fs(8), color: s.tM,
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
                      <span style={{ color: s.tM }}>{wd.tasks.length > 0 ? `${wd.tasks.length} шт.` : ""}</span>
                    </div>
                    {wd.tasks.length > 0 && (
                      <div style={{ marginTop: 4 }}>
                        {wd.tasks.slice(0, 3).map((t, j) => (
                          <div key={j} style={{ fontSize: fs(11), color: s.tM, marginTop: 2 }}>
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
// ARCANA — MY DAY (с фазой луны)
// ═══════════════════════════════════════════════════════════════

function ArDay({ s, openClient, navigate, openMoonPhases }) {
  const [done, setDone] = useState({});
  const { data, loading, error, refetch } = useApi('/api/arcana/today');
  const weatherApi = useApi('/api/weather');
  const rawSessions = data?.sessions_today?.length || 0;
  const rawWorks = data?.works_today?.length || 0;
  const tipApi = useApi(data ? `/api/arcana/tip?sessions=${rawSessions}&works=${rawWorks}` : null, [rawSessions, rawWorks]);

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;

  const a = adaptArcanaToday(data);
  const moon = a.moon;
  const worksTotal = a.worksToday.length;
  const worksDone = Object.values(done).filter(Boolean).length;
  const sessionsTotal = a.sessionsToday.length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, position: "relative" }}>
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
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("clients")}>
            <Metric s={s} v={worksDone} unit={`/${worksTotal}`} sub="работы" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("stats")}>
            <Metric s={s} v={a.monthBlock.inc >= 1000 ? `${Math.round(a.monthBlock.inc / 1000)}к` : a.monthBlock.inc} unit="₽" sub="доход" accent={s.acc} />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate?.("stats")}>
            <Metric s={s} v={`${a.accuracy}%`} sub="точность" accent={s.amber} />
          </div>
        </div>
      </div>

      <div className="moon-hero glass glow" onClick={() => openMoonPhases?.()}>
        <div className="glyph">{moon.glyph}</div>
        <div className="info">
          <div className="name">{moon.name}</div>
          <div className="sub">{moon.days} день цикла · освещение {moon.illum}%</div>
          <Bar s={s} pct={moon.illum} color={s.acc} />
        </div>
      </div>

      <SectionLabel s={s}>Статистика за {a.monthBlock.label}</SectionLabel>
      <div className="grid-2">
        {[
          { ico: "💰", v: `${a.monthBlock.inc.toLocaleString()}₽`, l: "Доход", accent: s.acc },
          { ico: "🕯️", v: `${a.monthBlock.supplies.toLocaleString()}₽`, l: "Расходники" },
          { ico: "✨", v: `${a.monthBlock.accuracy}%`, l: "Сбылось", accent: s.acc },
          { ico: "🃏", v: a.monthBlock.sessions, l: "Сеансов" },
        ].map((item, i) => (
          <div key={i} className="glass" style={{ padding: "16px 12px", textAlign: "center" }}>
            <div style={{ fontSize: 22, marginBottom: 6 }}>{item.ico}</div>
            <div style={{ fontFamily: H, fontSize: 24, fontWeight: 500, color: item.accent }}>{item.v}</div>
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4, fontWeight: 500 }}>{item.l}</div>
          </div>
        ))}
      </div>

      {a.sessionsToday.length > 0 && (
        <>
          <SectionLabel s={s}>Сеансы сегодня</SectionLabel>
          {a.sessionsToday.map((x) => (
            <div key={x.id} className="task glass" style={{ cursor: "pointer" }} onClick={() => x.client_id && openClient({ id: x.client_id })}>
              <span className="time">{x.time}</span>
              <div style={{ width: 30, height: 30, borderRadius: "50%", background: `${s.acc}22`, color: s.acc, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: H, fontSize: 13, fontWeight: 500, flexShrink: 0 }}>{x.client[0]}</div>
              <div className="body">
                <div className="title">{x.client}</div>
                <div className="meta"><span>{x.type} · {x.area}</span></div>
              </div>
              <ChevronRight size={16} color={s.tS} />
            </div>
          ))}
        </>
      )}

      {a.worksToday.length > 0 && (
        <>
          <SectionLabel s={s}>Работы</SectionLabel>
          {a.worksToday.map((w) => (
            <div key={w.id} className="task glass" style={{ opacity: done[w.id] ? 0.45 : 1 }}>
              <Chk s={s} done={done[w.id]} onClick={() => setDone((p) => ({ ...p, [w.id]: !p[w.id] }))} />
              <div className="body">
                <div className="title" style={{ textDecoration: done[w.id] ? "line-through" : "none" }}>{w.title}</div>
                <div className="meta"><span>{w.cat}</span></div>
              </div>
              <PrioDot s={s} prio={w.prio} />
            </div>
          ))}
        </>
      )}

      {worksTotal === 0 && a.unchecked30d === 0 && (
        <div style={{ textAlign: "center", padding: "20px 0 4px", fontStyle: "italic", fontSize: 14, opacity: 0.55, fontFamily: H }}>
          Сегодня в практике спокойно 🌙
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — SESSIONS
// ═══════════════════════════════════════════════════════════════

function ArSessions({ s, openSession }) {
  const [f, setF] = useState("all");
  const path = f === "all" ? "/api/arcana/sessions" : `/api/arcana/sessions?filter=area:${encodeURIComponent(f)}`;
  const { data, loading, error, refetch } = useApi(path, [f]);
  const list = loading || error ? [] : adaptSessions(data);
  const areas = ["all", ...new Set(list.map((x) => x.area).filter(Boolean))];
  const unchecked = list.filter((x) => (x.done || "").startsWith("⏳")).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span className="page-title">Расклады</span>
        {unchecked > 0 && (
          <span style={{ fontSize: fs(11), color: s.amber }}>⏳ {unchecked} непроверено</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {areas.map((a) => (
          <Pill key={a} s={s} active={f === a} onClick={() => setF(a)}>
            {a === "all" ? "Все" : a}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && list.length === 0 && (
        <Empty s={s} emoji="🔮" title="Раскладов нет" text="Пока тишина — карты ждут." />
      )}
      {!loading && !error && list.map((x) => {
        const cardsBrief = (x.cards || []).map((c) => c.name).slice(0, 3).join(", ") + (x.cards.length > 3 ? `, +${x.cards.length - 3}` : "");
        return (
          <div key={x.id} className="glass tap" style={{ padding: "14px 16px", marginBottom: 6 }} onClick={() => openSession({ id: x.id })}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
              <div className="flex-grow">
                <div style={{ fontFamily: H, fontSize: 18, fontWeight: 500, lineHeight: 1.2 }}>{x.q || "без темы"}</div>
                <div style={{ fontSize: 12, opacity: 0.65, marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {[x.type, x.deck, x.client, x.date].filter(Boolean).map((item, i) => <span key={i}>{item}</span>)}
                </div>
                {cardsBrief && <div style={{ fontSize: 13, fontStyle: "italic", marginTop: 6, opacity: 0.75, fontFamily: H }}>{cardsBrief}</div>}
              </div>
              <span style={{ fontSize: 18 }}>{(x.done || "⏳").split(" ")[0]}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — CLIENTS
// ═══════════════════════════════════════════════════════════════

function ArClients({ s, openClient }) {
  const { data, loading, error, refetch } = useApi("/api/arcana/clients");
  const view = loading || error
    ? { clients: [], total: 0, total_debt: 0 }
    : adaptClients(data);
  const total = view.total || view.clients.length;
  const debt = view.total_debt;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Glass s={s} glow>
        <span className="page-title">Клиенты</span>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <Metric s={s} v={total} sub="всего" />
          <Metric
            s={s}
            v={debt > 0 ? `${(debt / 1000).toFixed(1)}к` : "0"}
            sub="долги"
            accent={debt > 0 ? s.red : undefined}
          />
        </div>
      </Glass>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && view.clients.length === 0 && (
        <Empty s={s} emoji="👥" title="Пока без клиентов" text="Когда придёт первый — появится здесь." />
      )}
      {!loading && !error && view.clients.map((c) => (
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
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  background: `${s.acc}22`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: fs(14),
                  color: s.acc,
                  fontWeight: 500,
                  fontFamily: H,
                }}
              >
                {c.initial}
              </div>
              <div>
                <div style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>
                  {c.status} {c.name}
                  {c.self && (
                    <span style={{ color: s.tS, fontWeight: 400, fontSize: fs(11) }}> · я</span>
                  )}
                </div>
                <div style={{ fontSize: fs(10), color: s.tM }}>
                  {c.sessions} сеансов · {c.rituals} ритуалов
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
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — RITUALS
// ═══════════════════════════════════════════════════════════════

function ArRituals({ s, openRitual }) {
  const [goal, setGoal] = useState("all");
  const path = goal === "all"
    ? "/api/arcana/rituals"
    : `/api/arcana/rituals?goal=${encodeURIComponent(goal)}`;
  const { data, loading, error, refetch } = useApi(path, [goal]);
  const list = loading || error ? [] : adaptRituals(data);
  const goals = ["all", ...new Set(list.map((r) => r.goal).filter(Boolean))];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Ритуалы</div>
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
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — GRIMOIRE
// ═══════════════════════════════════════════════════════════════

function ArGrimoire({ s, openGrimoire }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const params = [];
  if (cat !== "all") params.push(`cat=${encodeURIComponent(cat)}`);
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const path = "/api/arcana/grimoire" + (params.length ? "?" + params.join("&") : "");
  const { data, loading, error, refetch } = useApi(path, [cat, q]);
  const view = loading || error ? { items: [], categories: [] } : adaptGrimoire(data);
  const cats = ["all", ...view.categories];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Гримуар</div>
      <SearchInput s={s} value={q} onChange={setQ} placeholder="Поиск в гримуаре" />
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
        <Empty s={s} emoji="📖" title="Гримуар пуст" text="Записи о колодах и картах появятся тут." />
      )}
      {!loading && !error && view.items.map((g) => (
        <Glass
          key={g.id}
          s={s}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={openGrimoire ? () => openGrimoire({ id: g.id }) : undefined}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: fs(13), color: s.text }}>{g.name}</span>
            <span style={{ fontSize: fs(13) }}>{g.theme}</span>
          </div>
          <div style={{ fontSize: fs(10), color: s.tM, marginTop: 2 }}>{g.cat}</div>
        </Glass>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ARCANA — STATS (точность раскладов)
// ═══════════════════════════════════════════════════════════════

function ArStats({ s }) {
  const { data, loading, error, refetch } = useApi("/api/arcana/stats");
  // wave6.6.1: quick-verify секция
  const uncheckedApi = useApi("/api/arcana/sessions?filter=status:unchecked", []);
  const [verifyBusy, setVerifyBusy] = useState({});
  const [localDone, setLocalDone] = useState({});

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const view = adaptArcanaStats(data);
  const months = view.months;
  const allVer = view.allVer;
  const pct = view.pct;
  const p = view.practice;
  const profit = p.profit;

  const unchecked = ((uncheckedApi.data?.sessions) || []).filter(
    (sess) => !localDone[sess.id]
  );

  const doVerify = async (id, status) => {
    if (verifyBusy[id]) return;
    setVerifyBusy((b) => ({ ...b, [id]: true }));
    setLocalDone((d) => ({ ...d, [id]: true }));
    try {
      await apiPost(`/api/arcana/sessions/${id}/verify`, { status });
      setTimeout(() => { uncheckedApi.refetch && uncheckedApi.refetch(); refetch(); }, 400);
    } catch (e) {
      setLocalDone((d) => { const n = { ...d }; delete n[id]; return n; });
      alert("Не удалось отметить");
    } finally {
      setVerifyBusy((b) => { const n = { ...b }; delete n[id]; return n; });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="page-title">Точность</div>

      {unchecked.length > 0 && (
        <Glass s={s} accent={s.amber} style={{ padding: "12px 14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "baseline" }}>
            <span style={{ fontSize: fs(13), color: s.text, fontWeight: 500 }}>❗ Ждут проверки</span>
            <span style={{ fontSize: fs(11), color: s.tS }}>{unchecked.length}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {unchecked.slice(0, 8).map((sess) => (
              <div key={sess.id} style={{ background: s.card, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ fontSize: fs(12), color: s.text, fontWeight: 500 }}>
                  {sess.question || sess.title || "Без темы"}
                </div>
                <div style={{ fontSize: fs(10), color: s.tM, marginBottom: 6 }}>
                  {sess.client || ""}{sess.date ? ` · ${formatDate(sess.date)}` : ""}
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                  <div onClick={() => doVerify(sess.id, "✅ Да")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.good + "33", color: s.good, fontSize: fs(11), cursor: "pointer" }}>
                    ✅ Сбылось
                  </div>
                  <div onClick={() => doVerify(sess.id, "〰️ Частично")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.amber + "33", color: s.amber, fontSize: fs(11), cursor: "pointer" }}>
                    〰️ Частично
                  </div>
                  <div onClick={() => doVerify(sess.id, "❌ Нет")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.red + "33", color: s.red, fontSize: fs(11), cursor: "pointer" }}>
                    ❌ Нет
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Glass>
      )}

      {/* Большой процент */}
      <Glass s={s} glow style={{ textAlign: "center", padding: "22px 14px" }}>
        <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 6 }}>
          Общий процент сбывшихся раскладов
        </div>
        <div
          style={{
            fontFamily: H,
            fontSize: fs(52),
            fontWeight: 600,
            color: s.acc,
            lineHeight: 1,
            letterSpacing: -1,
          }}
        >
          {pct}%
        </div>
        <div style={{ fontSize: fs(11), color: s.tM, marginTop: 6 }}>
          за всё время · {allVer} проверенных
        </div>
      </Glass>

      {/* Финансы практики */}
      <Glass s={s}>
        <div style={{ fontFamily: H, fontSize: fs(13), color: s.tS, marginBottom: 8 }}>
          Финансы практики · апрель
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Metric s={s} v={`${(p.inc / 1000).toFixed(0)}к`} sub="доход" accent={s.acc} />
          <Metric s={s} v={`${(p.exp / 1000).toFixed(0)}к`} sub="расход" />
          <Metric s={s} v={`${(profit / 1000).toFixed(0)}к`} sub="прибыль" accent={s.acc} />
        </div>
      </Glass>

      {/* По месяцам с трёхцветной полосой */}
      <SectionLabel s={s}>По месяцам</SectionLabel>
      {months.map((m, i) => {
        const ver = m.yes + m.partial + m.no;
        const mp = ver > 0 ? Math.round((m.yes / ver) * 100) : 0;
        return (
          <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              }}
            >
              <span
                style={{ fontSize: fs(14), color: s.text, fontWeight: 500, fontFamily: H }}
              >
                {m.name}
              </span>
              <span style={{ fontSize: fs(16), color: s.acc, fontWeight: 600, fontFamily: H }}>
                {mp}%
              </span>
            </div>
            {/* Трёхцветная полоса */}
            <div
              style={{
                display: "flex",
                gap: 3,
                height: 8,
                borderRadius: 4,
                overflow: "hidden",
              }}
            >
              {m.yes > 0 && (
                <div style={{ flex: m.yes, background: "#22c55e", borderRadius: 4 }} />
              )}
              {m.partial > 0 && (
                <div style={{ flex: m.partial, background: "#f59e0b", borderRadius: 4 }} />
              )}
              {m.no > 0 && (
                <div style={{ flex: m.no, background: "#ef4444", borderRadius: 4 }} />
              )}
            </div>
            <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: fs(11) }}>
              <span style={{ color: "#22c55e" }}>✓ {m.yes}</span>
              <span style={{ color: "#f59e0b" }}>~ {m.partial}</span>
              <span style={{ color: "#ef4444" }}>✗ {m.no}</span>
              <span style={{ color: s.tM, marginLeft: "auto" }}>
                всего: {m.total}
              </span>
            </div>
          </Glass>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// SESSION DETAIL SHEET
// ═══════════════════════════════════════════════════════════════

function SessionPhoto({ s, id, url, onUploaded }) {
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [localUrl, setLocalUrl] = useState(url || null);

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
      const r = await fetch(`/api/arcana/sessions/${id}/photo`, {
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
            width: "100%", aspectRatio: "2/3", objectFit: "cover",
            borderRadius: 8, boxShadow: `0 2px 8px ${s.brd}`,
          }}
        />
      ) : (
        <div style={{
          width: "100%", aspectRatio: "2/3", borderRadius: 8,
          background: s.card, border: `1px solid ${s.brd}`,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: fs(32),
        }}>🃏</div>
      )}
      <div style={{ textAlign: "center", width: "100%" }}>
        <div style={{ fontSize: fs(11), color: s.text, fontWeight: 500, lineHeight: 1.2 }}>
          {card.en || card.raw || "—"}
        </div>
        {card.ru && (
          <div style={{ fontSize: fs(10), color: s.tM, lineHeight: 1.2 }}>
            {card.ru}
          </div>
        )}
      </div>
    </div>
  );
}

function SessionDetail({ s, id }) {
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
        <Glass s={s} accent={s.acc} style={{ marginTop: 10, padding: "10px 12px" }}>
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
        <div
          style={{
            fontFamily: H,
            fontSize: fs(20),
            color: s.text,
            fontWeight: 500,
            marginBottom: 8,
          }}
        >
          {r.name}
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
          <span style={{ color: s.text }}>{r.client || "— (личный)"}</span>
          <span style={{ color: s.tS }}>📅 Дата</span>
          <span style={{ color: s.text }}>{r.date}</span>
          <span style={{ color: s.tS }}>🎯 Цель</span>
          <span style={{ color: s.text }}>{r.goal}</span>
          <span style={{ color: s.tS }}>📍 Место</span>
          <span style={{ color: s.text }}>{r.place}</span>
          {r.question && (
            <>
              <span style={{ color: s.tS }}>❓ Вопрос</span>
              <span style={{ color: s.text, gridColumn: "2 / span 3", fontSize: fs(11) }}>
                {r.question}
              </span>
            </>
          )}
          {r.price > 0 && (
            <>
              <span style={{ color: s.tS }}>💳 Оплата</span>
              <span
                style={{
                  color: r.paid >= r.price ? s.acc : s.red,
                  fontWeight: 500,
                  gridColumn: "2 / span 3",
                }}
              >
                {r.price.toLocaleString()} ₽ ·{" "}
                {r.paid >= r.price ? "оплачено" : `долг ${(r.price - r.paid).toLocaleString()} ₽`}
              </span>
            </>
          )}
        </div>
      </Glass>

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
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const c = adaptClientDossier(data);
  if (!c) return null;
  return (
    <div>
      <div style={{ display: "flex", gap: 14, marginBottom: 16 }}>
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
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: H, fontSize: fs(22), fontWeight: 500 }}>
            {c.status} {c.name}
            {c.self && (
              <span style={{ fontSize: fs(13), color: s.tS, fontWeight: 400 }}> · я</span>
            )}
          </div>
          <div style={{ fontSize: fs(12), color: s.tS, marginTop: 3 }}>
            {c.contact} · с {c.since}
          </div>
          <div style={{ fontSize: fs(12), color: s.text, marginTop: 5 }}>
            <span style={{ color: s.tS }}>Запрос:</span> {c.request}
          </div>
        </div>
      </div>

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

      <Glass s={s} accent={s.acc} style={{ marginBottom: 14 }}>
        <div style={{ fontSize: fs(10), color: s.tS, marginBottom: 4, display: "inline-flex", alignItems: "center", gap: 4 }}>
          <StickyNote size={fs(11)} /> Заметки
        </div>
        <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.55 }}>{c.notes}</div>
      </Glass>

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
          <span>{h.paid ? "✅" : "⚠️"}</span>
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
  { key: "session", icon: Sparkles, label: "Расклад" },
  { key: "ritual", icon: Flame, label: "Ритуал" },
  { key: "expense", icon: Wallet, label: "Финансы" },
  { key: "grimoire", icon: BookOpen, label: "В гримуар" },
  { key: "photo", icon: Camera, label: "Фото расклада" },
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
// QUICK FORMS (FAB → выбранная категория → форма)
// ═══════════════════════════════════════════════════════════════

const FAB_TITLE = {
  expense: "Финансы",
  task: "Новая задача",
  note: "Новая заметка",
  list: "В список",
  memory: "В память",
  photo: "Фото чека",
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

function ClientForm({ s, onSubmit, busy }) {
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [request, setRequest] = useState("");
  const [status, setStatus] = useState("🟢 Активный");
  const valid = name.trim().length > 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Input s={s} value={name} onChange={setName} placeholder="Имя" />
      <Input s={s} value={contact} onChange={setContact} placeholder="Контакт (@telegram или телефон)" />
      <Input s={s} value={request} onChange={setRequest} placeholder="Запрос / тема" />
      <div style={{ fontSize: fs(11), color: s.tS }}>Статус</div>
      <Select
        s={s}
        value={status}
        onChange={setStatus}
        options={["🟢 Активный", "⏸ Пауза", "⛔️ Архив"]}
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
            status,
          });
        })}
      />
    </div>
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
  { k: "sess", I: Sparkles, l: "Расклады" },
  { k: "cli", I: Users, l: "Клиенты" },
  { k: "rit", I: Flame, l: "Ритуалы" },
  { k: "grim", I: BookOpen, l: "Гримуар" },
  { k: "stats", I: BarChart3, l: "Точность" },
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
  // wave6.3: модалки для streaks + moon phases
  const openStreaks = () => setModal({ type: "streaks" });
  const openMoonPhases = () => setModal({ type: "moon-phases" });

  const shared = {
    s: sky, openTask, openAdhd, openClient, openSession, openRitual, openGrimoire,
    openStreaks, openMoonPhases,
    // wave6.3: навигация по табам из виджетов
    navigate: (tab) => setPage(tab),
  };
  const nxS = { day: NxDay, tasks: NxTasks, fin: NxFinance, lists: NxLists, mem: NxMemory, cal: NxCal };
  const arS = { day: ArDay, sess: ArSessions, cli: ArClients, rit: ArRituals, grim: ArGrimoire, stats: ArStats };
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
                  color={active ? sky.acc : sky.tS}
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
        {modal?.payload?.id && <SessionDetail s={sky} id={modal.payload.id} />}
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

      <div style={{ fontSize: fs(11), color: s.tM, textAlign: "center" }}>
        {/* TODO(кай): per-task streaks — нужна доп. схема */}
        Стрики по отдельным повторяющимся задачам — в разработке.
      </div>
    </div>
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
  return (
    <div>
      <div style={{ fontFamily: H, fontSize: fs(20), marginBottom: 6 }}>{g.name}</div>
      <div style={{ fontSize: fs(11), color: s.tS, marginBottom: 12 }}>
        {g.cat}{g.themes.length > 0 ? ` · ${g.themes.join(", ")}` : ""}
      </div>
      <Glass s={s} accent={s.acc} style={{ padding: "12px 14px", marginBottom: 10 }}>
        <div style={{ fontSize: fs(13), color: s.text, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
          {g.content || "Текст пока не заполнен."}
        </div>
      </Glass>
      {g.source && (
        <div style={{ fontSize: fs(11), color: s.tS, fontStyle: "italic" }}>
          Источник: {g.source}
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
