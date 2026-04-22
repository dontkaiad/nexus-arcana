import { useState, useRef, useMemo } from "react";
import { useApi } from "./hooks/useApi";
import { adaptToday, adaptArcanaToday } from "./adapters";
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
const getOrb = (progress, isSun) => {
  const t = isSun ? progress : 1 - progress;
  const a = Math.PI * 0.15 + t * Math.PI * 0.7;
  return { x: (0.5 + 0.58 * Math.cos(a)) * 100, y: (0.92 - 0.82 * Math.sin(a)) * 100 };
};

function getSky(p) {
  let d, m, w, g, b, card, text, tS, tM, acc, brd, red, amber, good;
  red = lerpC("#bf5a4a", "#c45a5a", p);
  amber = lerpC("#c49a3c", "#c4a03c", p);
  good = lerpC("#6b8f71", "#5a9a78", p);
  if (p < 0.3) {
    const t = p / 0.3;
    d = lerpC("#4a7a78", "#3a6a72", t);
    m = lerpC("#5a8a7a", "#5a7a88", t);
    w = lerpC("#8ab4a0", "#c4a060", t);
    g = lerpC("#c4c898", "#d4884a", t);
    b = lerpC("#dce8dc", "#e8dcc8", t);
    text = "#2e2b24";
    tS = "#4a463e"; // контраст поднят (было #5a564e)
    tM = "#6e6a5e"; // контраст поднят (было #8a8578)
    acc = lerpC("#6b8f71", "#7a9068", t);
    brd = "rgba(160,154,142,0.35)";
    card = `rgba(255,253,248,${(0.68 + t * 0.08).toFixed(2)})`;
  } else if (p < 0.6) {
    const t = (p - 0.3) / 0.3;
    d = lerpC("#3a6a72", "#2a3a5a", t);
    m = lerpC("#5a7a88", "#3a4a6a", t);
    w = lerpC("#c4a060", "#c46040", t);
    g = lerpC("#d4884a", "#a04048", t);
    b = lerpC("#e8dcc8", "#2a2838", t);
    text = lerpC("#2e2b24", "#d4ccc0", t);
    tS = lerpC("#4a463e", "#b0a898", t);
    tM = lerpC("#6e6a5e", "#807868", t);
    acc = lerpC("#7a9068", "#5a8a80", t);
    brd = `rgba(${Math.round(lerp(160, 60, t))},${Math.round(lerp(154, 65, t))},${Math.round(
      lerp(142, 80, t)
    )},${lerp(0.35, 0.42, t).toFixed(2)})`;
    card = `rgba(${Math.round(lerp(255, 22, t))},${Math.round(lerp(253, 27, t))},${Math.round(
      lerp(248, 40, t)
    )},${lerp(0.7, 0.62, t).toFixed(2)})`;
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

const H = "'Playfair Display', Georgia, serif";
const B = "-apple-system, 'SF Pro Text', system-ui, sans-serif";

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
      background: active ? `${s.acc}30` : s.card,
      color: active ? s.acc : s.text,
      border: `1px solid ${active ? s.acc + "66" : s.brd}`,
      fontFamily: B,
      fontWeight: active ? 500 : 400,
      whiteSpace: "nowrap",
      transition: "all 0.2s",
      backdropFilter: "blur(10px)",
    }}
  >
    {children}
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
        fontSize: 18,
        fontWeight: 500,
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
        <span style={{ color: s.tM, fontSize: 13, fontWeight: 400, marginLeft: 2 }}>{unit}</span>
      )}
    </div>
    <div style={{ fontSize: 10, color: s.tS, marginTop: 1 }}>{sub}</div>
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
        fontSize: 12,
        color: s.tS,
        letterSpacing: 0.3,
      }}
    >
      {children}
    </span>
    {action}
  </div>
);

