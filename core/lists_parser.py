"""core/lists_parser.py — общий Haiku-парсер для 🗒️ Списки (v1.2).

ИСТОРИЯ:
До v1.2 Nexus и Arcana имели свои `_PARSE_BUY_SYSTEM` промпты, извлекающие
только {name, category}. Поля Цена/Заметка/Приоритет/Срок годности/Количество
существовали в Notion но не заполнялись.

В v1.2 добавлены:
- Цена план (Number)
- Магазин (rich_text)
- Этап (Number)
+ парсер начал извлекать ВСЕ поля включая существовавшие но пустые.

Промпт вынесен в core/ чтобы оба бота использовали идентичную логику
парсинга (CLAUDE.md: «Параллельная реализация = БАГ»).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.claude_client import ask_claude
from core.list_manager import LIST_CATEGORIES

logger = logging.getLogger("nexus.lists_parser")


# ── Регекс пред-парсер цен (hint для Haiku) ──────────────────────────────────

_PRICE_INLINE_RE = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(к|тыс\.?|k|₽|р\b|руб\b)?",
    re.IGNORECASE,
)


def extract_price_inline(text: str) -> Optional[float]:
    """Эвристика: вернуть первую попавшуюся цену из текста.

    Используется как hint для Haiku, чтобы он точно не пропустил число.
    Множители: к/тыс/k → ×1000, ₽/р/руб → ×1, без суффикса → ×1.
    Возвращает None если ничего внятного не нашлось.
    """
    if not text:
        return None
    for match in _PRICE_INLINE_RE.finditer(text):
        raw = match.group(1).replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower().strip(".")
        if suffix in ("к", "тыс", "k"):
            value *= 1000
        # отсечь странные мелкие числа без суффикса (вроде "iPhone 17")
        if not suffix and value < 50:
            continue
        return value
    return None


# ── Команда «сумма X» ─────────────────────────────────────────────────────────

_LIST_SUM_RE = re.compile(
    r"^\s*(?:сумма|сколько|итого|подсч[её]т)\s+(?:по\s+)?(.+?)\s*\??$",
    re.IGNORECASE,
)


def match_sum_command(text: str) -> Optional[str]:
    """«сумма Apple-стек» → 'Apple-стек'. Иначе None."""
    if not text:
        return None
    m = _LIST_SUM_RE.match(text.strip())
    return m.group(1).strip() if m else None


# ── Промпт ────────────────────────────────────────────────────────────────────

_PRIORITY_VALUES = ["🔴 Срочно", "🟡 Важно", "⚪ Можно потом"]


def build_buy_system(
    *,
    bot_hint: str = "",
    memory_cats: Optional[dict[str, str]] = None,
    price_hint: Optional[float] = None,
    today_iso: Optional[str] = None,
) -> str:
    """Собрать system-промпт для парсинга «добавь в покупки …»."""
    today_iso = today_iso or datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    cats = ", ".join(LIST_CATEGORIES)
    prio = " / ".join(_PRIORITY_VALUES)

    parts = [
        "Пользователь хочет добавить товары в список покупок. ",
        "Извлеки ВСЕ метаданные из текста и верни ТОЛЬКО JSON без markdown:\n",
        '{"items":[{"name":"iPhone Pro","category":"💳 Прочее","price_plan":108600,'
        '"source":"iPiter","stage":2,"group":"Apple-стек","note":"Deep Blue 256GB",'
        '"priority":"🟡 Важно","qty":1,"expires":null}]}\n\n',
        f"КАТЕГОРИИ (точно с эмодзи): {cats}\n",
        f"ПРИОРИТЕТЫ (точно с эмодзи): {prio}\n",
        f"СЕГОДНЯ: {today_iso} (Europe/Moscow, UTC+3)\n\n",
    ]

    if bot_hint:
        parts.append(f"КОНТЕКСТ БОТА: {bot_hint}\n\n")

    parts.append(
        "ПОЛЯ:\n"
        "1. name — чистое название без цены, магазина, глаголов «купить/добавь».\n"
        "2. category — из списка выше, с эмодзи. Эвристика:\n"
        "   - энергетики (монстр/red bull/burn) → 🚬 Привычки\n"
        "   - сигареты/снюс/вейп → 🚬 Привычки\n"
        "   - корм/наполнитель/лоток → 🐾 Коты\n"
        "   - молоко/хлеб/яйца/мясо/еда → 🍜 Продукты\n"
        "   - туалетная бумага/мыло/порошок/лампочки → 🏠 Ж***\n"
        "   - таблетки/пластырь/витамины → 🏥 Здоровье\n"
        "   - шампунь/крем/маска → 💅 Бьюти\n"
        "   - свечи/ладан/благовония → 🕯️ Расходники\n"
        "   - масла/травы → 🌿 Травы/Масла\n"
        "   - карты Таро/колоды → 🃏 Карты/Колоды\n"
        "   - электроника / гаджеты / девайсы → 💻 Техника:\n"
        "     • iPhone / iPad / Mac / MacBook / iMac / AirPods / AirTag\n"
        "       / Apple Watch / Pixel / Samsung / Galaxy / Xiaomi → 💻 Техника\n"
        "     • ноутбук, монитор, наушники, клавиатура, мышь, веб-камера\n"
        "       → 💻 Техника\n"
        "     • телефон, планшет, гарнитура, чехол, кабель, зарядка → 💻 Техника\n"
        "     • Браслет / Loop / Strap / ремешок к часам → 💻 Техника\n"
        "   ВАЖНО: 💻 Подписки = ежемесячные сервисы (Spotify, Claude, VPN);\n"
        "          💻 Техника = разовая покупка устройства/аксессуара. Не путать.\n"
        "3. price_plan — число рублей (нет → null):\n"
        "   - «108к»→108000, «1.5к»→1500, «0.5к»→500\n"
        "   - «89р»→89, «2500₽»→2500, голое число «5000» в контексте цены→5000\n"
        "4. source — название магазина (нет → null):\n"
        "   - «в iPiter» / «в Pedant» / «в Озон» → название без предлога\n"
        "   - «на Wildberries» / «на WB» → «Wildberries» / «WB»\n"
        "   - «у мастера на Авито» → «Авито»\n"
        "5. stage — этап ДОЛГОГО плана 1..5 (нет → null):\n"
        "   - «этап 2» → 2, «первая волна» → 1, «вторая волна» → 2\n"
        "   - НЕ путать с приоритетом срочности.\n"
        "6. group — название подсписка (нет → null):\n"
        "   - «в Apple-стек» / «в группу X» / «в раздел X» → значение без предлога\n"
        "   - group ≠ category. category=тип расходов, group=произвольный подсписок.\n"
        "7. note — ОСТАТОК смыслового текста ПОСЛЕ извлечения остального (нет → null):\n"
        "   - «iPhone Pro 108к Deep Blue 256GB» → note=«Deep Blue 256GB»\n"
        "   - «молоко из Лосино-петровского» → note=«из Лосино-петровского»\n"
        "   - длиннее 100 символов → обрезать с многоточием.\n"
        f"8. priority — ровно одно из {prio} (нет → null):\n"
        "   - «срочно/сейчас же/горит» → «🔴 Срочно»\n"
        "   - «важно/не забыть» → «🟡 Важно»\n"
        "   - «когда-нибудь/не срочно/потом/может быть» → «⚪ Можно потом»\n"
        "9. qty — количество в штуках/упаковках (нет → null):\n"
        "   - «5 пачек кофе» → 5, «3 штуки» → 3\n"
        "   - «литр молока» → null (литр — единица объёма, не штуки)\n"
        "   - «5 литров» → 5\n"
        "10. expires — ISO дата YYYY-MM-DD (нет → null):\n"
        "    - «до 15 ноября» → текущий или ближайший будущий год\n"
        "    - «к среде» / «к понедельнику» → ближайшая будущая дата\n"
        "    - «через неделю» → сегодня + 7 дней\n"
        "    - «истекает 2026-12-01» → «2026-12-01»\n\n"
        "СПИСКИ:\n"
        "- Многострочный ввод / делиметры , ; - • → отдельные items.\n"
        "- Если у группы общий source/priority — повторяй на каждом item где явно указано.\n"
        "- name извлекать аккуратно — без обрезков типа «в iPiter».\n\n"
        "СОВМЕСТИМОСТЬ:\n"
        "- «молоко в покупки» → один item, все extra-поля = null.\n"
        "- Если поле не нашлось — null, НЕ выдумывай.\n"
        "- Поле всегда обязано присутствовать в JSON (даже если null).\n"
    )

    if memory_cats:
        mem_lines = "\n".join(f"- {n} → {c}" for n, c in memory_cats.items())
        parts.append(f"\nИЗВЕСТНЫЕ ПРЕДПОЧТЕНИЯ ИЗ ПАМЯТИ (приоритет):\n{mem_lines}\n")

    if price_hint is not None:
        parts.append(
            f"\nREGEX-HINT price_plan≈{int(price_hint)}. "
            "Проверь и подтверди если совпадает с контекстом.\n"
        )

    return "".join(parts)


# ── JSON-парсер ───────────────────────────────────────────────────────────────

def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").strip()
    raw = raw.removesuffix("```").strip()
    return raw


def _normalize_priority(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    for canon in _PRIORITY_VALUES:
        if v == canon or v.lower() == canon.lower():
            return canon
    bare = v.lower().lstrip("⚪🟡🔴 ").strip()
    mapping = {
        "срочно": "🔴 Срочно",
        "важно": "🟡 Важно",
        "можно потом": "⚪ Можно потом",
        "потом": "⚪ Можно потом",
    }
    return mapping.get(bare)


def _coerce_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v) -> Optional[int]:
    f = _coerce_float(v)
    return int(f) if f is not None else None


def _truncate_note(note: Optional[str]) -> Optional[str]:
    if not note:
        return None
    n = note.strip()
    if not n:
        return None
    if len(n) > 100:
        return n[:97].rstrip() + "..."
    return n


def normalize_buy_item(raw: dict) -> dict:
    """Нормализовать один item из Haiku-ответа.

    Гарантирует все 10 полей (name, category, price_plan, source, stage, group,
    note, priority, qty, expires). Невалидные значения → None.
    """
    name = (raw.get("name") or "").strip()
    return {
        "name": name,
        "category": (raw.get("category") or "").strip() or None,
        "price_plan": _coerce_float(raw.get("price_plan")),
        "source": (raw.get("source") or "").strip() or None,
        "stage": _coerce_int(raw.get("stage")),
        "group": (raw.get("group") or "").strip() or None,
        "note": _truncate_note(raw.get("note")),
        "priority": _normalize_priority(raw.get("priority")),
        "qty": _coerce_float(raw.get("qty")),
        "expires": (raw.get("expires") or "").strip() or None,
    }


def parse_buy_response(raw: str) -> list[dict]:
    """Распарсить ответ Haiku → список нормализованных items.

    Поддерживает оба формата:
    - {"items": [...]} (новый, v1.2)
    - [...] (legacy, v1.1 — массив верхнего уровня)
    Возвращает список items, каждый прошёл normalize_buy_item.
    """
    text = _strip_fence(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("parse_buy_response: bad JSON: %s | raw=%s", e, text[:200])
        return []

    if isinstance(parsed, dict):
        items_raw = parsed.get("items")
        if items_raw is None:
            # legacy: одиночный объект без обёртки items
            items_raw = [parsed] if parsed.get("name") else []
    elif isinstance(parsed, list):
        items_raw = parsed
    else:
        return []

    out = []
    for r in items_raw:
        if not isinstance(r, dict):
            continue
        item = normalize_buy_item(r)
        if item.get("name"):
            out.append(item)
    return out


async def parse_buy_text(
    text: str,
    *,
    bot_hint: str = "",
    memory_cats: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Полный pipeline: regex hint + Haiku + normalize.

    Возвращает список items с 10 полями. Пустой список = парсинг провалился.
    """
    price_hint = extract_price_inline(text)
    system = build_buy_system(
        bot_hint=bot_hint, memory_cats=memory_cats, price_hint=price_hint,
    )
    try:
        raw = await ask_claude(
            text, system=system, max_tokens=800,
            model="claude-haiku-4-5-20251001",
        )
    except Exception as e:
        logger.error("parse_buy_text: ask_claude failed: %s", e)
        return []
    return parse_buy_response(raw)


# ── Форматирование сумм ───────────────────────────────────────────────────────

def format_rub(amount: float) -> str:
    """108600 → '108 600'. Целые отображаются без копеек."""
    n = int(round(amount or 0))
    s = f"{n:,}".replace(",", " ")
    return s
