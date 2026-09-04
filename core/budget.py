"""core/budget.py — парсинг бюджета из Памяти.

Общий слой для Nexus-хендлера /budget и Mini App /api/finance.
Держит в одном месте: regex-парсеры, константы, публичные функции загрузки.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone as _tz
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Key prefix ↔ Notion category ─────────────────────────────────────────────

BUDGET_KEY_TO_CATEGORY: Dict[str, str] = {
    "income_": "📥 Доход",
    "постоянно_": "🔒 Постоянные",
    "лимит_": "💰 Лимит",
    "цель_": "🎯 Цели",
    "долг_": "📋 Долги",
}
BUDGET_ALL_CATEGORIES: List[str] = list(BUDGET_KEY_TO_CATEGORY.values())


# ── Display mapping для лимитов ──────────────────────────────────────────────

LIMIT_DISPLAY: Dict[str, str] = {
    "привычки": "🚬 Привычки",
    "продукты": "🍜 Продукты",
    "кафе": "🍱 Кафе/Доставка",
    "транспорт": "🚕 Транспорт",
    "бьюти": "💅 Бьюти",
    "гардероб": "👗 Гардероб",
    "здоровье": "🏥 Здоровье",
    "хобби": "📚 Хобби/Учеба",
    "импульсивные": "🎲 Импульсивные",
    "импульсивный": "🎲 Импульсивные",
    "подушка": "🛡️ Подушка",
    "расходники": "🕯️ Расходники",
    # Постоянные и разовые расходы как обычные категории лимита. Размер задаётся
    # не compute_limits(), а прямой суммой позиций при ✅ Принять
    # (_save_budget_plan → лимит_фикс / лимит_разовые). В _BUDGET_VARIABLE_CATS
    # НЕ входят — это параллельные счётчики, не участвуют в дележе остатка.
    "фикс": "🔒 Фикс",
    "разовые": "📦 Разовые",
}


# ── Regex ────────────────────────────────────────────────────────────────────

LIMIT_AMOUNT_RE = re.compile(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]')
LIMIT_FACT_RE = re.compile(
    r'лимит[:\s]+([^—\-\d]+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE | re.UNICODE,
)
INCOME_RE = re.compile(
    r'доход:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
PERMANENT_RE = re.compile(
    r'постоянно:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
# Разовая позиция плана (факт вида "разовое: коммуналка Гай — 16000₽").
# Хранится индивидуально в Памяти (ключ разовый_*) только для сопоставления
# текста будущих трат — не для диалога.
ONE_TIME_FACT_RE = re.compile(
    r'разов[оа][ея]:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
GOAL_RE = re.compile(
    r'цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?',
    re.IGNORECASE,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_amount(s: str) -> float:
    """'15 000' / '15,5' / '15.5' → float. Без ₽ и пробелов."""
    return float(s.replace(' ', '').replace(',', '.'))


def cat_link(cat: str) -> str:
    """'🚬 Привычки' → 'привычки', '🍱 Кафе/Доставка' → 'кафе'."""
    name = re.sub(r'^[^\w\u0400-\u04FF]+', '', cat, flags=re.UNICODE).strip()
    return name.split('/')[0].strip().lower()


def display_limit_name(raw_name: str) -> str:
    """'привычки' / 'лимит_привычки' → '🚬 Привычки'. Fallback — raw_name."""
    key = raw_name.lower().replace("лимит_", "").strip()
    return LIMIT_DISPLAY.get(key, raw_name)


# 🔒 Фикс / 📦 Разовые — категории-счётчики с прямой суммой позиций, НЕ часть
# дискреционного пула compute_limits(). В любых формулах, вычитающих «лимиты»
# из свободных денег, их учитывать НЕЛЬЗЯ: постоянные вычитаются отдельно
# (total_obligatory), разовые — вообще отдельный бакет. Единый предикат для
# build_budget_message (nexus/handlers/finance.py) и budget_day_limit_from_plan.
_PARALLEL_LIMIT_MARKERS = ("фикс", "разов")


def is_parallel_limit(name: str) -> bool:
    """True для лимит_фикс / лимит_разовые — по ключу ИЛИ по display-имени."""
    tag = (name or "").lower().replace("лимит_", "") + " " + display_limit_name(name or "").lower()
    return any(m in tag for m in _PARALLEL_LIMIT_MARKERS)


# ── Public API ───────────────────────────────────────────────────────────────

async def get_limits(mem_db: str = "") -> Dict[str, float]:
    """Все лимиты из Памяти (PG). Возвращает {cat_link: amount}."""
    from core.repos.memory_repo import _repo as _mem_repo
    limits: Dict[str, float] = {}
    try:
        mems = await _mem_repo.find_by_category("💰 Лимит", is_current=True, page_size=100)
        logger.info("get_limits: found %d limit memories", len(mems))
    except Exception as e:
        logger.error("get_limits: %s", e)
        return {}

    for m in mems:
        fact = m.fact or ""
        связь = (m.related_to or "").strip().lower()

        fact_match = LIMIT_FACT_RE.search(fact)
        if fact_match and not связь:
            связь = cat_link(fact_match.group(1).strip())

        amount_match = LIMIT_AMOUNT_RE.search(fact)
        logger.info("get_limits: fact=%r связь=%r amount=%r",
                    fact, связь, amount_match.group(0) if amount_match else None)

        if связь and amount_match:
            limits[связь] = float(amount_match.group(1).replace(' ', '').replace(',', '.'))
        else:
            logger.warning("get_limits: skip — связь=%r fact=%r", связь, fact)

    logger.info("get_limits: result=%s", limits)
    return limits


async def load_budget_data(user_notion_id: str = "") -> Dict[str, list]:
    """Все бюджетные записи Памяти (PG).

    Возвращает {"доходы": [...], "постоянные": [...], "цели": [...],
                "долги": [...], "лимиты": [...], "разовые": [...]}.
    "разовые" — персистентные разовый_* факты (индивидуальные позиции
    принятого плана), нужны чтобы пересчёт УЖЕ принятого плана не терял
    one_time (см. nexus/handlers/finance.py:_build_sonnet_input — сессионный
    буфер к тому моменту пуст, факты в Памяти — источник истины).
    """
    from core.repos.memory_repo import _repo as _mem_repo

    empty = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": [], "разовые": []}
    try:
        mems = await _mem_repo.find_by_key_prefixes(
            ["income_", "постоянно_", "лимит_", "цель_", "разовый_"],
            user_notion_id=user_notion_id,
        )
    except Exception as e:
        logger.error("load_budget_data: %s", e)
        return empty

    result: Dict[str, list] = {"доходы": [], "постоянные": [], "цели": [],
                               "долги": [], "лимиты": [], "разовые": []}
    for m in mems:
        fact = m.fact or ""
        key = (m.key or "").strip().lower()
        if not m.is_current:
            continue

        if key.startswith("income_"):
            m = INCOME_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["доходы"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("постоянно_"):
            m = PERMANENT_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["постоянные"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("цель_"):
            # Подушка больше НЕ цель — отдельная сущность (таблица cushion).
            # Старый факт цель_подушка у Кай игнорируем, чтобы не смешивался
            # с покупками (айфон/наушники) в списке целей.
            if key == "цель_подушка":
                continue
            m = GOAL_RE.search(fact)
            if m:
                saving = parse_amount(m.group(3)) if m.group(3) else 0
                result["цели"].append({
                    "name": m.group(1).strip(),
                    "target": parse_amount(m.group(2)),
                    "saving": saving,
                    "key": key,
                    "fact": fact,
                })
        elif key.startswith("лимит_"):
            amount_m = LIMIT_AMOUNT_RE.search(fact)
            if amount_m:
                связь = (m.related_to or "").strip()
                result["лимиты"].append({
                    "name": связь or key,
                    "amount": parse_amount(amount_m.group(1)),
                })
        elif key.startswith("разовый_"):
            ot_m = ONE_TIME_FACT_RE.search(fact)
            if ot_m:
                amt = parse_amount(ot_m.group(2))
                if amt > 0:
                    result["разовые"].append({"name": ot_m.group(1).strip(), "amount": amt})

    # Долги — читаем из таблицы debts (not Memory)
    try:
        from core.repos.pg_debts_repo import _repo as _debt_repo
        active_debts = await _debt_repo.list_active(user_notion_id, kind="i_owe")
        for d in active_debts:
            result["долги"].append({
                "name": d.name,
                "amount": d.amount,
                "deadline": d.deadline,
                "strategy": d.strategy,
                "monthly_payment": d.monthly_payment,
                "fact": "",
                "key": "",
            })
    except Exception as e:
        logger.error("load_budget_data debts: %s", e)

    # Подушка — отдельная сущность (таблица cushion), НЕ цель_-факт.
    try:
        from core.repos.pg_cushion_repo import _repo as _cushion_repo
        c = await _cushion_repo.get(user_notion_id)
        if c is not None:
            result["подушка"] = {
                "balance": c.balance,
                "target": c.target,
                "planned_contribution": c.planned_contribution,
            }
    except Exception as e:
        logger.error("load_budget_data cushion: %s", e)

    # Дедупликация лимитов по display-имени
    seen_limit_names: dict = {}
    for lim in result["лимиты"]:
        display = display_limit_name(lim["name"])
        if display not in seen_limit_names:
            seen_limit_names[display] = lim
        elif lim["amount"] > seen_limit_names[display]["amount"]:
            seen_limit_names[display] = lim
    result["лимиты"] = list(seen_limit_names.values())

    return result


# ── Детерминированный расчёт лимитов ─────────────────────────────────────────
#
# Вся арифметика распределения переменных лимитов живёт здесь, а не в
# LLM-инструкциях. Sonnet больше не считает ни одной цифры лимита — только
# текстовые пояснения вокруг готовых чисел из compute_limits().

CAT_TRANSPORT = "🚕 Транспорт"
CAT_IMPULSE = "🎲 Импульсивные"
CAT_PRODUCTS = "🍜 Продукты"
CAT_HABITS = "🚬 Привычки"

# Железные категории — всегда, никогда не режутся (пока discretionary их покрывает).
IRON_TRANSPORT = 1500
IRON_IMPULSE = 1000
IRON_TOTAL = IRON_TRANSPORT + IRON_IMPULSE  # 2500

PRODUCTS_TARGET = 10000
HABITS_CEILING = 13000
_PRIORITY_FLOOR = PRODUCTS_TARGET + HABITS_CEILING  # 23000

# Единственный порог «комфортный / тяжёлый месяц». Спека (BUDGET_IDEAL_SPEC)
# формулирует его как «остаток ПОСЛЕ железных транспорт+импульс ≥ 23 000₽»
# (= _PRIORITY_FLOOR). Хендлер сравнивает `free_after_debts`, а это остаток
# ДО железных — поэтому порог на нём = _PRIORITY_FLOOR + IRON_TOTAL = 25 500₽.
# Ровно та же граница, что comfortable в compute_limits().
# ОДНА константа: и плашка «жёстко», и развилка А/Б в nexus/handlers/finance.py
# импортируют её отсюда, чтобы числа больше не разъезжались (как 18500→15500).
BUDGET_TIGHT_THRESHOLD = _PRIORITY_FLOOR + IRON_TOTAL  # 25500 (на free_after_debts)

# Доля дохода, которую в КОМФОРТНЫЙ месяц (pool_after_iron ≥ 23000) откладываем
# в финансовую подушку ДО расчёта лимитов. Настраивается — поменять число
# достаточно здесь, вся арифметика ниже пляшет от этой константы.
CUSHION_COMFORTABLE_RATE = 0.20

# Приоритет распределения остатка: каждая категория забирает 50% того, что
# осталось после предыдущей; последняя в списке — весь остаток (иначе из-за
# округления виснет копейка и сумма не сходится с discretionary).
PRIORITY_CHAIN = [
    "🍱 Кафе/Доставка",
    "💅 Бьюти",
    "🏥 Здоровье",
    "👗 Гардероб",
    "📚 Хобби/Учеба",
]

# Все категории, которые compute_limits() всегда возвращает (0, если не профинансированы).
LIMIT_CATEGORIES = [CAT_TRANSPORT, CAT_IMPULSE, CAT_PRODUCTS, CAT_HABITS] + PRIORITY_CHAIN


def _distribute_limits(discretionary: int) -> Dict[str, int]:
    """Разложить discretionary по категориям. Сумма значений ТОЧНО равна
    discretionary (последняя категория цепочки забирает остаток без деления —
    копейки от округления не теряются и не создаются).

    Ветки:
      • discretionary ≤ 0        → все категории 0 (в минус не уходим).
      • discretionary < 2500     → железные транспорт/импульсивные урезаны
                                    пропорционально долям в IRON_TOTAL, остальное 0.
      • pool_after_iron ≥ 23000  → продукты=10000, привычки≤13000, остаток
                                    по PRIORITY_CHAIN делением пополам.
      • иначе                    → продукты/привычки делят pool_after_iron пополам.
    """
    limits: Dict[str, int] = {cat: 0 for cat in LIMIT_CATEGORIES}
    if discretionary <= 0:
        return limits

    if discretionary < IRON_TOTAL:
        transport = round(discretionary * IRON_TRANSPORT / IRON_TOTAL)
        limits[CAT_TRANSPORT] = transport
        limits[CAT_IMPULSE] = discretionary - transport
        return limits

    limits[CAT_TRANSPORT] = IRON_TRANSPORT
    limits[CAT_IMPULSE] = IRON_IMPULSE
    pool_after_iron = discretionary - IRON_TOTAL

    if pool_after_iron >= _PRIORITY_FLOOR:
        limits[CAT_PRODUCTS] = PRODUCTS_TARGET
        limits[CAT_HABITS] = min(HABITS_CEILING, pool_after_iron - PRODUCTS_TARGET)
        remaining = pool_after_iron - limits[CAT_PRODUCTS] - limits[CAT_HABITS]
        last_idx = len(PRIORITY_CHAIN) - 1
        for i, cat in enumerate(PRIORITY_CHAIN):
            if i == last_idx:
                limits[cat] = remaining
            else:
                amount = round(remaining / 2)
                limits[cat] = amount
                remaining -= amount
    else:
        products = round(pool_after_iron / 2)
        limits[CAT_PRODUCTS] = products
        limits[CAT_HABITS] = pool_after_iron - products

    return limits


def compute_limits(distributable_pool: float, total_debt_payment: float,
                   income_total: float = 0.0) -> dict:
    """Детерминированное распределение переменных лимитов + взнос в подушку. Без LLM.

    discretionary = distributable_pool − total_debt_payment.

    Комфортный месяц (pool_after_iron ≥ 23000, порог PRIORITY_FLOOR):
      резервируем CUSHION_COMFORTABLE_RATE (20%) от income_total в подушку ДО
      расчёта лимитов — лимиты считаются от уменьшенного пула.
    Тяжёлый месяц (pool_after_iron < 23000): ничего заранее не резервируем,
      весь остаток идёт на жизнь (cushion_contribution = 0).

    Возвращает:
      {
        "limits": {категория: рубли(int)} по всем LIMIT_CATEGORIES,
                  сумма == discretionary после вычета подушки,
        "cushion_contribution": int — сколько зарезервировано в подушку (явно,
                  не вычтено молча).
      }
    """
    discretionary = round(distributable_pool - total_debt_payment)

    # Комфортность определяется по пулу ДО резерва подушки: хватает ли после
    # железных на продукты-цель + потолок привычек.
    comfortable = (discretionary - IRON_TOTAL) >= _PRIORITY_FLOOR

    cushion_contribution = 0
    if comfortable and income_total and income_total > 0:
        cushion_contribution = round(income_total * CUSHION_COMFORTABLE_RATE)
        # Не уводим лимиты в минус: подушка не больше, чем есть в пуле.
        cushion_contribution = max(0, min(cushion_contribution, discretionary))
        discretionary -= cushion_contribution

    return {
        "limits": _distribute_limits(discretionary),
        "cushion_contribution": int(cushion_contribution),
    }


# ── Выбор «первого горящего долга» — детерминированно, по дедлайну ───────────

_RU_MONTHS: Dict[str, int] = {}
for _i, _base in enumerate([
    "январ", "феврал", "март", "апрел", "ма", "июн",
    "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
], start=1):
    _RU_MONTHS[_base] = _i
# частые полные формы, чтобы не зависеть от префикс-матча по «ма»
_RU_MONTHS_FULL = {
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4, "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10, "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
}


def parse_deadline(s: str, *, today: Optional[date] = None) -> Optional[date]:
    """'апрель 2026' / 'до апреля' / '2026-04' / '04.2026' → date(y, m, 1).

    Без года — ближайшее будущее вхождение месяца (если месяц уже прошёл в этом
    году → следующий год). Не распарсилось → None.
    """
    if not s:
        return None
    today = today or datetime.now(_MOSCOW_TZ).date()
    low = s.strip().lower()

    # ISO / числовые: 2026-04, 2026-04-15, 04.2026, 4/2026
    m = re.search(r'(20\d{2})[-./](\d{1,2})', low)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1)
    m = re.search(r'(\d{1,2})[./](20\d{2})', low)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1)

    month = _RU_MONTHS_FULL.get(low)
    if month is None:
        for word in re.findall(r'[а-яё]+', low):
            if word in _RU_MONTHS_FULL:
                month = _RU_MONTHS_FULL[word]
                break
            for base, num in _RU_MONTHS.items():
                if word.startswith(base):
                    month = num
                    break
            if month is not None:
                break
    if month is None:
        return None

    ym = re.search(r'20\d{2}', low)
    if ym:
        return date(int(ym.group(0)), month, 1)
    year = today.year
    if month < today.month:
        year += 1
    return date(year, month, 1)


def pick_debt_payment(debts: List[dict]) -> float:
    """monthly_payment ПЕРВОГО горящего долга (по дедлайну).

    Долги без платежа (monthly_payment/monthly ≤ 0) игнорируются — наследство,
    отложенные. Долг без распознанного дедлайна уходит в конец очереди.
    Ни одного платящего долга → 0.0 (обычный месяц, без вычета).
    """
    dated: List = []
    for d in debts or []:
        mp = d.get("monthly_payment")
        if mp is None:
            mp = d.get("monthly", 0)
        try:
            mp = float(mp or 0)
        except (TypeError, ValueError):
            mp = 0.0
        if mp <= 0:
            continue
        dl = parse_deadline(str(d.get("deadline") or ""))
        dated.append((dl or date.max, mp))
    if not dated:
        return 0.0
    dated.sort(key=lambda t: t[0])
    return dated[0][1]


_MOSCOW_TZ = _tz(timedelta(hours=3))


async def _budget_payday() -> int:
    """День пэйдея из Памяти (PG). Default 1."""
    try:
        from core.repos.pg_memory_repo import PgMemoryRepo
        mems = await PgMemoryRepo().find_by_exact_key("budget_payday")
        stored = mems[0].fact if mems else None
        if stored:
            m = re.search(r"(\d+)", stored)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 1


def _period_days_remaining(payday: int, tz_offset: int = 3) -> int:
    """Дней до конца бюджетного периода (не считая сегодня) → делитель.

    tz_offset — личный часовой пояс пользователя (граница дня/периода его,
    не серверная). Дефолт 3 = поведение до фикса, когда tz не задан.
    """
    user_tz = _tz(timedelta(hours=tz_offset))
    now = datetime.now(user_tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.day >= payday:
        next_month = now.month + 1 if now.month < 12 else 1
        next_year = now.year if now.month < 12 else now.year + 1
        period_end = datetime(next_year, next_month, payday, tzinfo=user_tz) - timedelta(days=1)
    else:
        period_end = now.replace(day=payday, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return max(1, (period_end - today_start).days)


async def budget_day_limit_from_plan(user_notion_id: str, tz_offset: int = 3) -> int:
    """«Бюджет дня» — сколько можно тратить в день на повседневное.

    Явно, термин-в-термин (каждый — из своего источника, НЕ через сумму лимит_*,
    которая уже дважды разъезжалась с этой формулой):

      остаток = Доход
              − Фикс      (Память: постоянно_*)
              − Разовые   (Память: лимит_разовые, если задан)
              − Долги     (таблица debts: сумма monthly_payment активных долгов)
              − Подушка   (таблица cushion: planned_contribution; 0 в тяжёлый месяц)
              − Цели      (Память: цель_*.saving — ежемесячный взнос; 0 если цели
                           просто ждут starts_after без активного взноса)
      день   = max(0, остаток / дней_до_конца_платёжного_периода)

    tz_offset — личный tz пользователя (граница периода по его дню). Дефолт 3.
    Возвращает 0 если плана нет / нет дохода / при любой ошибке.
    """
    try:
        budget = await load_budget_data(user_notion_id)

        income = sum(d["amount"] for d in budget["доходы"])
        if income <= 0:
            return 0

        fixed = sum(d["amount"] for d in budget["постоянные"])

        one_time = next(
            (d["amount"] for d in budget["лимиты"]
             if "разов" in (d.get("name") or "").lower()
             or "разов" in display_limit_name(d.get("name") or "").lower()),
            0,
        )

        debts_monthly = sum(
            (d.get("monthly_payment") or 0) for d in budget["долги"]
            if (d.get("monthly_payment") or 0) > 0
        )

        cushion_contribution = float((budget.get("подушка") or {}).get("planned_contribution", 0) or 0)

        goals_saving = sum((d.get("saving") or 0) for d in budget["цели"])

        remainder = income - fixed - one_time - debts_monthly - cushion_contribution - goals_saving

        payday = await _budget_payday()
        days = _period_days_remaining(payday, tz_offset)
        return max(0, int(remainder / days))
    except Exception:
        logger.error("budget_day_limit_from_plan: unexpected error", exc_info=True)
        return 0
