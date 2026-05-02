// Mock data for Nexus × Arcana redesign

window.NX_DATA = {
  today: {
    date: "1 мая, пт",
    streak: 8,
    budgetDay: 4166,
    spentDay: 1240,
    weather: { kind: "rain", temp: 5, city: "СПб", condition: "Лёгкий дождь" },
    overdue: [
      { id: 1, title: "кинуть 5 долларов на OpenAI API", cat: "💻", prio: "high", days: 29 },
      { id: 2, title: "сделать генеральную уборку кухни", cat: "🏠", prio: "high", days: 6 },
    ],
    scheduled: [
      { id: 3, title: "менять лоток котам", cat: "🐾", prio: "medium", time: "16:00", rpt: "каждые 2 дня", streak: 12 },
      { id: 4, title: "позвонить маме", cat: "👥", prio: "medium", time: "19:30" },
    ],
    adhdTip: "Выбери **одну задачу** прямо сейчас и поставь таймер на 25 минут. Остальное подождёт.",
  },
  tasks: [
    { id: 101, title: "позвонить нотариусу", cat: "👥", prio: "high", date: "1 июня", status: "active" },
    { id: 102, title: "разобраться с тараканами", cat: "🏠", prio: "high", status: "active" },
    { id: 103, title: "сводить луну к ветеринару", cat: "🐾", prio: "high", status: "active" },
    { id: 104, title: "выставить вещи на продажу", cat: "💳", prio: "medium", date: "1 июня", status: "active" },
    { id: 105, title: "переделать свой гитхаб", cat: "💻", prio: "medium", status: "active" },
    { id: 106, title: "пополнить Claude API", cat: "💻", prio: "medium", status: "active" },
    { id: 107, title: "разобрать гардероб", cat: "👗", prio: "low", date: "15 мая", status: "active" },
    { id: 108, title: "менять лоток котам", cat: "🐾", prio: "low", rpt: "каждый день", status: "active" },
  ],
  finance: {
    today: { spent: 0, budget: 4166 },
    transactions: [],
    cats: [
      { name: "Привычки", spent: 14200, limit: 17685, emoji: "🚬" },
      { name: "Продукты", spent: 6700, limit: 8000, emoji: "🍜" },
      { name: "Кафе", spent: 4100, limit: 5000, emoji: "🍱" },
      { name: "Транспорт", spent: 900, limit: 2000, emoji: "🚕" },
    ],
  },
  lists: {
    health: [{ id: 201, name: "компрессионные чулки medi", emoji: "💊", done: false }],
    misc: [{ id: 202, name: "нитки", emoji: "🧵", done: true }],
    hobby: [
      { id: 203, name: "паутинка по ткани", emoji: "🧵", done: true },
      { id: 204, name: "зажимы по ткани", emoji: "🧵", done: false },
      { id: 205, name: "нитки для обуви хаки", emoji: "🧵", done: false },
      { id: 206, name: "иглы для швейной машинки", emoji: "🧵", done: false },
    ],
  },
  memory: [
    { text: "крупы можно хранить только в пэт-бутылках", cat: "🏠 Быт" },
    { text: "курит только сигареты марки Chapman Green", cat: "🛒 Предпочтения" },
    { text: "кола, черноголовка, cola, пепси — привычки (газированные напитки)", cat: "🛒 Предпочтения" },
    { text: "монстры/monster/монстр = энергетики, категория привычки", cat: "🛒 Предпочтения" },
    { text: "луне нужен габапентин (противотревожное)", cat: "🐾 Коты" },
    { text: "любит Вадима", cat: "👥 Люди" },
    { text: "у алуны краткая кличка луна", cat: "🐾 Коты" },
    { text: "в Санкт-Петербурге", cat: "🏠 Быт" },
  ],
  // arcana
  arcanaToday: {
    date: "1 мая, пт",
    sessionsToday: [],
    unchecked30d: 0,
    accuracy: 0,
    moon: { glyph: "🌕", name: "Полнолуние", days: 14, illum: 99 },
    monthBlock: { inc: 0, supplies: 0, accuracy: 0, sessions: 0, label: "Май" },
  },
  sessions: [
    { id: 301, q: "Общий расклад", area: "Общая ситуация", deck: "Уэйт", type: "🔺 Триплет", client: "ТестСмоук", date: "21 апреля", cards: [{ name: "The Fool" }, { name: "The Magician" }, { name: "The High Priestess" }], status: "unchecked" },
    { id: 302, q: "Что думает Вадим", area: "Отношения", deck: "Уэйт", type: "🔺 Триплет", client: "Личный", date: "21 апреля", cards: [{ name: "The Fool" }, { name: "The Magician" }, { name: "The High Priestess" }], status: "unchecked" },
    { id: 303, q: "Что думает Вадим", area: "Отношения", deck: "Уэйт", type: "🔺 Триплет", client: "Личный", date: "21 апреля", cards: [{ name: "The Fool" }, { name: "The Magician" }, { name: "The High Priestess" }], status: "unchecked" },
    { id: 304, q: "общий вопрос", area: "Общая ситуация", deck: "Уэйт", type: "🔺 Триплет", client: "Личный", date: "21 апреля", cards: [{ name: "The Fool" }, { name: "The Magician" }, { name: "The High Priestess" }], status: "unchecked" },
  ],
  clients: [
    { id: 401, name: "Оля", initial: "О", sessions: 0, rituals: 0, status: "active" },
    { id: 402, name: "ТестСмоук", initial: "Т", sessions: 1, rituals: 0, status: "active" },
  ],
  rituals: [
    { id: 501, name: "Очищение дома", goal: "🌊 Очищение", place: "🏠 Дома", type: "🌟 Личный", date: "21 апреля" },
    { id: 502, name: "Очищение дома", goal: "🌊 Очищение", place: "🏠 Дома", type: "🌟 Личный", date: "21 апреля" },
  ],
  stats: {
    pct: 0,
    allVer: 0,
    practice: { inc: 0, exp: 0 },
    months: [
      { name: "Май", yes: 0, partial: 0, no: 0, total: 0 },
      { name: "Апрель", yes: 0, partial: 0, no: 0, total: 0 },
    ],
    unchecked: [
      { id: 301, q: "Общий расклад", client: "ТестСмоук", date: "21 апреля" },
      { id: 302, q: "Что думает Вадим", client: "Личный", date: "21 апреля" },
    ],
  },
};