const Empty = ({ s, text }) => (
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

const FAB = ({ s, onClick }) => (
  <div
    onClick={onClick}
    style={{
      position: "absolute",
      bottom: 84,
      right: 16,
      width: 52,
      height: 52,
      borderRadius: 26,
      background: s.acc,
      color: "#fff",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      cursor: "pointer",
      zIndex: 9,
      boxShadow: `0 4px 16px ${s.acc}66`,
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
        position: "absolute",
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
          fontSize: 13,
          color: s.text,
          textDecoration: done ? "line-through" : "none",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {t.cat} {t.title}
      </div>
      <div
        style={{
          fontSize: 10,
          color: s.tM,
          marginTop: 2,
          display: "flex",
          gap: 6,
          alignItems: "center",
        }}
      >
        {t.rem && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            <Bell size={9} /> {t.rem}
          </span>
        )}
        {t.rpt && <span>{t.rpt}</span>}
        {t.date && <span>{t.date}</span>}
        {t.streak > 0 && <span>🔥 {t.streak}</span>}
      </div>
    </div>
    <PrioDot s={s} prio={t.prio} />
  </Glass>
);

// ═══════════════════════════════════════════════════════════════
// NEXUS — MY DAY
// ═══════════════════════════════════════════════════════════════

function NxDay({ s, openTask }) {
  const [done, setDone] = useState({});
  const { data, loading, error, refetch } = useApi('/api/today');

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
  const total = t.scheduled.length + t.tasks.length;
  const leftPct = Math.round((t.spentDay / t.budgetDay) * 100);
  const toggle = (id) => setDone((p) => ({ ...p, [id]: !p[id] }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Glass s={s} glow>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>Мой день</span>
          <span style={{ fontSize: 11, color: s.tS }}>{t.date}</span>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <Metric s={s} v={`${doneCount}`} unit={`/${total}`} sub="задачи" />
          <Metric s={s} v={`${Math.round((t.budgetDay - t.spentDay) / 1000)}к`} unit="₽" sub="свободно" />
          <Metric
            s={s}
            v={t.streak}
            sub="стрик"
            accent={s.amber}
            icon={<LucideFlame size={14} color={s.amber} fill={s.amber} style={{ opacity: 0.9 }} />}
          />
        </div>
        <div style={{ marginTop: 11, paddingTop: 10, borderTop: `1px solid ${s.brd}` }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 11,
              color: s.tS,
              marginBottom: 4,
            }}
          >
            <span>Бюджет дня</span>
            <span
              style={{
                color: leftPct > 85 ? s.red : leftPct > 60 ? s.amber : s.acc,
                fontWeight: 500,
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
          <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
            потрачено {t.spentDay.toLocaleString()} ₽ из {t.budgetDay.toLocaleString()} ₽
          </div>
        </div>
      </Glass>

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
          <SectionLabel s={s}>Задачи</SectionLabel>
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

      {total === 0 && <Empty s={s} text="На сегодня пусто — отдыхай 🌿" />}

      <Glass s={s} accent={s.acc} style={{ marginTop: 4 }}>
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
          <RefreshCw size={13} color={s.tS} style={{ cursor: "pointer" }} />
        </div>
        <div style={{ fontSize: 12, color: s.text, lineHeight: 1.5 }}>{t.adhdTip}</div>
      </Glass>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — TASKS
// ═══════════════════════════════════════════════════════════════

function NxTasks({ s, openTask }) {
  const [f, setF] = useState("active");
  const list = f === "all" ? MOCK.tasks : MOCK.tasks.filter((t) => t.status === f);

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
      {list.length === 0 && <Empty s={s} text="Нет задач в этой категории" />}
      {list.map((t) => (
        <Glass
          key={t.id}
          s={s}
          accent={t.status === "overdue" ? s.red : undefined}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openTask(t)}
        >
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span
              style={{
                fontSize: 13,
                color: s.text,
                textDecoration: t.status === "done" ? "line-through" : "none",
                opacity: t.status === "done" ? 0.55 : 1,
              }}
            >
              {t.cat} {t.title}
            </span>
            <span style={{ fontSize: 12 }}>{t.prio}</span>
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
  const f = MOCK.finance;
  const balance = f.month.inc - f.month.exp;
  const sortedCats = [...f.cats].sort((a, b) => b.spent / b.limit - a.spent / a.limit);
  const todayTotal = f.today.reduce((a, x) => a + x.amt, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Финансы</div>
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

      {tab === "today" && (
        <>
          <Glass s={s}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={{ fontSize: 12, color: s.tS }}>Потрачено сегодня</span>
              <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>
                {todayTotal.toLocaleString()} ₽
              </span>
            </div>
          </Glass>
          {f.today.map((x, i) => (
            <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 13, color: s.text }}>{x.desc}</div>
                  <div style={{ fontSize: 10, color: s.tM, marginTop: 2 }}>{x.cat}</div>
                </div>
                <span style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                  {x.amt.toLocaleString()} ₽
                </span>
              </div>
            </Glass>
          ))}
        </>
      )}

      {tab === "month" && (
        <>
          <Glass s={s} glow>
            <div style={{ fontSize: 11, color: s.tS, marginBottom: 4 }}>Апрель 2026</div>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: s.tM }}>Доход</div>
                <div style={{ fontFamily: H, fontSize: 18, color: s.acc, fontWeight: 500 }}>
                  {f.month.inc.toLocaleString()} ₽
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: s.tM }}>Расход</div>
                <div style={{ fontFamily: H, fontSize: 18, color: s.text, fontWeight: 500 }}>
                  {f.month.exp.toLocaleString()} ₽
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, color: s.tM }}>Баланс</div>
                <div
                  style={{
                    fontFamily: H,
                    fontSize: 18,
                    color: balance >= 0 ? s.acc : s.red,
                    fontWeight: 500,
                  }}
                >
                  {balance >= 0 ? "+" : ""}
                  {balance.toLocaleString()} ₽
                </div>
              </div>
            </div>
          </Glass>
          <SectionLabel s={s}>По категориям</SectionLabel>
          {sortedCats.map((c, i) => {
            const pct = Math.round((c.spent / c.limit) * 100);
            const clr = pct > 85 ? s.red : pct > 60 ? s.amber : s.acc;
            return (
              <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 12,
                    color: s.text,
                    marginBottom: 4,
                  }}
                >
                  <span>{c.name}</span>
                  <span style={{ color: clr, fontWeight: 500 }}>
                    {c.spent.toLocaleString()} ₽
                  </span>
                </div>
                <Bar s={s} pct={pct} color={clr} />
              </Glass>
            );
          })}
        </>
      )}

      {tab === "limits" && (
        <>
          {sortedCats.map((c, i) => {
            const pct = Math.round((c.spent / c.limit) * 100);
            const clr = pct > 85 ? s.red : pct > 60 ? s.amber : s.acc;
            return (
              <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 12,
                    color: s.text,
                    marginBottom: 4,
                  }}
                >
                  <span>{c.name}</span>
                  <span style={{ color: clr, fontWeight: 500 }}>{pct}%</span>
                </div>
                <Bar s={s} pct={pct} color={clr} />
                <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                  {c.spent.toLocaleString()} ₽ / {c.limit.toLocaleString()} ₽
                </div>
              </Glass>
            );
          })}
        </>
      )}

      {tab === "goals" && (
        <>
          <SectionLabel s={s}>Долги</SectionLabel>
          {f.debts.map((d, i) => (
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
                до {d.by} · {d.note}
              </div>
              <div style={{ marginTop: 6 }}>
                <Bar s={s} pct={(1 - d.left / d.total) * 100} color={s.amber} />
              </div>
            </Glass>
          ))}
          <SectionLabel s={s}>Цели</SectionLabel>
          {f.goals.map((g, i) => (
            <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontSize: 14, color: s.text, fontFamily: H }}>{g.n}</span>
                <span style={{ fontSize: 13, color: s.acc, fontWeight: 500 }}>
                  {g.t.toLocaleString()} ₽
                </span>
              </div>
              <div style={{ fontSize: 11, color: s.tM, marginTop: 2 }}>после {g.after}</div>
              <div style={{ marginTop: 6 }}>
                <Bar s={s} pct={(g.s / g.t) * 100} color={s.acc} />
              </div>
            </Glass>
          ))}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// NEXUS — LISTS
