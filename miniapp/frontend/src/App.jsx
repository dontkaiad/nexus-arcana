import { useState, useRef, useMemo, useEffect } from "react";
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
  Bell, RefreshCw, X, Camera, Mic, Pencil, ChevronRight,
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
  amber = lerpC("#b8822a", "#d9a441", p);
  good = lerpC("#6b8f71", "#5a9a78", p);
  if (p < 0.3) {
    const t = p / 0.3;
    d = lerpC("#4a7a78", "#3a6a72", t);
    m = lerpC("#5a8a7a", "#5a7a88", t);
    w = lerpC("#8ab4a0", "#c4a060", t);
    g = lerpC("#c4c898", "#d4884a", t);
    b = lerpC("#dce8dc", "#e8dcc8", t);
    // wave8.9: чуть теплее/мягче вместо почти чёрного — заголовки теряли в гармонии
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

const H = "'Lora', Georgia, serif";
const B = "'Nunito', -apple-system, 'SF Pro Text', system-ui, sans-serif";

// ═══════════════════════════════════════════════════════════════
// CORE COMPONENTS
// ═══════════════════════════════════════════════════════════════

const Glass = ({ s, children, style, accent, glow, onClick }) => (
  <div
    onClick={onClick}
    style={{
      background: s.card,
      borderRadius: 16,
      border: `1px solid ${s.brd}`,
      borderLeft: accent ? `3px solid ${accent}` : undefined,
      backdropFilter: "blur(20px)",
      WebkitBackdropFilter: "blur(20px)",
      padding: "12px 14px",
      position: "relative",
      cursor: onClick ? "pointer" : undefined,
      ...style,
    }}
  >
    {glow && (
      <div
        style={{
          position: "absolute",
          top: -30,
          left: "50%",
          width: 100,
          height: 60,
          background: `radial-gradient(ellipse, ${s.acc}28 0%, transparent 70%)`,
          transform: "translateX(-50%)",
          pointerEvents: "none",
        }}
      />
    )}
    {children}
  </div>
);

const Pill = ({ s, active, children, onClick }) => (
  <div
    onClick={onClick}
    style={{
      padding: "5px 12px",
      borderRadius: 20,
      fontSize: 12,
      cursor: "pointer",
      // wave6.2.5: неактивный таб — прозрачный фон + тонкая граница + полный текст
      // wave7.8.1: активный таб — тёмный текст на светлой заливке для контраста
      background: active ? `${s.acc}30` : "transparent",
      color: active ? s.text : s.tM,
      border: `1px solid ${active ? s.acc + "66" : s.brd}`,
      fontFamily: B,
      fontWeight: active ? 600 : 500,
      whiteSpace: "nowrap",
      transition: "all 0.2s",
      backdropFilter: active ? "blur(10px)" : undefined,
    }}
  >
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
  <div style={{ height: 4, background: s.brd, borderRadius: 2, overflow: "hidden" }}>
    <div
      style={{
        height: "100%",
        width: `${Math.min(Math.max(pct, 0), 100)}%`,
        background: color || s.acc,
        borderRadius: 2,
        transition: "width 0.6s ease",
      }}
    />
  </div>
);

const Metric = ({ s, v, sub, unit, accent, icon }) => (
  <div
    style={{
      flex: 1,
      textAlign: "center",
      padding: "9px 2px",
      background: `${s.acc}14`,
      borderRadius: 10,
    }}
  >
    <div
      style={{
        fontFamily: H,
        fontSize: 22,
        fontWeight: 700,
        color: accent || s.text,
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        justifyContent: "center",
      }}
    >
      {icon}
      {v}
      {unit && (
        <span style={{ color: s.tM, fontSize: 14, fontWeight: 500, marginLeft: 2 }}>{unit}</span>
      )}
    </div>
    <div style={{ fontSize: 12, color: s.text, opacity: 0.8, fontWeight: 500, marginTop: 2 }}>{sub}</div>
  </div>
);

const Chk = ({ s, done, onClick }) => (
  <div
    onClick={onClick}
    style={{
      width: 22,
      height: 22,
      borderRadius: 6,
      border: `2px solid ${done ? s.acc : s.brd}`,
      background: done ? s.acc : "transparent",
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: 0,
      transition: "all 0.2s",
    }}
  >
    {done && <Check size={12} color="#fff" strokeWidth={3} />}
  </div>
);

const PrioDot = ({ s, prio }) => {
  const c =
    prio === "🔴" || prio === "high"
      ? s.red
      : prio === "🟡" || prio === "medium"
      ? s.amber
      : s.tM;
  return <span style={{ width: 7, height: 7, borderRadius: "50%", background: c, flexShrink: 0 }} />;
};

const SectionLabel = ({ s, children, action }) => (
  <div
    style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "baseline",
      padding: "0 4px",
      margin: "10px 0 6px",
    }}
  >
    <span
      style={{
        fontFamily: H,
        fontSize: 16,
        fontWeight: 600,
        color: s.text,
        letterSpacing: 0.3,
      }}
    >
      {children}
    </span>
    {action}
  </div>
);

const Empty = ({ s, text, chill, emoji, title }) => {
  if (chill || emoji || title) {
    // Wave 5 «чилл»-оформление: plaque/card вместо серого текста
    return (
      <Glass s={s} style={{ padding: "24px 14px", textAlign: "center" }}>
        {emoji && <div style={{ fontSize: 36, marginBottom: 6 }}>{emoji}</div>}
        {title && (
          <div style={{ fontFamily: H, fontSize: 18, color: s.text }}>{title}</div>
        )}
        <div style={{ fontSize: 13, color: s.tM, marginTop: title ? 4 : 0 }}>{text}</div>
      </Glass>
    );
  }
  return (
    <div
      style={{
        textAlign: "center",
        padding: "18px 12px",
        color: s.tS,
        fontSize: 12,
        fontStyle: "italic",
      }}
    >
      {text}
    </div>
  );
};

const ErrorBox = ({ s, error, refetch }) => (
  <Glass s={s} accent={s.red} style={{ padding: "14px 16px" }}>
    <div style={{ fontSize: 13, color: s.red, fontWeight: 500, marginBottom: 6 }}>
      Ошибка загрузки
    </div>
    <div style={{ fontSize: 12, color: s.tM, marginBottom: 10, wordBreak: "break-word" }}>
      {error?.message || "неизвестная ошибка"}
    </div>
    {refetch && (
      <div
        onClick={refetch}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "6px 12px", borderRadius: 8,
          background: `${s.acc}22`, color: s.acc,
          fontSize: 12, cursor: "pointer",
        }}
      >
        <RefreshCw size={12} /> Повторить
      </div>
    )}
  </Glass>
);

// Финальные логотипы: Satisfy + градиентный диск с кратерами и ореолом
const NexusLogo = () => (
  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
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
    <svg width="130" height="56" viewBox="0 0 130 56" xmlns="http://www.w3.org/2000/svg">
      <text x="0" y="40" fontFamily="Satisfy, cursive" fontSize="44" fill="#0a2e22">Nexus</text>
    </svg>
  </div>
);

const ArcanaLogo = () => (
  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
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
    <svg width="150" height="56" viewBox="0 0 150 56" xmlns="http://www.w3.org/2000/svg">
      <text x="0" y="40" fontFamily="Satisfy, cursive" fontSize="44" fill="#f0f2f8">Arcana</text>
    </svg>
  </div>
);

