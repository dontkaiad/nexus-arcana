"""core/memory_answer.py — ответ бота СВОИМИ СЛОВАМИ по найденным фактам (этап 4).

Вход LLM — ТОЛЬКО структурный Result из `core.memory_search.answer()`: группы (человек / тёзки раздельно)
и их факты (id, текст, дата, платформа, цитата-основание). Больше модель ничего не знает.

Grounding (строгий; любое нарушение → ответ отбрасывается, бот показывает прежний список):
  1  формат: JSON {"blocks":[{"group":N,"lines":[{"t":"…","ids":[…]}]}]}
  2  каждый id — из входа и именно из ЭТОЙ группы (тёзок не смешиваем)
  3  у каждой фразы есть хотя бы один id
  4a число во фразе есть в цитируемых фактах
  4b имя/слово с заглавной во фразе есть в цитируемых фактах или в подписи группы
  4c значимое слово (≥5 букв) есть в цитируемых фактах (по основе)
  5  формат: фраза длиннее 220 символов или > 30 фраз на блок (обычные излишки не ошибка: при сборке режем до
     6 фраз на группу и ~700 символов, и в «использованные» попадают только оставленные фразы)
  6  пустой ответ при непустом входе («ничего не нашла») — не выдумываем, показываем список
Дополнительно: таймаут (GEN_TIMEOUT), битый JSON, пустой ответ API.

Без LLM (решает вызывающий): ДР, «не знаю», «не нашла в Людях», пусто, ≤1 факта.
Каждая попытка пишется в `memory_answer_log` (успех / fallback + причина + id) и в лог —
по ним считается доля fallback за день (scripts/memory_answer_stats.py).
Модель — Haiku 5.5 (MEMORY_ANSWER_MODEL откатывает на 4.5), мышление выключено, temperature не шлём.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from html import escape
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import sqlalchemy as sa

from core.claude_client import ask_claude
from core.db import get_engine
from core.ru_morph import strip_case_ending

logger = logging.getLogger("core.memory_answer")

DEFAULT_MODEL = "claude-haiku-5-5"       # 5.5: без temperature, мышление выключено (claude_client.apply_model_params)


def model() -> str:
    """Модель генерации; откат на 4.5 — переменной окружения MEMORY_ANSWER_MODEL без правки кода."""
    return os.environ.get("MEMORY_ANSWER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


GEN_TIMEOUT = 8.0
MAX_PER_GROUP = 20
MAX_FACTS = 40
QUOTE_LEN = 160
MAX_LINES_PER_BLOCK = 30        # «абсурдно много» — формат нарушен; обычные излишки аккуратно режутся при сборке
MAX_LINE_CHARS = 220            # одна фраза длиннее — это не фраза, а пересказ
KEEP_LINES = 6                  # сколько фраз на группу показываем
KEEP_CHARS = 700                # и сколько символов всего
GENERATED_ROUTES = {"direct", "hybrid", "hybrid_self", "hybrid_person"}

Trace = Callable[[str, dict], None]

SYSTEM = (
    "Ты отвечаешь Кай (она, женский род) на вопрос по её личной памяти. Тебе даны ТОЛЬКО факты; других знаний "
    "о людях у тебя нет.\n"
    "ПРАВИЛА:\n"
    "1. Каждая фраза ответа опирается на факты и указывает их id. Ничего не добавляй от себя: не обобщай, не "
    "оценивай, не делай выводов, не придумывай причин и примеров. Используй слова самих фактов: не подбирай "
    "синонимов, не поясняй и не переформулируй сложнее, чем в факте.\n"
    "2. Сохраняй смысл и полярность: «не любит» остаётся «не любит», «любит» — «любит», «аллергия» — «аллергия». "
    "Не заменяй слова более сильными или мягкими.\n"
    "3. Близкие факты объединяй в одну короткую фразу. Фраза — это то, что идёт ПОСЛЕ слов «не любит:» / «любит:» "
    "/ «важное:», заголовок и вводные слова добавит программа (так что пиши «спешку и смену планов», а не "
    "«Миша не любит спешку»).\n"
    "4. Каждая группа (человек) — отдельный блок. Людей из разных групп не смешивай, ids бери только из "
    "своей группы.\n"
    "5. Имена, числа и даты бери только из фактов. id в текст фраз не пиши.\n"
    "6. Если ни один факт не отвечает на вопрос — верни {\"blocks\": []}.\n"
    "7. КОРОТКО: на каждую группу не больше 6 фраз и не больше ~500 символов вместе, каждая фраза — словосочетание "
    "до 12 слов. Когда фактов много, объединяй близкие по смыслу в одну фразу через запятую, а менее значимые "
    "опускай (полный список пользователь откроет кнопкой «Все факты»).\n"
    "Верни ТОЛЬКО JSON без markdown: "
    "{\"blocks\":[{\"group\":1,\"lines\":[{\"t\":\"…\",\"ids\":[1,2]}]}]}"
)


@dataclass
class GFact:
    id: int
    fact: str
    date: str = ""
    platform: str = ""
    quote: str = ""
    source: str = ""
    year: str = ""


@dataclass
class GGroup:
    n: int
    label: str
    person_id: Optional[int]
    facts: List[GFact] = field(default_factory=list)

    def ids(self) -> set:
        return {f.id for f in self.facts}

    def by_id(self) -> Dict[int, GFact]:
        return {f.id: f for f in self.facts}


@dataclass
class Outcome:
    ok: bool
    text: str = ""                       # готовый HTML-текст (ответ + строка источника), если ok
    used_ids: List[int] = field(default_factory=list)
    reason: str = ""                     # при fallback: timeout | api_error | bad_json | check_N
    detail: str = ""
    check: str = ""                      # номер проверки при нарушении grounding
    bad_ids: List[int] = field(default_factory=list)
    n_facts: int = 0
    latency_ms: int = 0
    out_chars: int = 0
    in_tokens: int = 0
    out_tokens: int = 0


def enabled() -> bool:
    return os.environ.get("MEMORY_GENERATE_ANSWER", "1").strip().lower() not in ("0", "false", "off", "no")


def eligible(result) -> bool:
    """Генерация только там, где есть что пересказывать: ≥2 факта и маршрут с фактами."""
    return (enabled() and result.kind == "facts" and result.route in GENERATED_ROUTES
            and sum(len(g.hits) for g in result.groups) >= 2)


# ── вход ─────────────────────────────────────────────────────────────────────

def _iso_year(s: str) -> str:
    return s[:4] if s and re.match(r"^\d{4}", s) else ""


def build_groups(result) -> List[GGroup]:
    groups: List[GGroup] = []
    total = 0
    for g in result.groups:
        if not g.hits:
            continue
        gg = GGroup(n=len(groups) + 1, label=g.label, person_id=g.person_id)
        for h in g.hits[:MAX_PER_GROUP]:
            if total >= MAX_FACTS:
                break
            m = h.memory
            gg.facts.append(GFact(
                id=int(m.id), fact=(m.fact or "").strip(), date=(m.source_message_date or m.date or "")[:10],
                platform=(m.source_platform or "").lower(), quote=(m.source_quote or "").strip()[:QUOTE_LEN],
                source=(m.source or "").lower(), year=_iso_year(m.source_message_date or m.date or ""),
            ))
            total += 1
        if gg.facts:
            groups.append(gg)
    return groups


_TYPE_RU = {"dislikes": "что не любит", "likes": "что любит", "important": "важное (здоровье, запреты)"}


def build_prompt(query: str, groups: List[GGroup], types: Sequence[str]) -> str:
    kind = " + ".join(_TYPE_RU[t] for t in types if t in _TYPE_RU) or "—"
    lines = [f"Вопрос: {query!r}", f"Что спрашивают: {kind}", "", "Группы фактов:"]
    for g in groups:
        lines.append(f"[{g.n}] {g.label or 'факты Кай'} — {len(g.facts)} шт.")
        for f in g.facts:
            bits = [f"id={f.id}", f.date or "", f.platform or "", f.fact]
            if f.quote:
                bits.append(f"основание: «{f.quote}»")
            lines.append("  " + " | ".join(b for b in bits if b))
    return "\n".join(lines)


# ── проверка grounding ───────────────────────────────────────────────────────

_WORD = re.compile(r"[\w\-]+", re.UNICODE)
_NUM = re.compile(r"\d+")
_FUNC_WORDS = {
    "когда", "который", "которые", "которая", "которое", "если", "чтобы", "потому", "очень", "всегда", "иногда",
    "часто", "почти", "также", "только", "между", "через", "перед", "после", "этого", "поэтому", "свою", "своей",
    "всего", "более", "менее", "может", "могут", "будет", "бывает", "тоже", "лишь",
}


def _stem(w: str, n: int = 5) -> str:
    w = w.lower().replace("ё", "е")
    return strip_case_ending(w)[:n]


def _stems(text: str, n: int = 5) -> set:
    return {_stem(w, n) for w in _WORD.findall(text or "")}


@dataclass
class Check:
    ok: bool
    check: str = ""
    bad_ids: List[int] = field(default_factory=list)
    used_ids: List[int] = field(default_factory=list)
    detail: str = ""                     # подробность нарушения БЕЗ текстов фактов (для лога): lines/chars/word_len


def validate(data, groups: List[GGroup], has_input: bool = True) -> Check:
    by_n = {g.n: g for g in groups}
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        return Check(False, "1")
    blocks = data["blocks"]
    if not blocks:
        return Check(False, "6") if has_input else Check(True)
    used: List[int] = []
    total = 0
    for b in blocks:
        if not isinstance(b, dict) or not isinstance(b.get("lines"), list) or not isinstance(b.get("group"), int):
            return Check(False, "1")
        g = by_n.get(b["group"])
        if g is None:
            return Check(False, "1")
        if len(b["lines"]) > MAX_LINES_PER_BLOCK:
            return Check(False, "5", detail=f"lines={len(b['lines'])}")
        label_stems = _stems(g.label, 4)
        for ln in b["lines"]:
            if not isinstance(ln, dict) or not isinstance(ln.get("t"), str) or not isinstance(ln.get("ids"), list):
                return Check(False, "1")
            ids = ln["ids"]
            if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                return Check(False, "1")
            if not ids:
                return Check(False, "3")
            foreign = [i for i in ids if i not in g.ids()]
            if foreign:
                return Check(False, "2", bad_ids=foreign)
            t = ln["t"].strip()
            if len(t) > MAX_LINE_CHARS:
                return Check(False, "5", detail=f"line_chars={len(t)}")
            total += len(t)
            src = " ".join(g.by_id()[i].fact for i in ids)
            # 4a — числа
            src_nums = set(_NUM.findall(src))
            if any(n not in src_nums for n in _NUM.findall(t)):
                return Check(False, "4a", bad_ids=list(ids))
            words = _WORD.findall(t)
            # 4b — слова с заглавной (кроме первого слова фразы)
            src_stems4 = _stems(src, 4) | label_stems
            for w in words[1:]:
                if len(w) >= 2 and w[0].isupper() and not w.isdigit() and _stem(w, 4) not in src_stems4:
                    return Check(False, "4b", bad_ids=list(ids))
            # 4c — значимые слова
            src_stems = _stems(src, 4)
            for w in words:
                lw = w.lower().replace("ё", "е")
                if len(lw) >= 5 and not lw.isdigit() and lw not in _FUNC_WORDS and _stem(lw, 4) not in src_stems:
                    return Check(False, "4c", bad_ids=list(ids), detail=f"word_len={len(lw)}")
            used.extend(ids)
    return Check(True, used_ids=sorted(set(used)))


# ── сборка текста ────────────────────────────────────────────────────────────

def _lead(types: Sequence[str]) -> str:
    t = tuple(types or ())
    if t == ("dislikes",):
        return " не любит"
    if t == ("likes",):
        return " любит"
    if t == ("important",):
        return ", важное"
    if set(t) == {"important", "dislikes"}:
        return ", важное и что не любит"
    return ""


def assemble(data: dict, groups: List[GGroup], types: Sequence[str]) -> Tuple[str, List[int]]:
    """Текст ответа и id реально показанных фраз. Излишки по размеру режем здесь: это подача, а не grounding."""
    by_n = {g.n: g for g in groups}
    paras = []
    shown: List[int] = []
    budget = KEEP_CHARS
    for b in data["blocks"]:
        g = by_n[b["group"]]
        items = []
        for ln in b["lines"]:
            t = ln["t"].strip().rstrip(".,;")
            if not t:
                continue
            if len(items) >= KEEP_LINES or (items and budget - len(t) < 0):
                break
            items.append(t)
            budget -= len(t)
            shown.extend(ln["ids"])
        if not items:
            continue
        body = ", ".join(items)
        if g.label:
            paras.append(f"<b>{escape(g.label)}</b>{escape(_lead(types))}: {escape(body)}.")
        else:
            paras.append(escape(body[:1].upper() + body[1:]) + ".")
    return "\n\n".join(paras), sorted(set(shown))


def source_line(groups: List[GGroup], used: Sequence[int]) -> str:
    """«по фактам из TG/VK, 2024–2025» — детерминированно из данных использованных фактов."""
    ids = set(used)
    facts = [f for g in groups for f in g.facts if f.id in ids]
    plats = sorted({f.platform.upper() for f in facts if f.platform in ("tg", "vk")})
    manual = any(f.source == "manual" and not f.platform for f in facts)
    years = sorted({f.year for f in facts if f.year})
    span = ""
    if years:
        span = years[0] if years[0] == years[-1] else f"{years[0]}–{years[-1]}"
    if plats:
        s = "по фактам из " + "/".join(plats)
    elif manual:
        s = "добавлено вручную"
    else:
        s = "по сохранённым фактам"
    return f"<i>{s}{', ' + span if span else ''}</i>"


# ── вызов, логирование, счётчик ──────────────────────────────────────────────

def _record(bot: str, route: str, o: Outcome) -> None:
    """Строка в memory_answer_log (счётчик успех/fallback по дням). Сбой записи не мешает ответу.
    MEMORY_ANSWER_LOG=0 — не писать (eval-скрипт, чтобы прогоны не портили дневную статистику)."""
    if os.environ.get("MEMORY_ANSWER_LOG", "1").strip().lower() in ("0", "false", "off", "no"):
        return
    try:
        with get_engine().begin() as c:
            c.execute(sa.text(
                "INSERT INTO memory_answer_log (bot, route, outcome, reason, fact_ids, n_facts, latency_ms, out_chars, "
                "in_tokens, out_tokens, model) VALUES (:b, :r, :o, :re, :ids, :n, :l, :oc, :it, :ot, :m)"),
                {"b": bot, "r": route, "o": "ok" if o.ok else "fallback", "re": o.reason or None,
                 "ids": o.bad_ids if (not o.ok and o.bad_ids) else o.used_ids, "n": o.n_facts,
                 "l": o.latency_ms, "oc": o.out_chars, "it": o.in_tokens, "ot": o.out_tokens, "m": model()})
    except Exception as e:
        logger.warning("memory_answer_log write failed: %s", e)


def _parse(raw: str):
    cleaned = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


async def generate(result, query: str, *, arcana: bool = False, trace: Optional[Trace] = None) -> Outcome:
    """Сгенерировать ответ. ok=False → вызывающий показывает прежний список (причина уже залогирована)."""
    def _t(step: str, data: dict) -> None:
        if trace:
            try:
                trace(step, data)
            except Exception:
                pass

    bot = "arcana" if arcana else "nexus"
    groups = build_groups(result)
    n_facts = sum(len(g.facts) for g in groups)
    o = Outcome(ok=False, n_facts=n_facts)
    if n_facts < 2:
        o.reason = "skipped"
        return o
    all_ids = [f.id for g in groups for f in g.facts]
    _t("gen_input", {"ids": all_ids, "groups": len(groups), "chars": sum(len(f.fact) for g in groups for f in g.facts)})
    voice = ("Бот — Arcana, говорит в женском роде." if arcana else "Бот — Nexus, говорит в мужском роде.")
    prompt = build_prompt(query, groups, result.types)
    t0 = time.monotonic()
    raw = ""
    usage: dict = {}
    try:
        raw = await asyncio.wait_for(
            ask_claude(prompt=prompt, system=SYSTEM + "\n" + voice, model=model(), max_tokens=700,
                       temperature=0, thinking="off", usage_out=usage),
            timeout=GEN_TIMEOUT)
    except asyncio.TimeoutError:
        o.reason = "timeout"
    except Exception as e:                                # pragma: no cover — ask_claude сам гасит APIError
        o.reason = "api_error"
        logger.warning("memory_answer: LLM call failed: %s", e)
    o.latency_ms = int((time.monotonic() - t0) * 1000)
    o.out_chars = len(raw or "")
    o.in_tokens, o.out_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    if not o.reason and not (raw or "").strip():
        o.reason = "api_error"
    if not o.reason:
        try:
            data = _parse(raw)
        except Exception:
            data = None
            o.reason, o.check = "check_1", "1"
        if data is not None:
            chk = validate(data, groups)
            if chk.ok:
                body, shown = assemble(data, groups, result.types)
                if body.strip():
                    o.ok = True
                    o.used_ids = shown
                    o.text = body + "\n\n" + source_line(groups, shown)
                else:
                    o.reason, o.check = "check_6", "6"
            else:
                o.reason, o.check, o.bad_ids, o.detail = f"check_{chk.check}", chk.check, chk.bad_ids, chk.detail
    if o.ok:
        logger.info("memory_answer ok: route=%s bot=%s facts=%d used=%s latency=%dms tokens=%d/%d", result.route, bot,
                    n_facts, o.used_ids, o.latency_ms, o.in_tokens, o.out_tokens)
        _t("gen_output", {"ok": True, "used_ids": o.used_ids, "chars": len(o.text)})
    else:
        logger.warning("memory_answer fallback: reason=%s check=%s detail=%s ids=%s route=%s bot=%s facts=%d",
                       o.reason, o.check or "-", o.detail or "-", o.bad_ids or all_ids, result.route, bot, n_facts)
        _t("gen_fallback", {"reason": o.reason, "check": o.check, "ids": o.bad_ids})
    await asyncio.to_thread(_record, bot, result.route, o)
    return o