// ═══════════════════════════════════════════════════════════════

function NxLists({ s }) {
  const [tab, setTab] = useState("buy");
  const [q, setQ] = useState("");
  const data =
    tab === "buy" ? MOCK.lists.buy : tab === "inv" ? MOCK.lists.inv : MOCK.lists.check;
  const filtered = data.filter((x) => x.name.toLowerCase().includes(q.toLowerCase()));

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
      {filtered.length === 0 && (
        <Empty s={s} text={tab === "check" ? "Нет активных чеклистов" : "Пусто"} />
      )}
      {tab === "buy" &&
        filtered.map((x, i) => (
          <Glass
            key={i}
            s={s}
            style={{ padding: "8px 14px", marginBottom: 4, opacity: x.done ? 0.5 : 1 }}
          >
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <Chk s={s} done={x.done} />
              <span style={{ fontSize: 13, color: s.text }}>
                {x.cat} {x.name}
              </span>
            </div>
          </Glass>
        ))}
      {tab === "inv" &&
        filtered.map((x, i) => (
          <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 13, color: s.text }}>{x.name}</span>
              <span style={{ fontSize: 13, color: s.acc, fontWeight: 500 }}>{x.qty} шт</span>
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
  const cats = ["all", ...new Set(MOCK.memory.map((m) => m.cat))];
  const filtered = MOCK.memory.filter(
    (m) => (cat === "all" || m.cat === cat) && m.text.toLowerCase().includes(q.toLowerCase())
  );

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
      {filtered.length === 0 && <Empty s={s} text="Ничего не найдено" />}
      {filtered.map((m, i) => (
        <Glass key={i} s={s} style={{ padding: "8px 14px", marginBottom: 4 }}>
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

function NxCal({ s }) {
  const [view, setView] = useState("month");
  const [picked, setPicked] = useState(22);
  const daysShort = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  // апрель 2026 начинается со среды
  const monthStart = 3; // Пн=0 ... Ср=2
  const daysInMonth = 30;
  const weeks = [];
  let cur = [];
  for (let i = 0; i < monthStart - 1; i++) cur.push(null);
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
  const tasksByDay = { 22: ["🐾 Лоток", "💻 Интернет"], 21: ["🐾 Лоток"], 23: ["🐾 Лоток"], 27: ["🏠 Уборка"], 15: ["👥 Нотариус"] };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span style={{ fontFamily: H, fontSize: 20, color: s.text }}>Апрель 2026</span>
        <div style={{ display: "flex", gap: 6 }}>
          <Pill s={s} active={view === "week"} onClick={() => setView("week")}>
            Неделя
          </Pill>
          <Pill s={s} active={view === "month"} onClick={() => setView("month")}>
            Месяц
          </Pill>
        </div>
      </div>
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
              const isToday = d === 22;
              const isPicked = d === picked;
              const has = tasksByDay[d];
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
                    minHeight: 34,
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
                  {has && (
                    <div
                      style={{
                        width: 4,
                        height: 4,
                        borderRadius: 2,
                        background: s.acc,
                        margin: "3px auto 0",
                      }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </Glass>
      <SectionLabel s={s}>{picked} апреля</SectionLabel>
      {!tasksByDay[picked] && <Empty s={s} text="Нет задач в этот день" />}
      {(tasksByDay[picked] || []).map((t, i) => (
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

function ArDay({ s, openClient }) {
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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Hero с метриками */}
      <Glass s={s} glow>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontFamily: H, fontSize: 22, color: s.text }}>Мой день</span>
          <span style={{ fontSize: 11, color: s.tS }}>{a.date}</span>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <Metric s={s} v={a.sessionsToday.length} sub="сеансов" />
          <Metric
            s={s}
            v={a.unchecked30d}
            sub="не провер."
            accent={a.unchecked30d > 0 ? s.amber : undefined}
          />
          <Metric s={s} v={`${a.accuracy}%`} sub="точность" accent={s.acc} />
        </div>
      </Glass>

      {/* Фаза луны — большой блок */}
      <Glass s={s} accent={s.acc} glow>
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
            const client = MOCK.clients.find((c) => c.name === x.client);
            return (
              <Glass
                key={x.id}
                s={s}
                style={{ padding: "10px 14px", marginBottom: 6, display: "flex", gap: 10, alignItems: "center" }}
                onClick={() => client && openClient(client)}
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
  const areas = ["all", ...new Set(MOCK.sessions.map((x) => x.area))];
  const list = f === "all" ? MOCK.sessions : MOCK.sessions.filter((x) => x.area === f);
  const unchecked = MOCK.sessions.filter((x) => x.done.startsWith("⏳")).length;

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
      {list.map((x) => {
        const cardsBrief = x.cards.map((c) => c.name).slice(0, 3).join(", ") +
          (x.cards.length > 3 ? `, +${x.cards.length - 3}` : "");
        const doneGlyph = x.done.split(" ")[0];
        return (
          <Glass
            key={x.id}
            s={s}
            style={{ padding: "10px 14px", marginBottom: 4 }}
            onClick={() => openSession(x)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                  {x.q}
                </div>
                <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                  {x.type} · {x.deck} · {x.client} · {x.date}
                </div>
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
  const total = MOCK.clients.length;
  const debt = MOCK.clients.reduce((a, c) => a + c.debt, 0);

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
      {MOCK.clients.map((c) => (
        <Glass
          key={c.id}
          s={s}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openClient(c)}
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
  const goals = ["all", ...new Set(MOCK.rituals.map((r) => r.goal))];
  const list = goal === "all" ? MOCK.rituals : MOCK.rituals.filter((r) => r.goal === goal);

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
      {list.map((r) => (
        <Glass
          key={r.id}
          s={s}
          style={{ padding: "10px 14px", marginBottom: 4 }}
          onClick={() => openRitual(r)}
        >
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <div>
              <div style={{ fontSize: 14, color: s.text, fontWeight: 500, fontFamily: H }}>
                {r.name}
              </div>
              <div style={{ fontSize: 10, color: s.tM, marginTop: 3 }}>
                {r.goal} · {r.place} · {r.type} · {r.date}
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

function ArGrimoire({ s }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const cats = ["all", ...new Set(MOCK.grimoire.map((g) => g.cat))];
  const filtered = MOCK.grimoire.filter(
    (g) => (cat === "all" || g.cat === cat) && g.name.toLowerCase().includes(q.toLowerCase())
  );

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
      {filtered.map((g, i) => (
        <Glass key={i} s={s} style={{ padding: "10px 14px", marginBottom: 4 }}>
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
  const months = MOCK.stats.months;
  const allVer = months.reduce((a, m) => a + m.yes + m.partial + m.no, 0);
  const allYes = months.reduce((a, m) => a + m.yes, 0);
  const pct = allVer > 0 ? Math.round((allYes / allVer) * 100) : 0;
  const p = MOCK.stats.practice;
  const profit = p.inc - p.exp;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontFamily: H, fontSize: 20, color: s.text }}>Точность</div>

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

function SessionDetail({ s, x }) {
  if (!x) return null;
  const doneGlyph = x.done.split(" ")[0];
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

      {/* Заглушка фото */}
      <Glass
        s={s}
        style={{
          padding: "22px 14px",
          marginBottom: 12,
          textAlign: "center",
          border: `1.5px dashed ${s.brd}`,
        }}
      >
        <Camera size={26} color={s.tM} style={{ margin: "0 auto 6px", display: "block" }} />
        <div style={{ fontSize: 11, color: s.tS }}>Фото расклада — бот загрузит через Cloudinary</div>
      </Glass>

      {/* Карты по позициям (grid 2-col) */}
      <SectionLabel s={s}>Карты в раскладе</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        {x.cards.map((c, i) => (
          <Glass key={i} s={s} style={{ padding: "10px 12px" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <span style={{ fontSize: 18, flexShrink: 0 }}>{c.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 10, color: s.tS }}>{c.pos}</div>
                <div
                  style={{
                    fontSize: 12,
                    color: s.text,
                    fontWeight: 500,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {c.name}
                </div>
              </div>
            </div>
          </Glass>
        ))}
      </div>

      {/* Дно колоды */}
      {x.bottom && (
        <Glass s={s} accent={s.acc} style={{ marginTop: 8, padding: "10px 12px" }}>
          <div style={{ fontSize: 10, color: s.tS, marginBottom: 2 }}>🂠 Дно колоды</div>
          <div style={{ fontSize: 13, color: s.text, fontWeight: 500 }}>
            {x.bottom.icon} {x.bottom.name}
          </div>
        </Glass>
      )}

      {/* Трактовка */}
      <SectionLabel s={s}>Трактовка</SectionLabel>
      <Glass s={s} accent={s.acc} style={{ padding: "12px 14px" }}>
        <div style={{ fontSize: 13, color: s.text, lineHeight: 1.6 }}>{x.interp}</div>
      </Glass>

      {/* Кнопки статуса */}
      <SectionLabel s={s}>Статус сбылось</SectionLabel>
      <div style={{ display: "flex", gap: 6 }}>
        {[
          { label: "✓ Сбылось", c: "#22c55e" },
          { label: "~ Частично", c: "#f59e0b" },
          { label: "✗ Нет", c: "#ef4444" },
        ].map((b, i) => (
          <div
            key={i}
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
              cursor: "pointer",
              backdropFilter: "blur(10px)",
            }}
          >
            {b.label}
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// RITUAL DETAIL SHEET
// ═══════════════════════════════════════════════════════════════

function RitualDetail({ s, r }) {
  if (!r) return null;
  const suppliesTotal = r.supplies.reduce((a, x) => a + x.price, 0);
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

      {/* Кнопки результата */}
      <SectionLabel s={s}>Результат</SectionLabel>
      <div style={{ display: "flex", gap: 6 }}>
        {[
          { label: "✓ Сработал", c: "#22c55e" },
          { label: "~ Частично", c: "#f59e0b" },
          { label: "✗ Нет", c: "#ef4444" },
        ].map((b, i) => (
          <div
            key={i}
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
              cursor: "pointer",
              backdropFilter: "blur(10px)",
            }}
          >
            {b.label}
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// CLIENT DETAIL SHEET (из v2)
// ═══════════════════════════════════════════════════════════════

function ClientDetail({ s, c }) {
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
  { key: "expense", icon: Wallet, label: "Расход" },
  { key: "task", icon: Check, label: "Задача" },
  { key: "note", icon: StickyNote, label: "Заметка" },
  { key: "list", icon: ListIcon, label: "В список" },
  { key: "memory", icon: Brain, label: "В память" },
  { key: "photo", icon: Camera, label: "Фото чека" },
  { key: "voice", icon: Mic, label: "Голосом" },
];

const ARCANA_ADD = [
  { key: "client", icon: Users, label: "Клиент" },
  { key: "session", icon: Sparkles, label: "Расклад" },
  { key: "ritual", icon: Flame, label: "Ритуал" },
  { key: "work", icon: Check, label: "Работа" },
  { key: "grimoire", icon: BookOpen, label: "В гримуар" },
  { key: "photo", icon: Camera, label: "Фото расклада" },
  { key: "voice", icon: Mic, label: "Голосом" },
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

  const shared = { s: sky, openTask, openAdhd, openClient, openSession, openRitual };
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
          <img
            src={isDay ? "/nexus.png" : "/arcana.png"}
            alt={isDay ? "Nexus" : "Arcana"}
            style={{
              width: 28,
              height: 28,
              borderRadius: "50%",
              objectFit: "cover",
            }}
          />
          <div
            style={{
              fontFamily: H,
              fontSize: 18,
              color: sky.text,
            }}
          >
            {isDay ? "Nexus" : "Arcana"}
          </div>
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

      {/* FAB */}
      <FAB s={sky} onClick={() => setFabOpen(true)} />

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
                color: active ? sky.acc : sky.tS,
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", justifyContent: "center" }}>
                <Ic
                  size={19}
                  color={active ? sky.acc : sky.tS}
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
          <div>
            <div style={{ fontFamily: H, fontSize: 18, color: sky.text, marginBottom: 6 }}>
              {modal.payload.cat} {modal.payload.title}
            </div>
            <div style={{ fontSize: 12, color: sky.tS, marginBottom: 14 }}>
              {modal.payload.date || modal.payload.time || modal.payload.rpt || "без даты"}
              {modal.payload.prio && ` · ${modal.payload.prio}`}
              {modal.payload.streak > 0 && ` · 🔥 ${modal.payload.streak}`}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <ActionRow s={sky} icon={<Check size={16} />} label="Сделано" onClick={() => setModal(null)} />
              <ActionRow s={sky} icon={<Pencil size={16} />} label="Редактировать" onClick={() => setModal(null)} />
              <ActionRow s={sky} icon={<Calendar size={16} />} label="Перенести" onClick={() => setModal(null)} />
              <ActionRow s={sky} icon={<ListIcon size={16} />} label="Разбить на подзадачи" onClick={() => setModal(null)} />
              <ActionRow
                s={sky}
                icon={<Trash2 size={16} />}
                label="Отменить"
                onClick={() => setModal(null)}
                destructive
              />
            </div>
          </div>
        )}
      </Sheet>

      <Sheet s={sky} open={modal?.type === "adhd"} onClose={() => setModal(null)} title="🦋 СДВГ-профиль">
        <div style={{ fontSize: 13, color: sky.text, lineHeight: 1.6, marginBottom: 10 }}>
          {MOCK.adhdProfile}
        </div>
        <SectionLabel s={sky}>Записи</SectionLabel>
        {["Работает техника 2 минут", "Гиперфокус вечером", "Утром трудный старт"].map((t, i) => (
          <Glass key={i} s={sky} style={{ padding: "8px 14px", marginBottom: 4 }}>
            <div style={{ fontSize: 13, color: sky.text }}>{t}</div>
          </Glass>
        ))}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "client"}
        onClose={() => setModal(null)}
        title={`Клиент: ${modal?.payload?.name || ""}`}
      >
        {modal?.payload && <ClientDetail s={sky} c={modal.payload} />}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "session"}
        onClose={() => setModal(null)}
        title="Расклад"
      >
        {modal?.payload && <SessionDetail s={sky} x={modal.payload} />}
      </Sheet>

      <Sheet
        s={sky}
        open={modal?.type === "ritual"}
        onClose={() => setModal(null)}
        title="Ритуал"
      >
        {modal?.payload && <RitualDetail s={sky} r={modal.payload} />}
      </Sheet>

      <Sheet s={sky} open={fabOpen} onClose={() => setFabOpen(false)} title="Добавить">
        <QuickAdd
          s={sky}
          actions={isDay ? NEXUS_ADD : ARCANA_ADD}
          onPick={() => setFabOpen(false)}
        />
      </Sheet>
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