const FAB = ({ s, onClick }) => (
  <div
    onClick={onClick}
    style={{
      // wave5.4: fixed + safe-area-inset; wave8.7: glass-стиль с акцентной рамкой
      position: "fixed",
      bottom: "calc(env(safe-area-inset-bottom, 0px) + 80px)",
      right: 16,
      width: 52,
      height: 52,
      borderRadius: 26,
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
    <Plus size={24} strokeWidth={2.2} />
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
          padding: "14px 16px 28px",
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
            <span style={{ fontFamily: H, fontSize: 20, color: s.text }}>{title}</span>
            <span
              onClick={onClose}
              style={{ color: s.tS, cursor: "pointer", display: "flex" }}
            >
              <X size={20} />
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
          fontSize: 12,
          color: s.acc,
          fontWeight: 500,
          minWidth: 38,
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
        {t.cat && (
          <span style={{
            display: "inline-flex", alignItems: "center",
            padding: "1px 7px", borderRadius: 9,
            fontSize: 10, background: `${s.acc}22`, color: s.text,
            flexShrink: 0, whiteSpace: "nowrap",
          }}>
            {t.cat}
          </span>
        )}
        <span style={{
          fontSize: 15,
          color: s.text,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}>
          {t.title}
        </span>
      </div>
      <div
        style={{
          fontSize: 11,
          color: s.tM,
          marginTop: 2,
          display: "flex",
          gap: 6,
          alignItems: "center",
        }}
      >
        {t.deadlineTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            📅 {t.deadlineTime}
          </span>
        )}
        {t.reminderTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            <Bell size={9} /> {t.reminderTime}
          </span>
        )}
        {t.rem && !t.reminderTime && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            <Bell size={9} /> {t.rem}
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
  // wave8.13: модалка выбора города (window.prompt не работает в Telegram)
  const [cityModal, setCityModal] = useState(false);
  const [cityInput, setCityInput] = useState("");
  const [cityBusy, setCityBusy] = useState(false);

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) {
    return (
      <Glass s={s} accent={s.red} style={{ padding: "14px 16px" }}>
        <div style={{ fontSize: 13, color: s.red, fontWeight: 500, marginBottom: 6 }}>
          Ошибка загрузки
        </div>
        <div style={{ fontSize: 12, color: s.tM, marginBottom: 10, wordBreak: "break-word" }}>
          {error.message}
        </div>
        <div
          onClick={refetch}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8,
            background: `${s.acc}22`, color: s.acc,
            fontSize: 12, cursor: "pointer",
          }}
        >
          <RefreshCw size={12} /> Повторить
        </div>
      </Glass>
    );
  }

  const t = adaptToday(data);
  const doneCount = Object.values(done).filter(Boolean).length;
  const total = t.scheduled.length + t.tasks.length + (t.noDate?.length || 0);
  const leftPct = Math.round((t.spentDay / t.budgetDay) * 100);
  const toggle = (id) => setDone((p) => ({ ...p, [id]: !p[id] }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Glass s={s} glow>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>Мой день</span>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 11, color: s.tS }}>{t.date}</div>
            <div
              style={{ fontSize: 11, color: s.text, opacity: 0.8, marginTop: 2, cursor: "pointer" }}
              onClick={() => {
                setCityInput(weatherApi.data?.city || "");
                setCityModal(true);
              }}
            >
              {weatherApi.data && !weatherApi.data.error ? (
                <>
                  {WEATHER_ICON[weatherApi.data.kind] || "🌤️"}
                  {" "}
                  {weatherApi.data.temp > 0 ? "+" : ""}{weatherApi.data.temp}° · {weatherApi.data.city}
                </>
              ) : (
                <>🌤️ указать город</>
              )}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate && navigate("tasks")}>
            <Metric s={s} v={`${doneCount}`} unit={`/${total}`} sub="задачи" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate && navigate("fin")}>
            <Metric s={s} v={`${Math.round((t.budgetDay - t.spentDay) / 1000)}к`} unit="₽" sub="свободно" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => openStreaks && openStreaks()}>
            <Metric
              s={s}
              v={t.streak}
              sub="стрик"
              accent={s.amber}
              icon={<LucideFlame size={14} color={s.amber} fill={s.amber} style={{ opacity: 0.9 }} />}
            />
          </div>
        </div>
        <div
          onClick={() => navigate && navigate("fin")}
          style={{ marginTop: 11, paddingTop: 10, borderTop: `1px solid ${s.brd}`, cursor: "pointer" }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 14,
              color: s.text,
              marginBottom: 5,
            }}
          >
            <span style={{ fontWeight: 500 }}>Бюджет дня</span>
            <span
              style={{
                color: leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.text,
                fontWeight: 600,
              }}
            >
              {(t.budgetDay - t.spentDay).toLocaleString()} ₽ · {100 - leftPct}%
            </span>
          </div>
          <Bar
            s={s}
            pct={leftPct}
            color={leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.acc}
          />
          <div style={{ fontSize: 12, color: s.text, opacity: 0.75, marginTop: 4 }}>
            потрачено {t.spentDay.toLocaleString()} ₽ из {t.budgetDay.toLocaleString()} ₽
          </div>
        </div>
      </Glass>

      {/* wave7.3: СДВГ-совет поднят наверх. wave8.6: refresh по клику. */}
      {t.adhdTip && (
        <Glass s={s} accent={s.acc}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 4,
            }}
          >
            <span
              style={{
                fontSize: 11,
                color: s.acc,
                fontWeight: 500,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              🦋 СДВГ-совет
            </span>
            <RefreshCw
              size={13}
              color={s.tS}
              style={{ cursor: "pointer" }}
              onClick={async () => {
                try {
                  await apiPost("/api/today/refresh-tip");
                  refetch();
                } catch (_) { /* ignore */ }
              }}
            />
          </div>
          <div style={{ fontSize: 13, color: s.text, lineHeight: 1.5 }}>{renderBoldMd(t.adhdTip)}</div>
        </Glass>
      )}

      {t.overdue.length > 0 && (
        <Glass s={s} accent={s.red} style={{ padding: "10px 14px" }}>
          <div style={{ fontSize: 11, color: s.red, fontWeight: 500, marginBottom: 5 }}>
            Просрочено · {t.overdue.length}
          </div>
          {t.overdue.map((o) => (
            <div
              key={o.id}
              onClick={() => openTask(o)}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "2px 0",
                cursor: "pointer",
              }}
            >
              <span style={{ fontSize: 13, color: s.text }}>
                {o.cat} {o.title}
              </span>
              <span style={{ fontSize: 11, color: s.red }}>{o.days} д назад</span>
            </div>
          ))}
        </Glass>
      )}

      {t.scheduled.length > 0 && (
        <>
          <SectionLabel s={s}>Расписание</SectionLabel>
          {t.scheduled.map((x) => (
            <TaskRow
              key={x.id}
              s={s}
              t={x}
              done={done[x.id]}
              onToggle={() => toggle(x.id)}
              onOpen={() => openTask(x)}
              withTime
            />
          ))}
        </>
      )}

      {t.tasks.length > 0 && (
        <>
          <SectionLabel s={s}>Сегодня</SectionLabel>
          {t.tasks.map((x) => (
            <TaskRow
              key={x.id}
              s={s}
              t={x}
              done={done[x.id]}
              onToggle={() => toggle(x.id)}
              onOpen={() => openTask(x)}
            />
          ))}
        </>
      )}

      {t.noDate && t.noDate.length > 0 && (
        <>
          <SectionLabel s={s}>📌 Без срока</SectionLabel>
          {t.noDate.map((x) => (
            <TaskRow
              key={x.id}
              s={s}
              t={x}
              done={done[x.id]}
              onToggle={() => toggle(x.id)}
              onOpen={() => openTask(x)}
            />
          ))}
        </>
      )}

      {total === 0 && <Empty s={s} text="На сегодня пусто — отдыхай 🌿" />}

      <Sheet s={s} open={cityModal} onClose={() => setCityModal(false)} title="Твой город">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 13, color: s.tM, fontFamily: B }}>
            Погода подтянется для указанного города
          </div>
          <input
            autoFocus
            value={cityInput}
            onChange={(e) => setCityInput(e.target.value)}
            onKeyDown={async (e) => {
              if (e.key === "Enter" && cityInput.trim() && !cityBusy) {
                setCityBusy(true);
                try {
                  await apiPost("/api/weather/city", { city: cityInput.trim() });
                  setCityModal(false);
                  weatherApi.refetch();
                } finally { setCityBusy(false); }
              }
            }}
            placeholder="Санкт-Петербург"
            style={{
              background: s.card, border: `1px solid ${s.brd}`,
              borderRadius: 10, padding: "10px 12px",
              color: s.text, fontFamily: B, fontSize: 14, outline: "none",
            }}
          />
          <SubmitBtn
            s={s}
            disabled={!cityInput.trim() || cityBusy}
            label={cityBusy ? "Сохраняю..." : "Сохранить"}
            onClick={async () => {
              setCityBusy(true);
              try {
                await apiPost("/api/weather/city", { city: cityInput.trim() });
                setCityModal(false);
                weatherApi.refetch();
              } finally { setCityBusy(false); }
            }}
          />
        </div>
      </Sheet>
    </div>
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
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Задачи</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {[
          ["all", "Все"],
          ["active", "Активные"],
          ["overdue", "Просрочено"],
          ["done", "Выполнено"],
        ].map(([k, l]) => (
          <Pill key={k} s={s} active={f === k} onClick={() => setF(k)}>
            {l}
          </Pill>
        ))}
      </div>
      {loading && <Empty s={s} text="Загружаю..." />}
      {error && <ErrorBox s={s} error={error} refetch={refetch} />}
      {!loading && !error && list.length === 0 && (
        <Empty s={s} emoji="🌿" title="Чилл" text="На сегодня задач нет. Можно отдохнуть." />
      )}
      {!loading && !error && list.map((t) => (
        <Glass
          key={t.id}
          s={s}
          accent={t.status === "overdue" ? s.red : undefined}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openTask(t)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0,
              textDecoration: t.status === "done" ? "line-through" : "none",
              opacity: t.status === "done" ? 0.55 : 1,
            }}>
              {t.cat && (
                <span style={{
                  display: "inline-flex", alignItems: "center",
                  padding: "1px 8px", borderRadius: 10,
                  fontSize: 10, background: `${s.acc}22`, color: s.text,
                  flexShrink: 0, whiteSpace: "nowrap",
                }}>
                  {t.cat}
                </span>
              )}
              <span style={{ fontSize: 14, color: s.text }}>{t.title}</span>
            </div>
            <span style={{ fontSize: 12, flexShrink: 0 }}>{t.prio}</span>
          </div>
          <div
            style={{
              fontSize: 10,
              color: t.status === "overdue" ? s.red : s.tM,
              marginTop: 3,
              display: "flex",
              gap: 6,
            }}
          >
            {t.date && <span>{t.date}</span>}
            {t.rpt && <span>{t.rpt}</span>}
            {t.status === "done" && <span>✓ сделано</span>}
          </div>
        </Glass>
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
        <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Финансы</div>
        {tabsUi}
        <Empty s={s} text="Загружаю..." />
      </div>
    );
  }
  if (error) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Финансы</div>
        {tabsUi}
        <ErrorBox s={s} error={error} refetch={refetch} />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Финансы</div>
      {tabsUi}

      {tab === "today" && (() => {
        const { total, items, budget } = adaptFinanceToday(data);
        return (
          <>
            <Glass s={s}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontSize: 12, color: s.tS }}>Потрачено сегодня</span>
                <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>
                  {total.toLocaleString()} ₽
                </span>
              </div>
            </Glass>
            {budget && (
              <Glass s={s} accent={s.acc} style={{ padding: "10px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: s.tS, marginBottom: 4 }}>
                  <span>Бюджет дня</span>
                  <span>{budget.day.toLocaleString()} ₽ · {budget.pct}%</span>
                </div>
                <Bar s={s} pct={budget.pct} color={budget.pct > 85 ? s.red : budget.pct > 60 ? s.amber : s.acc} />
                <div style={{ fontSize: 10, color: s.tM, marginTop: 4 }}>
                  Потрачено {budget.spent.toLocaleString()} ₽ · осталось {budget.left.toLocaleString()} ₽
                </div>
              </Glass>
            )}
            {items.length === 0 && <Empty s={s} emoji="💚" title="Пока не тратила" text="Сегодня без трат — приятно." />}
            {items.map((x) => (
              <Glass key={x.id} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: 13, color: s.text }}>{x.desc || "без описания"}</div>
                    <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>{x.cat}</div>
                  </div>
                  <span style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
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
              <div style={{ fontSize: 11, color: s.tS, marginBottom: 4 }}>{monthLabel}</div>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: s.tM }}>Доход</div>
                  <div style={{ fontFamily: H, fontSize: 18, color: s.acc, fontWeight: 500 }}>
                    {inc.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: s.tM }}>Расход</div>
                  <div style={{ fontFamily: H, fontSize: 18, color: s.text, fontWeight: 500 }}>
                    {exp.toLocaleString()} ₽
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: s.tM }}>Баланс</div>
                  <div
                    style={{
                      fontFamily: H, fontSize: 18,
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
                  style={{ padding: "8px 14px", marginBottom: 4, cursor: "pointer" }}
                  onClick={() => setDrillCat({ full: catFull, display: c.name, month: monthIso })}
                >
                  <div
                    style={{
                      display: "flex", justifyContent: "space-between",
                      fontSize: 12, color: s.text, marginBottom: 4,
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
                  style={{ padding: "8px 14px", marginBottom: 4, cursor: "pointer" }}
                  onClick={() => setDrillCat({ full: catFull, display: c.name, month: monthIso })}
                >
                  <div
                    style={{
                      display: "flex", justifyContent: "space-between",
                      fontSize: 12, color: s.text, marginBottom: 4,
                    }}
                  >
                    <span>{c.name}</span>
                    <span style={{ color: clr, fontWeight: 500 }}>{c.pct}%</span>
                  </div>
                  <Bar s={s} pct={c.pct} color={clr} />
                  <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                    {c.spent.toLocaleString()} ₽ / {c.limit.toLocaleString()} ₽
                  </div>
                </Glass>
              );
            })}
          </>
        );
      })()}

      {tab === "goals" && (() => {
        const { debts, goals } = adaptFinanceGoals(data);
        return (
          <>
            <SectionLabel s={s}>Долги</SectionLabel>
            {debts.length === 0 && <Empty s={s} text="Долгов нет 🌿" />}
            {debts.map((d, i) => (
              <Glass key={i} s={s} accent={s.amber} style={{ padding: "10px 14px", marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                    {d.n}
                  </span>
                  <span style={{ fontSize: 14, color: s.red, fontWeight: 500, fontFamily: H }}>
                    {d.left.toLocaleString()} ₽
                  </span>
                </div>
                <div style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>
                  {d.by ? `до ${d.by}` : ""}{d.note ? ` · ${d.note}` : ""}
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
                  <span style={{ fontSize: 14, color: s.text, fontFamily: H }}>{g.n}</span>
                  <span style={{ fontSize: 13, color: s.acc, fontWeight: 500 }}>
                    {g.t.toLocaleString()} ₽
                  </span>
                </div>
                <div style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>
                  {g.monthly > 0 ? `откладываю ${g.monthly.toLocaleString()} ₽/мес` : `после ${g.after}`}
                </div>
                {g.t > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <Bar s={s} pct={(g.s / g.t) * 100} color={s.acc} />
                  </div>
                )}
              </Glass>
            ))}
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
    </div>
  );
}

function CategoryDrillSheet({ s, cat, month }) {
  const path = `/api/finance/category?cat=${encodeURIComponent(cat)}&month=${month}`;
  const { data, loading, error, refetch } = useApi(path, [cat, month]);
  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) return <ErrorBox s={s} error={error} refetch={refetch} />;
  const items = data?.items || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 13, color: s.tS, marginBottom: 4 }}>
        Всего: <span style={{ color: s.text, fontWeight: 500 }}>{(data?.total || 0).toLocaleString()} ₽</span> · {data?.count || 0} шт.
      </div>
      {items.length === 0 && <Empty s={s} emoji="🌿" title="Пусто" text="Тут трат нет." />}
      {items.map((it) => (
        <Glass key={it.id} s={s} style={{ padding: "8px 12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontSize: 13, color: s.text }}>{it.desc || "—"}</span>
            <span style={{ fontSize: 13, color: s.text, fontWeight: 500, fontFamily: H }}>
              {it.amount.toLocaleString()} ₽
            </span>
          </div>
          <div style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>{formatDate(it.date)}</div>
        </Glass>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — LISTS
// ═══════════════════════════════════════════════════════════════

function NxLists({ s }) {
  const [tab, setTab] = useState("buy");
  const [q, setQ] = useState("");
  const qEnc = encodeURIComponent(q || "");
  const path = q ? `/api/lists?type=${tab}&q=${qEnc}` : `/api/lists?type=${tab}`;
  const { data, loading, error, refetch } = useApi(path, [tab, q]);
  const apiItems = loading || error ? [] : adaptLists(data);

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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Списки</div>
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
          <div style={{ fontSize: 14, color: s.text }}>{emptyText}</div>
        </Glass>
      )}
      {!loading && !error && tab !== "inv" &&
        items.map((x) => (
          <Glass
            key={x.id}
            s={s}
            style={{
              padding: "8px 14px", marginBottom: 4, opacity: x.done ? 0.5 : 1,
              cursor: "pointer",
            }}
            onClick={() => toggleDone(x)}
          >
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <Chk s={s} done={x.done} />
              <span style={{ fontSize: 13, color: s.text, textDecoration: x.done ? "line-through" : "none" }}>
                {x.cat} {x.name}
              </span>
            </div>
          </Glass>
        ))}
      {!loading && !error && tab === "inv" &&
        items.map((x) => (
          <Glass key={x.id} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 13, color: s.text }}>{x.name}</span>
              <span style={{ fontSize: 13, color: s.acc, fontWeight: 500 }}>{x.qty ? `${x.qty} шт` : ""}</span>
            </div>
            {x.exp && (
              <div style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>до {x.exp}</div>
            )}
          </Glass>
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
      <Search size={14} color={s.tS} />
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
          fontSize: 13,
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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Память</div>
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
            <div style={{ fontSize: 11, color: s.acc, fontWeight: 500 }}>🦋 СДВГ-профиль</div>
            <div style={{ fontSize: 12, color: s.text, marginTop: 2 }}>
              Персональные паттерны и стратегии
            </div>
          </div>
          <ChevronRight size={16} color={s.tS} />
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
          <div style={{ fontSize: 13, color: s.text }}>{m.text}</div>
          <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>{m.cat}</div>
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

function NxCal({ s }) {
  const now = new Date();
  const [view, setView] = useState("month");
  const [monthStr, setMonthStr] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`
  );
  const [picked, setPicked] = useState(now.getDate());
  const daysShort = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  const { data, loading, error, refetch } = useApi(`/api/calendar?month=${monthStr}`, [monthStr]);
  const { tasksByDay } = loading || error
    ? { tasksByDay: {} }
    : adaptCalendar(data);

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
        <span style={{ fontFamily: H, fontSize: 20, color: s.text }}>{title}</span>
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
                style={{ textAlign: "center", fontSize: 10, color: s.tS, padding: 3 }}
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
                        fontSize: 13,
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
                            fontSize: 8, color: s.tM,
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
              {weekDays.map((wd, i) => (
                <div key={i} style={{
                  padding: "8px 10px", borderRadius: 8,
                  background: wd.isToday ? `${s.acc}18` : "transparent",
                  border: `1px solid ${wd.isToday ? s.acc + "55" : s.brd}`,
                }}>
                  <div style={{
                    display: "flex", justifyContent: "space-between",
                    fontSize: 12, color: wd.isToday ? s.acc : s.text, fontWeight: 500,
                  }}>
                    <span>{wd.label}</span>
                    <span style={{ color: s.tM }}>{wd.tasks.length > 0 ? `${wd.tasks.length} шт.` : ""}</span>
                  </div>
                  {wd.tasks.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      {wd.tasks.slice(0, 3).map((t, j) => (
                        <div key={j} style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>
                          • {t}
                        </div>
                      ))}
                      {wd.tasks.length > 3 && (
                        <div style={{ fontSize: 10, color: s.tS, marginTop: 2 }}>
                          и ещё {wd.tasks.length - 3}…
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Glass>
        );
      })()}
      <SectionLabel s={s}>
        {picked} {RU_MONTHS_FULL[month0].toLowerCase()}
      </SectionLabel>
      {loading && <Empty s={s} text="Загружаю..." />}
      {!loading && !tasksByDay[picked] && (
        <Empty s={s} chill emoji="📅" text="В этот день всё свободно" />
      )}
      {!loading && (tasksByDay[picked] || []).map((t, i) => (
        <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
          <div style={{ fontSize: 13, color: s.text }}>{t}</div>
        </Glass>
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

  if (loading) return <Empty s={s} text="Загружаю..." />;
  if (error) {
    return (
      <Glass s={s} accent={s.red} style={{ padding: "14px 16px" }}>
        <div style={{ fontSize: 13, color: s.red, fontWeight: 500, marginBottom: 6 }}>
          Ошибка загрузки
        </div>
        <div style={{ fontSize: 12, color: s.tM, marginBottom: 10, wordBreak: "break-word" }}>
          {error.message}
        </div>
        <div
          onClick={refetch}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8,
            background: `${s.acc}22`, color: s.acc,
            fontSize: 12, cursor: "pointer",
          }}
        >
          <RefreshCw size={12} /> Повторить
        </div>
      </Glass>
    );
  }

  const a = adaptArcanaToday(data);
  const moon = a.moon;
  const total = a.sessionsToday.length + a.worksToday.length;
  const doneCount = Object.values(done).filter(Boolean).length;

  // wave6.5.5: лунный градиент по illum
  const illum = a.moon?.illum ?? 0;
  const moonGradient =
    illum < 10 ? "linear-gradient(180deg, #0a0e1e 0%, #1c2340 100%)" :
    illum < 40 ? "linear-gradient(180deg, #1a2340 0%, #2a3550 100%)" :
    illum < 60 ? "linear-gradient(180deg, #2a3550 0%, #3d4a6b 100%)" :
    illum < 90 ? "linear-gradient(180deg, #3d4a6b 0%, #5a6b8a 100%)" :
                 "linear-gradient(180deg, #5a6b8a 0%, #8a9cb8 100%)";

  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 8,
      // Добавляем оверлей фон на основе фазы луны — транспарент поверх основного
      position: "relative",
    }}>
      <div style={{
        position: "absolute", inset: -14, zIndex: -1,
        background: moonGradient, opacity: 0.22,
        pointerEvents: "none", transition: "background 2s ease",
      }} />
      {/* Hero с метриками */}
      <Glass s={s} glow>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>Мой день</span>
          <span style={{ fontSize: 11, color: s.tS }}>{a.date}</span>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate && navigate("sess")}>
            <Metric s={s} v={a.sessionsToday.length} sub="сеансов" />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate && navigate("stats")}>
            <Metric
              s={s}
              v={a.unchecked30d}
              sub="не провер."
              accent={a.unchecked30d > 0 ? s.amber : undefined}
            />
          </div>
          <div style={{ flex: 1, cursor: "pointer" }} onClick={() => navigate && navigate("stats")}>
            <Metric s={s} v={`${a.accuracy}%`} sub="точность" accent={s.acc} />
          </div>
        </div>
      </Glass>

      {/* Фаза луны — большой блок */}
      <Glass
        s={s}
        accent={s.acc}
        glow
        onClick={() => openMoonPhases && openMoonPhases()}
        style={{ cursor: "pointer" }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <div
            style={{
              fontSize: 54,
              lineHeight: 1,
              filter: "drop-shadow(0 0 10px rgba(255,255,255,0.3))",
            }}
          >
            {moon.glyph}
          </div>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontFamily: H,
                fontSize: 17,
                color: s.text,
                fontWeight: 500,
              }}
            >
              {moon.name}
            </div>
            <div style={{ fontSize: 11, color: s.tS, marginTop: 3 }}>
              {moon.days} день цикла · освещение {moon.illum}%
            </div>
            <div style={{ marginTop: 6 }}>
              <Bar s={s} pct={moon.illum} color={s.acc} />
            </div>
          </div>
        </div>
      </Glass>

      {/* Статистика за месяц (4 карточки) */}
      <SectionLabel s={s}>Статистика за {a.monthBlock.label}</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <Glass s={s} style={{ padding: "12px 10px", textAlign: "center" }}>
          <div style={{ fontSize: 18, marginBottom: 4 }}>💰</div>
          <div style={{ fontFamily: H, fontSize: 18, color: s.acc, fontWeight: 500 }}>
            {a.monthBlock.inc.toLocaleString()}<span style={{ fontSize: 13, marginLeft: 1 }}>₽</span>
          </div>
          <div style={{ fontSize: 10, color: s.tS, marginTop: 2 }}>Доход</div>
        </Glass>
        <Glass s={s} style={{ padding: "12px 10px", textAlign: "center" }}>
          <div style={{ fontSize: 18, marginBottom: 4 }}>🕯️</div>
          <div style={{ fontFamily: H, fontSize: 18, color: s.text, fontWeight: 500 }}>
            {a.monthBlock.supplies.toLocaleString()}<span style={{ fontSize: 13, marginLeft: 1 }}>₽</span>
          </div>
          <div style={{ fontSize: 10, color: s.tS, marginTop: 2 }}>Расходники</div>
        </Glass>
        <Glass s={s} style={{ padding: "12px 10px", textAlign: "center" }}>
          <div style={{ fontSize: 18, marginBottom: 4 }}>✨</div>
          <div style={{ fontFamily: H, fontSize: 18, color: s.acc, fontWeight: 500 }}>
            {a.monthBlock.accuracy}%
          </div>
          <div style={{ fontSize: 10, color: s.tS, marginTop: 2 }}>Сбылось</div>
        </Glass>
        <Glass s={s} style={{ padding: "12px 10px", textAlign: "center" }}>
          <div style={{ fontSize: 18, marginBottom: 4 }}>🃏</div>
          <div style={{ fontFamily: H, fontSize: 18, color: s.text, fontWeight: 500 }}>
            {a.monthBlock.sessions}
          </div>
          <div style={{ fontSize: 10, color: s.tS, marginTop: 2 }}>Сеансов</div>
        </Glass>
      </div>

      {/* Сеансы */}
      {a.sessionsToday.length > 0 && (
        <>
          <SectionLabel s={s}>Сеансы сегодня</SectionLabel>
          {a.sessionsToday.map((x) => {
            const cid = x.client_id;
            return (
              <Glass
                key={x.id}
                s={s}
                style={{ padding: "10px 14px", marginBottom: 6, display: "flex", gap: 10, alignItems: "center" }}
                onClick={() => cid && openClient({ id: cid })}
              >
                <span
                  style={{
                    fontFamily: "'SF Mono', Menlo, monospace",
                    fontSize: 12,
                    color: s.acc,
                    fontWeight: 500,
                    minWidth: 38,
                  }}
                >
                  {x.time}
                </span>
                <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: "50%",
                    background: `${s.acc}22`,
                    color: s.acc,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: H,
                    fontSize: 13,
                    fontWeight: 500,
                    flexShrink: 0,
                  }}
                >
                  {x.client[0]}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: s.text, fontWeight: 500 }}>{x.client}</div>
                  <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>
                    {x.type} · {x.area}
                  </div>
                </div>
                <ChevronRight size={16} color={s.tS} />
              </Glass>
            );
          })}
        </>
      )}

      {/* Работы практики */}
      {a.worksToday.length > 0 && (
        <>
          <SectionLabel s={s}>Работы</SectionLabel>
          {a.worksToday.map((w) => (
            <Glass
              key={w.id}
              s={s}
              style={{
                padding: "10px 14px",
                marginBottom: 6,
                display: "flex",
                gap: 10,
                alignItems: "center",
                opacity: done[w.id] ? 0.45 : 1,
              }}
            >
              <Chk
                s={s}
                done={done[w.id]}
                onClick={() => setDone((p) => ({ ...p, [w.id]: !p[w.id] }))}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    color: s.text,
                    textDecoration: done[w.id] ? "line-through" : "none",
                  }}
                >
                  {w.title}
                </div>
                <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>{w.cat}</div>
              </div>
              <PrioDot s={s} prio={w.prio} />
            </Glass>
          ))}
        </>
      )}

      {total === 0 && a.unchecked30d === 0 && <Empty s={s} text="Сегодня в практике спокойно 🌙" />}
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
        <span style={{ fontFamily: H, fontSize: 20, color: s.text }}>Расклады</span>
        {unchecked > 0 && (
          <span style={{ fontSize: 11, color: s.amber }}>⏳ {unchecked} непроверено</span>
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
        const cardsBrief = (x.cards || []).map((c) => c.name).slice(0, 3).join(", ") +
          (x.cards.length > 3 ? `, +${x.cards.length - 3}` : "");
        const doneGlyph = (x.done || "⏳").split(" ")[0];
        return (
          <Glass
            key={x.id}
            s={s}
            style={{ padding: "10px 14px", marginBottom: 4 }}
            onClick={() => openSession({ id: x.id })}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                  {x.q || "без темы"}
                </div>
                <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                  {[x.type, x.deck, x.client, x.date].filter(Boolean).join(" · ")}
                </div>
                {cardsBrief && (
                  <div
                    style={{
                      fontSize: 11,
                      color: s.tS,
                      marginTop: 3,
                      fontStyle: "italic",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    🃏 {cardsBrief}
                  </div>
                )}
              </div>
              <span style={{ fontSize: 14, flexShrink: 0 }}>{doneGlyph}</span>
            </div>
          </Glass>
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
        <span style={{ fontFamily: H, fontSize: 20, color: s.text }}>Клиенты</span>
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
                  fontSize: 14,
                  color: s.acc,
                  fontWeight: 500,
                  fontFamily: H,
                }}
              >
                {c.initial}
              </div>
              <div>
                <div style={{ fontSize: 13, color: s.text, fontWeight: 500 }}>
                  {c.status} {c.name}
                  {c.self && (
                    <span style={{ color: s.tS, fontWeight: 400, fontSize: 11 }}> · я</span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: s.tM }}>
                  {c.sessions} сеансов · {c.rituals} ритуалов
                </div>
              </div>
            </div>
            {c.debt > 0 && (
              <span style={{ fontSize: 13, color: s.red, fontWeight: 500 }}>
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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Ритуалы</div>
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
              <div style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                {r.name}
              </div>
              <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                {[r.goal, r.place, r.type, r.date].filter(Boolean).join(" · ")}
              </div>
            </div>
            <span style={{ fontSize: 16 }}>{r.result}</span>
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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Гримуар</div>
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
            <span style={{ fontSize: 13, color: s.text }}>{g.name}</span>
            <span style={{ fontSize: 13 }}>{g.theme}</span>
          </div>
          <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>{g.cat}</div>
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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Точность</div>

      {unchecked.length > 0 && (
        <Glass s={s} accent={s.amber} style={{ padding: "12px 14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "baseline" }}>
            <span style={{ fontSize: 13, color: s.text, fontWeight: 500 }}>❗ Ждут проверки</span>
            <span style={{ fontSize: 11, color: s.tS }}>{unchecked.length}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {unchecked.slice(0, 8).map((sess) => (
              <div key={sess.id} style={{ background: s.card, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ fontSize: 12, color: s.text, fontWeight: 500 }}>
                  {sess.question || sess.title || "Без темы"}
                </div>
                <div style={{ fontSize: 10, color: s.tM, marginBottom: 6 }}>
                  {sess.client || ""}{sess.date ? ` · ${formatDate(sess.date)}` : ""}
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                  <div onClick={() => doVerify(sess.id, "✅ Да")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.good + "33", color: s.good, fontSize: 11, cursor: "pointer" }}>
                    ✅ Сбылось
                  </div>
                  <div onClick={() => doVerify(sess.id, "〰️ Частично")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.amber + "33", color: s.amber, fontSize: 11, cursor: "pointer" }}>
                    〰️ Частично
                  </div>
                  <div onClick={() => doVerify(sess.id, "❌ Нет")}
                       style={{ flex: 1, textAlign: "center", padding: "4px", borderRadius: 6, background: s.red + "33", color: s.red, fontSize: 11, cursor: "pointer" }}>
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
        <div style={{ fontSize: 11, color: s.tS, marginBottom: 6 }}>
          Общий процент сбывшихся раскладов
        </div>
        <div
          style={{
            fontFamily: H,
            fontSize: 52,
            fontWeight: 600,
            color: s.acc,
            lineHeight: 1,
            letterSpacing: -1,
          }}
        >
          {pct}%
        </div>
        <div style={{ fontSize: 11, color: s.tM, marginTop: 6 }}>
          за всё время · {allVer} проверенных
        </div>
      </Glass>

      {/* Финансы практики */}
      <Glass s={s}>
        <div style={{ fontFamily: H, fontSize: 13, color: s.tS, marginBottom: 8 }}>
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
                style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}
              >
                {m.name}
              </span>
              <span style={{ fontSize: 16, color: s.acc, fontWeight: 600, fontFamily: H }}>
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
            <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: 11 }}>
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
        <Camera size={26} color={s.tM} style={{ margin: "0 auto 6px", display: "block" }} />
        <div style={{ fontSize: 11, color: s.tS }}>
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
        style={{ display: "flex", justifyContent: "space-between", cursor: "pointer", fontSize: 12, color: s.acc, fontWeight: 500 }}
      >
        <span>⚡ Короткая суть</span>
        <span>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div style={{ marginTop: 6, fontSize: 13, color: s.text, lineHeight: 1.5 }}>
          {summary ? (
            summary
          ) : (
            <div
              onClick={load}
              style={{
                display: "inline-block", padding: "4px 10px", borderRadius: 6,
                background: `${s.acc}22`, color: s.acc, cursor: busy ? "wait" : "pointer",
                fontSize: 12,
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
          fontSize: 32,
        }}>🃏</div>
      )}
      <div style={{ textAlign: "center", width: "100%" }}>
        <div style={{ fontSize: 11, color: s.text, fontWeight: 500, lineHeight: 1.2 }}>
          {card.en || card.raw || "—"}
        </div>
        {card.ru && (
          <div style={{ fontSize: 10, color: s.tM, lineHeight: 1.2 }}>
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
            fontSize: 20,
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
            fontSize: 12,
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
          <span style={{ color: s.text, fontSize: 11 }}>
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
            <span style={{ fontSize: 24, flexShrink: 0 }}>🂠</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 10, color: s.tS }}>Дно</div>
              <div style={{ fontSize: 14, color: s.text, fontWeight: 500 }}>
                {x.bottomCard.en || x.bottomCard.raw || "—"}
              </div>
              {x.bottomCard.ru && (
                <div style={{ fontSize: 11, color: s.tM }}>{x.bottomCard.ru}</div>
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
          style={{ fontSize: 13, color: s.text, lineHeight: 1.6 }}
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
              fontSize: 12,
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
            fontSize: 20,
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
            fontSize: 12,
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
              <span style={{ color: s.text, gridColumn: "2 / span 3", fontSize: 11 }}>
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
        <div style={{ fontSize: 13, color: s.text, fontWeight: 500, marginBottom: 8 }}>
          🕯️ Расходники
        </div>
        {r.supplies.map((x, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "4px 0",
              fontSize: 12,
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
            fontSize: 13,
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
          <Clock size={14} color={s.acc} />
          <span style={{ fontSize: 13, color: s.text, fontWeight: 500 }}>Время</span>
          <span style={{ fontSize: 13, color: s.tS, marginLeft: "auto" }}>{r.time} мин</span>
        </div>
      </Glass>

      {/* Подношения */}
      {r.offerings && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
          <div style={{ fontSize: 13, color: s.text, fontWeight: 500, marginBottom: 4 }}>
            🙏 Подношения / откуп
          </div>
          <div style={{ fontSize: 12, color: s.tS }}>{r.offerings}</div>
        </Glass>
      )}

      {/* Силы */}
      {r.powers && (
        <Glass s={s} style={{ padding: "10px 14px", marginBottom: 10 }}>
          <div style={{ fontSize: 13, color: s.text, fontWeight: 500, marginBottom: 4 }}>
            ⚡ Силы
          </div>
          <div style={{ fontSize: 12, color: s.tS }}>{r.powers}</div>
        </Glass>
      )}

      {/* Структура */}
      <Glass s={s} accent={s.acc} style={{ padding: "10px 14px" }}>
        <div style={{ fontSize: 13, color: s.text, fontWeight: 500, marginBottom: 8 }}>
          📕 Структура ритуала
        </div>
        {r.structure.map((step, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              gap: 8,
              padding: "4px 0",
              fontSize: 12,
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
            fontSize: 28,
            color: "#fff",
            fontFamily: H,
            fontWeight: 500,
            flexShrink: 0,
          }}
        >
          {c.initial}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: H, fontSize: 22, color: s.text, fontWeight: 500 }}>
            {c.status} {c.name}
            {c.self && (
              <span style={{ fontSize: 13, color: s.tS, fontWeight: 400 }}> · я</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: s.tS, marginTop: 3 }}>
            {c.contact} · с {c.since}
          </div>
          <div style={{ fontSize: 12, color: s.text, marginTop: 5 }}>
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
        <div style={{ fontSize: 10, color: s.tS, marginBottom: 4, display: "inline-flex", alignItems: "center", gap: 4 }}>
          <StickyNote size={11} /> Заметки
        </div>
        <div style={{ fontSize: 13, color: s.text, lineHeight: 1.55 }}>{c.notes}</div>
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
            fontSize: 12,
          }}
        >
          <span style={{ color: s.tM, minWidth: 50 }}>{h.date}</span>
          <span style={{ fontSize: 14 }}>{h.type}</span>
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
              <Ic size={20} color={s.acc} />
            </div>
            <span style={{ fontSize: 12, color: s.text, fontFamily: B }}>{a.label}</span>
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
  return (
    <div>
      <div style={{ fontFamily: H, fontSize: 18, color: s.text, marginBottom: 6 }}>
        {task.cat} {task.title}
      </div>
      <div style={{ fontSize: 12, color: s.tS, marginBottom: 14 }}>
        {task.date || task.time || task.rpt || "без даты"}
        {task.prio && ` · ${task.prio}`}
        {task.streak > 0 && ` · 🔥 ${task.streak}`}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <ActionRow
          s={s}
          icon={<Check size={16} />}
          label={busy === "done" ? "Сохраняю..." : "Сделано"}
          onClick={() => !busy && task.id && run("done", () => apiPost(`/api/tasks/${task.id}/done`))}
        />
        <ActionRow
          s={s}
          icon={<Calendar size={16} />}
          label={busy === "post" ? "Сохраняю..." : "Перенести на день"}
          onClick={() => !busy && task.id && run("post", () => apiPost(`/api/tasks/${task.id}/postpone`, { days: 1 }))}
        />
        <ActionRow
          s={s}
          icon={<Calendar size={16} />}
          label={busy === "post7" ? "Сохраняю..." : "Перенести на неделю"}
          onClick={() => !busy && task.id && run("post7", () => apiPost(`/api/tasks/${task.id}/postpone`, { days: 7 }))}
        />
        <ActionRow
          s={s}
          icon={<Trash2 size={16} />}
          label={busy === "cancel" ? "Сохраняю..." : "Отменить"}
          onClick={() => !busy && task.id && run("cancel", () => apiPost(`/api/tasks/${task.id}/cancel`))}
          destructive
        />
      </div>
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
        fontSize: 13,
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
        fontSize: 14,
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
      <div style={{ fontSize: 13, color: s.text, marginBottom: 8 }}>
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

  const catsForType = type === "income" ? INCOME_CATS : EXPENSE_CATS;

  // сбрасываем категорию при смене типа
  const changeType = (t) => {
    setType(t);
    if (t === "practice_income") {
      setCat(""); // для практики категория не нужна
    } else if (t === "income") {
      setCat(INCOME_CATS[0]);
    } else {
      setCat(EXPENSE_CATS[0]);
    }
  };

  const needsCat = type === "expense";
  const valid = parseFloat(amount) > 0 && (!needsCat || !!cat);

  const submitLabel = busy
    ? "Сохраняю..."
    : type === "expense"
      ? "Сохранить расход"
      : "Сохранить доход";

  const comingSoon = () => alert("Coming soon 🌱");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 11, color: s.tS }}>Тип</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {financeTypes.map((t) => (
          <Pill key={t.k} s={s} active={type === t.k} onClick={() => changeType(t.k)}>
            {t.label}
          </Pill>
        ))}
      </div>
      <div style={{ position: "relative" }}>
        <Input s={s} value={amount} onChange={setAmount} placeholder="Сумма, ₽" type="number" step="1" />
        <div onClick={comingSoon} style={{
          position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
          cursor: "pointer", opacity: 0.5, fontSize: 16,
        }}>🎤</div>
      </div>
      {type !== "practice_income" && (
        <>
          <div style={{ fontSize: 11, color: s.tS }}>Категория</div>
          <PillSelect s={s} value={cat} onChange={setCat} options={catsForType} />
        </>
      )}
      <div style={{ position: "relative" }}>
        <Input s={s} value={desc} onChange={setDesc} placeholder={
          type === "practice_income" ? "Клиент / расклад" : "Описание"
        } />
        <div onClick={comingSoon} style={{
          position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
          cursor: "pointer", opacity: 0.5, fontSize: 16,
        }}>📸</div>
      </div>
      <SubmitBtn
        s={s}
        disabled={!valid || busy}
        label={submitLabel}
        onClick={onSubmit(async () => {
          await apiPost("/api/finance", {
            type,
            amount: parseFloat(amount),
            cat: needsCat ? cat : (cat || null),
            desc,
            bot: (botType === "arcana" || type === "practice_income") ? "arcana" : "nexus",
          });
        })}
      />
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
          cursor: "pointer", opacity: 0.5, fontSize: 16,
        }}>🎤</div>
      </div>
      <div style={{ fontSize: 11, color: s.tS }}>Категория</div>
      {cats.length > 0 ? (
        <PillSelect s={s} value={cat} onChange={setCat} options={cats} />
      ) : (
        <Input s={s} value={cat} onChange={setCat} placeholder="🏠 Дом" />
      )}
      <div style={{ fontSize: 11, color: s.tS }}>Приоритет</div>
      <PillSelect s={s} value={prio} onChange={setPrio} options={PRIOS} />
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <Input s={s} value={date} onChange={setDate} placeholder="Дата" type="date" />
        </div>
        <div style={{ flex: 1 }}>
          <Input s={s} value={time} onChange={setTime} placeholder="чч:мм" type="time" />
        </div>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: s.text, cursor: "pointer" }}>
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

function NoteForm({ s, onSubmit, busy }) {
  const [text, setText] = useState("");
  const [cat, setCat] = useState("");
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
            fontSize: 13,
            outline: "none",
            width: "100%",
            resize: "vertical",
          }}
        />
        <div onClick={comingSoon} style={{
          position: "absolute", right: 10, top: 10,
          cursor: "pointer", opacity: 0.5, fontSize: 16,
        }}>🎤</div>
      </div>
      <div style={{ fontSize: 11, color: s.tS }}>Категория</div>
      <PillSelect
        s={s}
        value={cat}
        onChange={setCat}
        options={["🏡 Быт", "🐈 Коты", "👥 Люди", "⭐ Предпочтения", "🦋 СДВГ"]}
      />
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
      <div style={{ fontSize: 11, color: s.tS }}>Тип</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Pill s={s} active={type === "buy"} onClick={() => setType("buy")}>🛒 Покупки</Pill>
        <Pill s={s} active={type === "check"} onClick={() => setType("check")}>📋 Чеклист</Pill>
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
              cursor: "pointer", color: s.tS, fontSize: 18, padding: "0 6px",
            }}>×</div>
          )}
        </div>
      ))}
      {names.length < 20 && (
        <div onClick={addRow} style={{
          fontSize: 12, color: s.acc, cursor: "pointer", textAlign: "center", padding: "4px 0",
        }}>+ добавить ещё</div>
      )}

      {type !== "check" && (
        <>
          <div style={{ fontSize: 11, color: s.tS }}>Категория</div>
          <PillSelect
            s={s}
            value={cat}
            onChange={setCat}
            options={["🍜 Продукты", "🧴 Бытовая химия", "🐈 Коты", "💧 Уход", "📦 Прочее"]}
          />
        </>
      )}
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
      <div style={{ fontSize: 11, color: s.tS }}>Статус</div>
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
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600&display=swap');
        @keyframes tw { 0% { opacity: 0.15 } 100% { opacity: 0.7 } }
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
          onClick={() => go(!isN)}
          style={{
            padding: "5px 12px",
            borderRadius: 20,
            background: sky.card,
            border: `1px solid ${sky.brd}`,
            backdropFilter: "blur(10px)",
            cursor: "pointer",
            fontSize: 11,
            color: sky.tS,
            userSelect: "none",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {isDay ? (
            <>
              <Moon size={13} color={sky.tS} /> →
            </>
          ) : (
            <>
              ← <Sun size={13} color={sky.tS} />
            </>
          )}
        </div>
      </div>

      {/* BODY */}
      <div style={{ padding: "6px 14px 110px", position: "relative", zIndex: 2 }}>
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
          padding: "6px 10px 18px",
          display: "flex",
          justifyContent: "center",
          gap: 2,
          background: `linear-gradient(transparent, ${sky.base}ee 30%)`,
          backdropFilter: "blur(10px)",
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
                maxWidth: 74,
                textAlign: "center",
                padding: "7px 2px",
                borderRadius: 12,
                cursor: "pointer",
                background: active ? `${sky.acc}25` : "transparent",
                color: active ? sky.text : sky.tS,
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", justifyContent: "center" }}>
                <Ic
                  size={19}
                  color={active ? sky.text : sky.tS}
                  strokeWidth={active ? 2 : 1.6}
                />
              </div>
              <div
                style={{
                  fontSize: 9,
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
      <Sheet s={sky} open={modal?.type === "task"} onClose={() => setModal(null)} title="Задача">
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
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ fontSize: 42 }}>🔥</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: H, fontSize: 22, color: s.text, fontWeight: 500 }}>
              {data?.current || 0} дней
            </div>
            <div style={{ fontSize: 12, color: s.tM }}>
              Лучший: {data?.best || 0}
              {data?.last_activity_date ? ` · последний раз ${formatDate(data.last_activity_date)}` : ""}
            </div>
          </div>
        </div>
      </Glass>

      {weekDays.length > 0 && (
        <Glass s={s}>
          <div style={{ fontSize: 11, color: s.tS, marginBottom: 8 }}>Последние 7 дней</div>
          <div style={{ display: "flex", gap: 6, justifyContent: "space-between" }}>
            {weekDays.map((d, i) => (
              <div key={i} style={{ flex: 1, textAlign: "center" }}>
                <div style={{
                  aspectRatio: "1/1",
                  borderRadius: 8,
                  border: d.is_today ? `2px solid ${s.amber}` : `1px solid ${s.brd}`,
                  background: d.has_activity ? `${s.amber}44` : "transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 18,
                }}>
                  {d.has_activity ? "🔥" : ""}
                </div>
                <div style={{ fontSize: 10, color: s.tS, marginTop: 3 }}>{d.weekday}</div>
              </div>
            ))}
          </div>
        </Glass>
      )}

      <div style={{ fontSize: 11, color: s.tM, textAlign: "center" }}>
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
          <div style={{ fontSize: 64, filter: "drop-shadow(0 0 10px rgba(255,255,255,0.3))" }}>
            {current.glyph}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: H, fontSize: 22, color: s.text, fontWeight: 600 }}>
              {current.name}
            </div>
            <div style={{ fontSize: 14, color: s.text, opacity: 0.85, marginTop: 4 }}>
              освещённость {current.illum}% · {isRising ? "растущая" : "убывающая"}
            </div>
          </div>
        </div>
      </Glass>

      <div style={{ fontSize: 14, color: s.text, fontWeight: 600, marginBottom: 6 }}>Ближайшие фазы</div>
      {upcoming.map((p, i) => {
        const dt = p.date ? new Date(p.date) : null;
        const today = new Date();
        const daysAway = dt ? Math.round((dt - today) / (1000 * 60 * 60 * 24)) : null;
        return (
          <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ fontSize: 32 }}>{p.glyph}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 15, color: s.text, fontWeight: 500 }}>{p.name}</div>
                <div style={{ fontSize: 12, color: s.text, opacity: 0.75 }}>
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
  return (
    <>
      <div style={{ fontSize: 13, color: s.text, lineHeight: 1.6, marginBottom: 10 }}>
        {view.profile || "Профиль пока не сгенерирован."}
      </div>
      {view.records.length > 0 && (
        <>
          <SectionLabel s={s}>Записи</SectionLabel>
          {view.records.map((r) => (
            <Glass key={r.id} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
              <div style={{ fontSize: 13, color: s.text }}>{r.text}</div>
            </Glass>
          ))}
        </>
      )}
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
      <div style={{ fontFamily: H, fontSize: 20, color: s.text, marginBottom: 6 }}>{g.name}</div>
      <div style={{ fontSize: 11, color: s.tS, marginBottom: 12 }}>
        {g.cat}{g.themes.length > 0 ? ` · ${g.themes.join(", ")}` : ""}
      </div>
      <Glass s={s} accent={s.acc} style={{ padding: "12px 14px", marginBottom: 10 }}>
        <div style={{ fontSize: 13, color: s.text, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
          {g.content || "Текст пока не заполнен."}
        </div>
      </Glass>
      {g.source && (
        <div style={{ fontSize: 11, color: s.tS, fontStyle: "italic" }}>
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
      <span style={{ fontSize: 14 }}>{label}</span>
    </div>
  );
}
