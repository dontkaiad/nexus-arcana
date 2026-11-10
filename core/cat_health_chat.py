"""core/cat_health_chat.py — ввод здоровья котов из чата: «Тучка весит 4,2 кг», «Пончик ветеринар 12 декабря», «Кексу капли с 15-го на 10 дней».

Встроено в существующий разбор фактов (core.memory.parse_and_store) БЕЗ нового вызова LLM:
  1. дешёвая проверка без LLM (`wants_health`): в тексте есть имя / кличка кота И слово про здоровье. Нет — разбор идёт как раньше, промпт
     не меняется, лишних токенов ноль;
  2. да — к ТОМУ ЖЕ запросу Haiku добавляется короткий блок (`prompt_block`): в JSON ответа появляется необязательный ключ «здоровье»;
  3. код проверяет ключ строго (`validate`): тип, диапазоны, даты, число граммов сверяется с числом из исходного текста; любое сомнение —
     None, и сообщение сохраняется обычным фактом, как раньше (ничего не теряется и ничего не выдумывается);
  4. `store` пишет в cat_weights / cat_vet_visits / cat_medications через core.cat_health (с задачами-напоминаниями), повтор того же
     сообщения идемпотентен.
Кот должен резолвиться ровно в одну карточку владельца, иначе — обычный факт.
"""
from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from core import cat_health as svc
from core.repos import pg_cat_health_repo as repo

# слова про здоровье (без LLM, регистр не важен); голое «курс» не берём — слишком общее слово
HEALTH_RE = re.compile(
    r"вес(?!ь)|весит|весил|взвеш|\bкг\b|килограмм|грамм|\bгр\b|ветеринар|\bвет\b|ветклиник|врач|прививк|вакцин|глист|дегельмин|"
    r"блох|клещ|капл|таблетк|лекарств|антибиот|укол|осмотр|анализ|курс\s+(?:лечени|капел|таблет|антибиот|укол)", re.I)
_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|килограмм\w*|гр\w*|г\b|грамм\w*)", re.I)
KINDS = ("visit", "vaccine", "deworm", "flea", "other")
_TYPES = {"вес": "weight", "визит": "visit", "лекарство": "med"}


def _stem(name: str) -> str:
    n = name.lower().replace("ё", "е").strip()
    return n[: max(3, len(n) - 2)] if len(n) > 3 else n


def wants_health(text: str, cat_names: List[str]) -> bool:
    """Есть имя кота (с падежами по основе) и слово про здоровье."""
    t = (text or "").lower().replace("ё", "е")
    if not HEALTH_RE.search(t):
        return False
    words = re.findall(r"[a-zа-я0-9-]+", t)
    return any(w.startswith(_stem(n)) for n in cat_names or [] if n and n.strip() for w in words)


_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def prompt_block(today: date) -> str:
    return (
        "\n\nЗДОРОВЬЕ КОТА. Если сообщение про ВЕС кота, ВИЗИТ кота к ветеринару (в том числе прививку, обработку от глистов / блох) "
        "или КУРС ЛЕКАРСТВА коту, добавь в JSON ещё один ключ \"здоровье\" (объект); остальные ключи заполняй как обычно. "
        f"Сегодня {today.isoformat()} ({_WEEKDAYS[today.weekday()]}). Даты пиши ГГГГ-ММ-ДД; дата без года — ближайшая будущая (начало курса «с 15-го» — "
        "ближайшее 15-е число, сегодня подходит; «в пятницу» — ближайшая пятница). Неточное время («в начале месяца», «на днях», «скоро», «на "
        "следующей неделе») — НЕ угадывай дату и НЕ добавляй \"здоровье\". Формы объекта:\n"
        '  {"тип":"вес","кот":"имя","граммы":4200}   (4,2 кг = 4200)\n'
        '  {"тип":"визит","кот":"имя","название":"осмотр","дата":"ГГГГ-ММ-ДД","вид":"visit|vaccine|deworm|flea|other","следующий_срок":"ГГГГ-ММ-ДД или пусто"}\n'
        '  {"тип":"лекарство","кот":"имя","название":"капли","доза":"по 2 капли или пусто","начало":"ГГГГ-ММ-ДД","дней":10,"приёмов":1}\n'
        "Нет имени кота, числа или даты — НЕ добавляй \"здоровье\".\n"
        '  "тучка весит 4,2 кг" → {"fact":"тучка весит 4,2 кг","category":"🐾 Коты","связь":"тучка","ключ":"тучка_вес","здоровье":{"тип":"вес","кот":"тучка","граммы":4200}}\n'
        '  "пончик ветеринар 12 декабря" → {"fact":"пончик у ветеринара 12 декабря","category":"🐾 Коты","связь":"пончик","ключ":"пончик_ветеринар","здоровье":{"тип":"визит","кот":"пончик","название":"ветеринар","дата":"<12 декабря>","вид":"visit","следующий_срок":""}}\n'
        '  "кексу капли с 15-го на 10 дней" → {"fact":"кексу капли с 15-го на 10 дней","category":"🐾 Коты","связь":"кекс","ключ":"кекс_капли","здоровье":{"тип":"лекарство","кот":"кекс","название":"капли","доза":"","начало":"<ближайшее 15-е>","дней":10,"приёмов":1}}'
    )


@dataclass(frozen=True)
class Item:
    type: str                       # weight | visit | med
    cat_name: str
    grams: int = 0
    title: str = ""
    on: Optional[date] = None       # дата визита / начало курса
    kind: str = "visit"
    next_due: Optional[date] = None
    dose: Optional[str] = None
    days: Optional[int] = None
    times: int = 1


