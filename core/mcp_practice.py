"""core/mcp_practice.py — правила создания личной практики (работы Arcana без клиента) из MCP (ADR-0040). Чистые функции.

Что тут живёт: категории и приоритеты из белых списков, проверка названия на имя клиента (без указания, какое именно),
тексты алертов. Дату, время, окно дат, лимит «в сутки» и пересечение с бронями берём из core.mcp_tasks (одно правило на двоих).
Маршрут (miniapp/backend/routes/mcp_practice.py) только собирает это вместе и пишет в базу.
"""
from __future__ import annotations

import html
import re
from typing import Iterable, Optional, Sequence

from core import mcp_tasks as _tasks
from core.mcp_tasks import (  # noqa: F401  (реэкспорт: маршрут и тесты берут правила отсюда)
    Conflict, DailyLimiter, McpTaskError, add_years, check_date_window, find_conflicts, parse_date, task_interval, when_text,
)

CREATE_LIMIT_PER_DAY = 20
TITLE_MAX = 120
PRIO_LABEL = {"🔴": "Срочно", "🟡": "Важно", "⚪": "Можно потом"}   # коды, которые понимает PgWorksRepo
DEFAULT_PRIO = "🟡"
# Белый список категорий работ практики (те же шесть, что знает бот: arcana/handlers/works.py WORK_CATEGORY_MAP).
CATEGORIES = ("🃏 Расклад", "✨ Ритуал", "📱 Соцсети", "🛒 Расходники", "📚 Обучение", "🗂️ Прочее")
DEFAULT_CATEGORY = "🗂️ Прочее"
MIN_NAME_TOKEN = 3                                   # короче трёх букв в имени клиента не ищем (слишком много случайных совпадений)

_CATEGORY_BY_NAME = {_tasks._name_of(c): c for c in CATEGORIES}


# ── поля ─────────────────────────────────────────────────────────────────────

def clean_title(raw: str) -> str:
    """1..120 символов после обрезки пробелов, без управляющих символов и < > (как у задач, только короче)."""
    return _tasks.clean_title(raw, TITLE_MAX)


def parse_time(raw: Optional[str]) -> Optional[str]:
    """HH:MM. 00:00 отказ: у работ полночь значит «без времени» (бот, дашборд и слот Зари так её и читают), а Claude просил бы время."""
    hhmm = _tasks.parse_time(raw)
    if hhmm == "00:00":
        raise McpTaskError(422, "bad_time", "00:00 means no time for a work; omit deadline_time or use 00:01..23:59")
    return hhmm


def pick_category(raw: str) -> str:
    """Имя или полный код из белого списка (регистр и эмодзи не важны); всё остальное 422."""
    code = _CATEGORY_BY_NAME.get(_tasks._name_of(raw or ""))
    if code is None:
        raise McpTaskError(422, "bad_category", "unknown category")
    return code


def pick_priority(emoji: str) -> str:
    """🔴 🟡 ⚪ (⚪ с селектором вариации или без него) → «Срочно» / «Важно» / «Можно потом»."""
    e = (emoji or "").replace("️", "")
    if e not in PRIO_LABEL:
        raise McpTaskError(422, "bad_prio", "prio must be one of 🔴 🟡 ⚪")
    return PRIO_LABEL[e]


# ── имя клиента в названии ───────────────────────────────────────────────────

_WORD_RE = re.compile(r"[0-9a-zа-яё]+")


def _norm(text: str) -> str:
    """Регистр не важен, ё = е."""
    return (text or "").casefold().replace("ё", "е")


_POSSESSIVE = ("ин", "ина", "ино", "ины", "ину", "иной")       # Машин, Машина, Машину …
_SUFFIX_A = ("ы", "е", "у", "ой", "ей", "ою", "и")              # Маша → Маши, Маше, Машу, Машей, Верой …
_SUFFIX_YA = ("я", "и", "е", "ю", "ей", "ею")                   # Мария → Марии, Марию, Марией
_SUFFIX_SOFT = ("я", "ю", "ем", "е")                            # Игорь → Игоря, Игорю, Игорем; Сергей → Сергея …
_SUFFIX_HARD = ("а", "у", "ом", "е")                            # Олег → Олега, Олегу, Олегом, Олеге
_POSSESSIVE_HARD = ("ов", "ова", "ово", "ову", "овой")          # Олегов, Олегова …


def name_forms(token: str) -> set:
    """Нормализованное слово имени → множество его форм (падежи и притяжательные), всё слово целиком, не кусок слова."""
    t = _norm(token)
    forms = {t}
    if t.endswith("а"):
        s = t[:-1]
        forms |= {s + e for e in _SUFFIX_A} | {s + e for e in _POSSESSIVE}
    elif t.endswith("я"):
        s = t[:-1]
        forms |= {s + e for e in _SUFFIX_YA}
        if not t.endswith("ия"):                                    # Таня → Танин; Мария → «Марьин» нам не нужен, а «Марин» это уже другое имя
            forms |= {s + e for e in _POSSESSIVE}
    elif t.endswith(("ь", "й")):
        s = t[:-1]
        forms |= {s + e for e in _SUFFIX_SOFT}
    elif re.search(r"[б-джзк-нп-тф-щ]$", t):                       # согласная на конце
        forms |= {t + e for e in _SUFFIX_HARD} | {t + e for e in _POSSESSIVE_HARD}
    return forms


def client_forms(names: Iterable[str]) -> set:
    """Все формы всех слов (от 3 букв) всех имён клиентов. Имя «Анна Иванова» даёт формы и «Анна», и «Иванова»."""
    out: set = set()
    for name in names:
        for token in _WORD_RE.findall(_norm(name)):
            if len(token) >= MIN_NAME_TOKEN:
                out |= name_forms(token)
    return out


def title_has_client_name(title: str, names: Iterable[str]) -> bool:
    """Правило совпадения: название разбирается на слова (регистр не важен, ё = е), слово целиком равно одной из форм слова
    из имени клиента (имя в любом падеже, притяжательное «Машин»). Часть слова не считается: «Вера» не найдётся в «верно»,
    «Анна» в «Ананасы». Не ловит уменьшительные («Машенька»), опечатки и имена короче трёх букв."""
    forms = client_forms(names)
    return bool(forms) and any(w in forms for w in _WORD_RE.findall(_norm(title)))


def check_title_has_no_client(title: str, names: Iterable[str]) -> None:
    if title_has_client_name(title, names):
        # какое имя совпало, не говорим: ответ не должен подтверждать, что такой клиент есть
        raise McpTaskError(422, "client_name_in_title", "title must not contain a client's name; rephrase without it")


# ── тексты алертов ───────────────────────────────────────────────────────────

def alert_created(title: str, d: str, hhmm: Optional[str]) -> str:
    return f"🤖 Claude создал практику: <b>{html.escape(title)}</b>, срок {html.escape(when_text(d, hhmm))}"


def alert_conflict(work_id: str, title: str, conflicts: Sequence[Conflict]) -> str:
    """Имя друга попадает только сюда (закрытый топик лог-группы); название и имя экранируются для HTML."""
    def one(c: Conflict) -> str:
        return f"{html.escape(c.name or 'без имени')} {html.escape(c.date)} {html.escape(c.starts)}-{html.escape(c.ends)}"
    head = f"⚠️ Практика #{html.escape(str(work_id))} <b>{html.escape(title)}</b> занимает час в Заре и пересекается с "
    if len(conflicts) == 1:
        return head + f"бронью {one(conflicts[0])}, перенеси"
    return head + "бронями: " + "; ".join(one(c) for c in conflicts) + ", перенеси"
