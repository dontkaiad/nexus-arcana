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
  5  формат: пункт (тема+пояснение) длиннее max(120, длина цитируемых фактов + 40) символов или > 30 пунктов (обычные излишки не ошибка: при сборке режем до
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

def model() -> str:
    """Модель генерации: MEMORY_ANSWER_MODEL (прежнее имя переменной) → роль memory_answer
    (MODEL_MEMORY_ANSWER / по умолчанию Haiku 5.5, см. core.models)."""
    from core import models
    return os.environ.get("MEMORY_ANSWER_MODEL", "").strip() or models.model_for("memory_answer")


GEN_TIMEOUT = 8.0
MAX_PER_GROUP = 20
MAX_FACTS = 40
QUOTE_LEN = 160
MAX_LINES_PER_BLOCK = 30        # «абсурдно много» — формат нарушен; обычные излишки аккуратно режутся при сборке
MAX_LINE_CHARS = 120            # тема + пояснение длиннее — это не пункт, а пересказ (см. также длину фактов)
KEEP_LINES = 5                  # сколько пунктов на группу показываем
KEEP_CHARS = 450                # и сколько символов на ГРУППУ (бюджет не общий: тёзки всегда оба)
FALLBACK_FACTS = 3              # детерминированная строка группы: столько фактов дословно
FALLBACK_CUT = 70
GENERATED_ROUTES = {"direct", "hybrid", "hybrid_self", "hybrid_person", "hybrid_cat"}

Trace = Callable[[str, dict], None]

SYSTEM = (
    "Ты отвечаешь Кай (она, женский род) на вопрос по её личной памяти. Тебе даны ТОЛЬКО факты; других знаний "
    "о людях у тебя нет.\n"
    "ЗАДАЧА: сгруппируй факты каждой группы по смысловым кластерам-темам и верни для каждой группы до 5 пунктов. "
    "Пункт = тема (1–3 слова, нейтральное название кластера, без оценок) + пояснение (2–4 слова, "
    "словами самих фактов) + ids фактов этого кластера.\n"
    "ПРАВИЛА:\n"
    "1. Ничего не добавляй от себя: не оценивай, не делай выводов, не придумывай причин и примеров. Пояснение — "
    "слова из самих фактов, без синонимов. Тема только называет кластер, но не добавляет новых утверждений.\n"
    "2. Сохраняй смысл и полярность: «не любит» остаётся «не любит», «любит» — «любит», «аллергия» — «аллергия». "
    "Не заменяй слова более сильными или мягкими.\n"
    "3. Каждый пункт указывает ids ТОЛЬКО фактов своего кластера из своей группы. Факты каждого человека — отдельный "
    "блок; людей из разных групп не смешивай. Каждая группа должна получить хотя бы один пункт.\n"
    "4. Не больше 5 пунктов на группу. Когда фактов много — выбери самые частые и важные кластеры; остальное "
    "пользователь откроет кнопкой «Все факты».\n"
    "5. Имена, числа и даты бери только из фактов. id в текст не пиши.\n"
    "6. Если ни один факт не отвечает на вопрос — верни {\"blocks\": []}.\n"
    "Верни ТОЛЬКО JSON без markdown: "
    "{\"blocks\":[{\"group\":1,\"lines\":[{\"t\":\"тема\",\"n\":\"пояснение\",\"ids\":[1,2]}]}]}"
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
    data: Optional[dict] = None          # ответ после отбрасывания необоснованных фраз
    dropped: List[str] = field(default_factory=list)   # номера проверок отброшенных фраз


def _text_check(t: str, src: str, label_stems: set, strict_words: bool) -> Tuple[str, str]:
    """Проверки текста против цитируемых фактов: 4a числа, 4b имена с заглавной, 4c значимые слова (только для
    strict_words — пояснение; тема лишь называет кластер, её слова не обязаны быть в фактах)."""
    if any(n not in set(_NUM.findall(src)) for n in _NUM.findall(t)):
        return "4a", ""
    words = _WORD.findall(t)
    src4 = _stems(src, 4) | label_stems
    for w in words[1:]:
        if len(w) >= 2 and w[0].isupper() and not w.isdigit() and _stem(w, 4) not in src4:
            return "4b", ""
    if strict_words:
        src_stems = _stems(src, 4)
        for w in words:
            lw = w.lower().replace("ё", "е")
            if len(lw) >= 5 and not lw.isdigit() and lw not in _FUNC_WORDS and _stem(lw, 4) not in src_stems:
                return "4c", f"word_len={len(lw)}"
    return "", ""


def _line_check(theme: str, note: str, ids: List[int], g: GGroup, label_stems: set) -> Tuple[str, str]:
    """Проверки 4a–4c одного пункта. ('', '') — обоснован; иначе (номер проверки, detail)."""
    src = " ".join(g.by_id()[i].fact for i in ids)
    chk, d = _text_check(theme, src, label_stems, strict_words=False)
    if chk:
        return chk, d
    if note:
        return _text_check(note, src, label_stems, strict_words=True)
    return "", ""


def validate(data, groups: List[GGroup], has_input: bool = True) -> Check:
    """Формат / чужие id / размер — нарушение ВСЕГО ответа (1, 2, 5). Фраза без опоры (3, 4a–4c) отбрасывается
    ОДНА: оставшиеся фразы целиком обоснованы, так что показывать их безопасно. Не осталось ни одной → fallback
    с номером проверки первой отброшенной фразы. Отфильтрованный ответ — в Check.data, число отброшенных — dropped."""
    by_n = {g.n: g for g in groups}
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        return Check(False, "1")
    if not data["blocks"]:
        return Check(False, "6") if has_input else Check(True)
    kept_blocks: List[dict] = []
    dropped: List[Tuple[str, List[int], str]] = []
    for b in data["blocks"]:
        if not isinstance(b, dict) or not isinstance(b.get("lines"), list) or not isinstance(b.get("group"), int):
            return Check(False, "1")
        g = by_n.get(b["group"])
        if g is None:
            return Check(False, "1")
        if len(b["lines"]) > MAX_LINES_PER_BLOCK:
            return Check(False, "5", detail=f"lines={len(b['lines'])}")
        label_stems = _stems(g.label, 4)
        kept = []
        for ln in b["lines"]:
            if (not isinstance(ln, dict) or not isinstance(ln.get("t"), str) or not isinstance(ln.get("ids"), list)
                    or not isinstance(ln.get("n", ""), str)):
                return Check(False, "1")
            ids = ln["ids"]
            if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                return Check(False, "1")
            foreign = [i for i in ids if i not in g.ids()]
            if foreign:                                       # чужой id — признак, что модель потеряла опору: всё прочь
                return Check(False, "2", bad_ids=foreign)
            t, n = ln["t"].strip(), (ln.get("n") or "").strip()
            # пункт не может быть заметно длиннее фактов, которые пересказывает
            cited = sum(len(g.by_id()[i].fact) for i in ids if i in g.ids())
            if len(t) + len(n) > max(MAX_LINE_CHARS, cited + 40):
                return Check(False, "5", detail=f"line_chars={len(t) + len(n)}")
            if not ids or not t:
                dropped.append(("3", [], ""))
                continue
            chk, detail = _line_check(t, n, ids, g, label_stems)
            if chk:
                dropped.append((chk, list(ids), detail))
                continue
            kept.append(ln)
        if kept:
            kept_blocks.append({"group": b["group"], "lines": kept})
    if not kept_blocks:
        first = dropped[0] if dropped else ("6", [], "")
        return Check(False, first[0], bad_ids=first[1], detail=first[2])
    used = sorted({i for kb in kept_blocks for ln in kb["lines"] for i in ln["ids"]})
    return Check(True, used_ids=used, data={"blocks": kept_blocks}, dropped=[d[0] for d in dropped])


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


def _fallback_group(g: GGroup, types: Sequence[str]) -> Tuple[str, List[int]]:
    """Детерминированная строка группы, когда от модели по ней ничего не осталось: первые факты ДОСЛОВНО
    (без LLM, поэтому всегда обоснованы). Тёзки обязаны присутствовать оба — хотя бы так."""
    shown = g.facts[:FALLBACK_FACTS]
    rows = ["• " + escape(f.fact if len(f.fact) <= FALLBACK_CUT else f.fact[:FALLBACK_CUT].rstrip() + "…") for f in shown]
    rest = len(g.facts) - len(shown)
    if rest > 0:
        rows.append(f"<i>и ещё {rest} — в «Все факты»</i>")
    head = f"<b>{escape(g.label)}</b>{escape(_lead(types))}:\n" if g.label else ""
    return head + "\n".join(rows), [f.id for f in shown]


def assemble(data: dict, groups: List[GGroup], types: Sequence[str]) -> Tuple[str, List[int]]:
    """Текст ответа и id реально показанных пунктов. Формат группы:
        <b>Имя</b> не любит:
        • <b>тема</b> — пояснение
    До KEEP_LINES пунктов и KEEP_CHARS символов НА ГРУППУ (излишки режем здесь — это подача, не grounding).
    Группа, по которой модель ничего не вернула или все пункты не прошли проверку, не пропадает: для неё
    детерминированная строка из первых фактов дословно."""
    by_group = {b["group"]: b for b in data["blocks"]}
    paras: List[str] = []
    shown: List[int] = []
    for g in groups:
        b = by_group.get(g.n)
        bullets: List[str] = []
        budget = KEEP_CHARS
        for ln in (b["lines"] if b else []):
            t, n = ln["t"].strip().rstrip(".,;"), (ln.get("n") or "").strip().rstrip(".,;")
            if not t:
                continue
            row = f"• <b>{escape(t)}</b>" + (f" — {escape(n)}" if n else "")
            if len(bullets) >= KEEP_LINES or (bullets and budget - len(t) - len(n) < 0):
                break
            bullets.append(row)
            budget -= len(t) + len(n)
            shown.extend(ln["ids"])
        if bullets:
            head = f"<b>{escape(g.label)}</b>{escape(_lead(types))}:\n" if g.label else ""
            paras.append(head + "\n".join(bullets))
        else:
            text, ids = _fallback_group(g, types)
            paras.append(text)
            shown.extend(ids)
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

    async def _attempt(timeout: float) -> None:
        o.ok, o.text, o.used_ids, o.reason, o.check, o.bad_ids, o.detail = False, "", [], "", "", [], ""
        raw = ""
        usage: dict = {}
        try:
            raw = await asyncio.wait_for(
                ask_claude(prompt=prompt, system=SYSTEM + "\n" + voice, model=model(), role="memory_answer", max_tokens=700,
                           temperature=0, thinking="off", usage_out=usage),
                timeout=timeout)
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
                    body, shown = assemble(chk.data or data, groups, result.types)
                    if body.strip():
                        o.ok = True
                        o.used_ids = shown
                        if chk.dropped:
                            o.reason = "dropped:" + ",".join(chk.dropped)       # успех, но часть фраз отброшена (виден в журнале)
                            o.detail = f"dropped={len(chk.dropped)}"
                        o.text = body + "\n\n" + source_line(groups, shown)
                    else:
                        o.reason, o.check = "check_6", "6"
                else:
                    o.reason, o.check, o.bad_ids, o.detail = f"check_{chk.check}", chk.check, chk.bad_ids, chk.detail

    await _attempt(GEN_TIMEOUT)
    # Битый формат (check_1) — обычный сбой генерации без температуры: один повтор, если остался запас по времени.
    left = GEN_TIMEOUT - (time.monotonic() - t0)
    if not o.ok and o.check == "1" and left > 3.0:
        logger.info("memory_answer: bad format, retry once (%.1fs left)", left)
        await _attempt(left)
    o.latency_ms = int((time.monotonic() - t0) * 1000)
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