def _date(v: Any, lo: date, hi: date) -> Optional[date]:
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
        return None
    try:
        d = date.fromisoformat(v.strip())
    except ValueError:
        return None
    return d if lo <= d <= hi else None


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _text(v: Any, limit: int) -> Optional[str]:
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    return v if v and len(v) <= limit else None


def weight_from_text(text: str) -> Optional[int]:
    """Граммы из исходного сообщения («4,2 кг», «4200 г»); None — числа нет или оно неоднозначно (разные значения)."""
    vals = set()
    for num, unit in _WEIGHT_RE.findall(text or ""):
        x = float(num.replace(",", "."))
        vals.add(int(round(x * 1000)) if unit.lower().startswith(("кг", "килограмм")) else int(round(x)))
    return vals.pop() if len(vals) == 1 else None


def validate(raw: Any, today: date, text: str = "") -> Optional[Item]:
    """Строгая проверка ключа «здоровье» из ответа модели. Любое сомнение — None (сообщение сохранится обычным фактом)."""
    if not isinstance(raw, dict):
        return None
    typ = _TYPES.get(str(raw.get("тип") or "").strip().lower())
    cat = _text(raw.get("кот"), 60)
    if typ is None or cat is None:
        return None
    if typ == "weight":
        g = _int(raw.get("граммы"))
        from_text = weight_from_text(text)
        if from_text is not None:
            g = from_text                                   # число из самого сообщения надёжнее пересказа модели
        if g is None or not repo.GRAMS_MIN <= g <= repo.GRAMS_MAX:
            return None
        return Item("weight", cat, grams=g)
    if typ == "visit":
        d = _date(raw.get("дата"), today - timedelta(days=366), today + timedelta(days=731))
        title_raw = raw.get("название")
        title = _text(title_raw, 120) if title_raw not in (None, "") else "ветеринар"
        if title is None:
            return None
        kind = str(raw.get("вид") or "visit").strip().lower()
        if d is None or kind not in KINDS:
            return None
        nd_raw = raw.get("следующий_срок")
        nd = _date(nd_raw, d, d + timedelta(days=1100)) if nd_raw not in (None, "") else None
        if nd_raw not in (None, "") and nd is None:
            return None
        return Item("visit", cat, title=title, on=d, kind=kind, next_due=nd)
    name = _text(raw.get("название"), 80)
    start = _date(raw.get("начало") or today.isoformat(), today - timedelta(days=30), today + timedelta(days=365))
    days_raw, times_raw = raw.get("дней"), raw.get("приёмов")
    days = _int(days_raw) if days_raw not in (None, "") else None
    times = _int(times_raw) if times_raw not in (None, "") else 1
    if days_raw not in (None, "") and days is None:
        return None
    dose_raw = raw.get("доза")
    dose = _text(dose_raw, 80) if dose_raw not in (None, "") else None
    if name is None or start is None or times is None or not 1 <= times <= 4 or (days is not None and not 1 <= days <= 365) or (dose_raw not in (None, "") and dose is None):
        return None
    return Item("med", cat, title=name, on=start, dose=dose, days=days, times=times)


def _kg(g: int) -> str:
    return f"{g / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",")


async def store(owner_tg: int, user_id: str, cat: Dict[str, Any], item: Item, today: date) -> Dict[str, Any]:
    """Записать. Возвращает {type, cat_id, cat, duplicate, task_linked, summary_html}. Повтор того же сообщения ничего не дублирует."""
    cid, name = int(cat["id"]), html.escape(cat["name"])
    if item.type == "weight":
        row = await svc.add_weight(owner_tg, cid, item.grams, today)
        return {"type": "weight", "cat_id": cid, "cat": cat["name"], "id": row["id"], "duplicate": False, "task_linked": False,
                "summary_html": f"⚖️ {name}: {_kg(item.grams)} кг"}
    if item.type == "visit":
        status = "done" if item.on < today else "planned"
        same = [v for v in await asyncio.to_thread(repo.list_visits, user_id, cid) if v["visit_on"] == item.on and v["kind"] == item.kind
                and v["title"].lower() == item.title.lower() and v["status"] != "cancelled"]
        if same:
            v, dup = same[0], True
        else:
            v, dup = await svc.add_visit(owner_tg, cid, item.title, item.on, kind=item.kind, next_due=item.next_due, status=status), False
        icon = "💉" if item.kind == "vaccine" else "🩺"
        tail = ", напомню накануне в 09:00" if v.get("task_linked") and status == "planned" else ""
        return {"type": "visit", "cat_id": cid, "cat": cat["name"], "id": v["id"], "duplicate": dup, "task_linked": bool(v.get("task_linked")),
                "summary_html": f"{icon} {name}: {html.escape(item.title)} {item.on:%d.%m.%Y}{tail}" + (" (уже записано)" if dup else "")}
    same_m = [m for m in await asyncio.to_thread(repo.list_meds, user_id, cid, "active") if m["name"].lower() == item.title.lower() and m["starts_on"] == item.on]
    if same_m:
        m, dup = same_m[0], True
    else:
        m, dup = await svc.add_med(owner_tg, cid, item.title, item.on, days=item.days, dose=item.dose, times_per_day=item.times), False
    span = f" на {item.days} дн." if item.days else ""
    tail = ", напоминаю каждый день в 09:00" if m.get("task_linked") else ""
    return {"type": "med", "cat_id": cid, "cat": cat["name"], "id": m["id"], "duplicate": dup, "task_linked": bool(m.get("task_linked")),
            "summary_html": f"💊 {name}: {html.escape(item.title)} с {item.on:%d.%m}{span}{tail}" + (" (уже записано)" if dup else "")}
