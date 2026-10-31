"""Факты о собеседниках из переписок (TG JSON + VK HTML) → Haiku → пост-проверка → staging.

Атрибуция (после аудита: у 47% фактов автор цитаты — сама Кай, у VK терялся автор):
  • каждая реплика подписана однозначно: «Кай:» / «Собеседник:»;
  • извлекаются ТОЛЬКО факты о Собеседнике; реплики Кай — контекст
    («у тебя же аллергия на орехи» → факт о собеседнике, «у меня аллергия» → не факт);
  • quote — дословная реплика; пост-проверка БЕЗ LLM: цитата обязана найтись в этом чате,
    иначе факт выбрасывается (галлюцинация/пересказ). Из найденного сообщения берутся
    автор (quote_author), дата и чат — они сохраняются у факта.

  python3 -m tools.people_import.extract_facts --tg data/result.json --vk data/vk_archive --sample 30
  python3 -m tools.people_import.extract_facts --tg … --vk … --fresh      # полный прогон
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tools.people_import.attribute_quotes import expected_about, first_person_by_kai, locate, verify
from tools.people_import.chats import Chat, Msg, classify_chat, load_chats
from tools.people_import.common import DATA_DIR, STAGING, TYPES, append_staging, load_staging, write_staging
from tools.people_import.selfblock import SelfBlock, load_selfblock

from core.claude_client import ask_claude  # retry-обёртка (правило репо)

PREFILTER = re.compile(
    r"люблю|обожаю|ненавижу|не ем|аллерги|не пью|\bдр\b|день рождения|подари|мечтаю|бесит",
    re.IGNORECASE,
)
WINDOW = 2
BATCH = 12
CONCURRENCY = 4
LABEL = {"kai": "Кай", "partner": "Собеседник", "other": "Другой"}

SYSTEM = (
    "Ты извлекаешь устойчивые факты из фрагментов личной переписки между Кай (владелец переписки) "
    "и Собеседником. Каждая реплика подписана: «Кай:» или «Собеседник:» (иногда «Другой:»).\n"
    "ПРАВИЛА:\n"
    "1. У каждого факта поле about: «partner» — факт о Собеседнике, «kai» — факт о Кай.\n"
    "2. Кто о ком: если Кай говорит о СЕБЕ («я люблю…», «у меня аллергия…») → about=kai. Если Кай говорит "
    "о Собеседнике («у тебя же аллергия на орехи», «ты не ешь мясо») → about=partner. Если Собеседник говорит "
    "о себе («я люблю…») → about=partner. Если Собеседник говорит о Кай («ты же любишь…») → about=kai.\n"
    "3. Факты о третьих лицах не извлекай. Не извлекай разовые планы, настроение, бытовуху.\n"
    "4. Факт: вкусы, аллергии, здоровье, день рождения, мечты, что бесит, семья, питомцы, работа, что нельзя "
    "дарить или говорить.\n"
    "5. fact — коротко, в третьем лице, по-русски, без имени («любит аниме»). type: likes | dislikes | birthday | "
    "important | other. important — то, что важно знать, чтобы не навредить и не ошибиться: аллергии и здоровье, "
    "больные темы, дети и питомцы, работа, что нельзя дарить или говорить. Не ест/не пьёт по вкусу → dislikes; "
    "мечты/хочет в подарок → likes.\n"
    "6. quote — ДОСЛОВНАЯ реплика (одна, без подписи «Кай:»/«Собеседник:»), из которой следует факт. Не пересказывай "
    "и не склеивай реплики. speaker — автор этой реплики: «Кай» или «Собеседник».\n"
    "Ответ — ТОЛЬКО JSON-массив: [{\"i\": <номер фрагмента>, \"about\": \"partner|kai\", \"fact\": \"...\", "
    "\"type\": \"...\", \"quote\": \"...\", \"speaker\": \"Кай|Собеседник\"}]. Нет фактов — []."
)


# ── окна ────────────────────────────────────────────────────────────────────

def window_ranges(msgs: List[Msg]) -> List[Tuple[int, int]]:
    """Диапазоны [lo, hi) вокруг реплик с триггерами; пересекающиеся склеиваются."""
    ranges: List[List[int]] = []
    for i, m in enumerate(msgs):
        if m.text and PREFILTER.search(m.text):
            lo, hi = max(0, i - WINDOW), min(len(msgs), i + WINDOW + 1)
            if ranges and lo <= ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], hi)
            else:
                ranges.append([lo, hi])
    return [(a, b) for a, b in ranges]


def render_window(msgs: List[Msg], lo: int, hi: int) -> str:
    """Каждая реплика — «Кай: …» / «Собеседник: …» (одна строка, переносы внутри реплики убраны)."""
    return "\n".join("%s: %s" % (LABEL.get(m.author, "Другой"), " ".join(m.text.split()))
                     for m in msgs[lo:hi] if m.text)


# ── разбор ответа и пост-проверка ───────────────────────────────────────────

def _parse_json(raw: str) -> List[Dict]:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        return []


SELF_NAME = "Кай"


def build_records(chat: Chat, ranges: List[Tuple[int, int]], items: List[Dict], stats: Counter,
                  sb: Optional[SelfBlock] = None) -> List[Dict]:
    """Ответ LLM → записи staging. Выбрасываем: цитата не найдена в чате; «о ком» не сходится с
    автором и местоимениями цитаты (Кай «я…» не может быть фактом о собеседнике). Факты о Кай
    (about=kai) остаются — они пойдут в карточку «Я»."""
    sb = sb or load_selfblock()
    kind, partner_name = classify_chat(chat, sb)      # self | person (+ имя: title → имя из реплик → «Удалённый аккаунт»)
    partner_is_self = kind == "self"
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("fact") or not it.get("quote"):
            stats["malformed"] += 1
            continue
        stats["items"] += 1
        about = it.get("about") if it.get("about") in ("kai", "partner") else "partner"
        i = it.get("i")
        lo, hi = ranges[i] if isinstance(i, int) and 0 <= i < len(ranges) else (0, None)
        found = verify(chat, str(it["quote"]), lo, hi)
        if not found:
            stats["quote_not_found"] += 1
            continue
        quote = str(it["quote"]).strip()
        if about == "partner":                       # «сырой» показатель качества промпта (до проверки)
            stats["raw_partner"] += 1
            stats["raw_partner_kai_first_person"] += first_person_by_kai(quote, found["author"])
        exp = expected_about(found["author"], quote)
        if partner_is_self:
            about = "kai"                            # оба аккаунта владельца — это она сама
        elif exp is not None and exp != about:
            stats["about_mismatch"] += 1
            continue
        t = it.get("type") if it.get("type") in TYPES else "other"
        rec = {"name": SELF_NAME if about == "kai" else partner_name, "aliases": [], "fact": str(it["fact"]).strip(),
               "type": t, "quote": quote[:300], "about": about, "source": chat.source, "origin": "chat",
               "source_chat_id": chat.id, "source_message_date": found["date"] or None,
               "quote_author": found["author"],
               "tg_id": chat.id if chat.source == "tg" and about == "partner" else None,
               "vk_id": chat.id if chat.source == "vk" and about == "partner" else None}
        stats["kept"] += 1
        stats["kept_" + about] += 1
        out.append(rec)
    return out


class LLMUnavailable(RuntimeError):
    """Несколько вызовов подряд вернули пусто (нет баланса/кредитов, лимиты, сеть)."""


MAX_FAILED = 5          # столько пустых ответов — и прогон останавливается, не тратя время впустую
CHECKPOINT = DATA_DIR / "extract_done.txt"


def _done_batches() -> set:
    return set(CHECKPOINT.read_text(encoding="utf-8").split("\n")) if CHECKPOINT.exists() else set()


async def extract_windows(chat: Chat, ranges: List[Tuple[int, int]], stats: Counter,
                          sb: Optional[SelfBlock] = None, done: Optional[set] = None,
                          sink=None) -> List[Dict]:
    """`ask_claude` при ошибке API молча возвращает "" — раньше такие пачки терялись незаметно
    (на проде кончился баланс → 27% вызовов пропало, а прогон «успешно» закончился). Теперь пустой
    ответ считается сбоем, пачка не отмечается выполненной, а после MAX_FAILED сбоев прогон падает."""
    out: List[Dict] = []
    for s in range(0, len(ranges), BATCH):
        if stats["llm_failed"] >= MAX_FAILED:
            raise LLMUnavailable("пустые ответы LLM: %d — проверь баланс/ключ" % stats["llm_failed"])
        mark = "%s:%s:%d" % (chat.source, chat.id, s)
        if done is not None and mark in done:
            stats["skipped_done"] += 1
            continue
        part = ranges[s:s + BATCH]
        prompt = "\n\n".join("### Фрагмент %d\n%s" % (i, render_window(chat.msgs, lo, hi)[:1500])
                             for i, (lo, hi) in enumerate(part))
        stats["calls"] += 1
        raw = await ask_claude(prompt, system=SYSTEM, max_tokens=2000, temperature=0)
        if not (raw or "").strip():
            stats["llm_failed"] += 1
            continue
        recs = build_records(chat, part, _parse_json(raw), stats, sb)
        out += recs
        if sink is not None:
            sink(recs)                               # сначала сохраняем записи пачки…
        if done is not None:                         # …и только потом отмечаем её выполненной
            done.add(mark)
            with CHECKPOINT.open("a", encoding="utf-8") as f:
                f.write(mark + "\n")
    return out


# ── какие пачки потерялись ───────────────────────────────────────────────────

def batch_marks(chats: Dict[str, Dict[int, Chat]]) -> List[Tuple[str, Chat, int, List[Tuple[int, int]]]]:
    """Все пачки в порядке обработки: (метка, чат, start, диапазоны окон). Тот же порядок и то же
    разбиение, что у run_full, — поэтому метки стабильны между запусками."""
    out = []
    for cs in chats.values():
        for c in cs.values():
            r = window_ranges(c.msgs)
            for s in range(0, len(r), BATCH):
                out.append(("%s:%s:%d" % (c.source, c.id, s), c, s, r[s:s + BATCH]))
    return out


def find_failed_batches(chats: Dict[str, Dict[int, Chat]], rows: List[Dict]) -> Dict:
    """Пачки БЕЗ единой записи в staging. Чекпойнта у прогона, в котором кончился баланс, не было,
    но пачка, давшая факт, определяется по цитате: найдена в окне пачки → вызов удался.
    Остаются пачки без записей: упавшие вызовы + удавшиеся, но пустые (их мало; перезапуск
    идемпотентен — дубли отсекает staging, пустые снова вернутся пустыми)."""
    by_chat: Dict[Tuple[str, int], List[Dict]] = {}
    for r in rows:
        if r.get("origin") == "chat" and r.get("source_chat_id"):
            by_chat.setdefault((r["source"], int(r["source_chat_id"])), []).append(r)
    all_batches = batch_marks(chats)
    has: Dict[str, bool] = {}
    for mark, chat, s, part in all_batches:
        hit = False
        for r in by_chat.get((chat.source, chat.id), []):
            if any(locate(chat.msgs, r.get("quote", ""), lo, hi) for lo, hi in part):
                hit = True
                break
        has[mark] = hit
    empty = [m for m, *_ in all_batches if not has[m]]
    order = {m: i for i, (m, *_) in enumerate(all_batches)}
    last_ok = max((order[m] for m in has if has[m]), default=-1)
    return {"total": len(all_batches), "with_records": sum(has.values()), "empty": empty,
            "empty_after_last_ok": [m for m in empty if order[m] > last_ok],
            "last_ok_index": last_ok}


# Цены Haiku 4.5, $ за 1M токенов (вход / выход); токены считаем с запасом: кириллица ≈ 2.2 симв./токен.
PRICE_IN, PRICE_OUT = 1.0, 5.0
CHARS_PER_TOKEN = 2.2
OUT_TOKENS_PER_CALL = 800        # по факту прошлого прогона: ~6.6 факта на вызов × ~90 токенов + служебное


def estimate_cost(chats, marks) -> Dict:
    want = set(marks)
    in_chars = n = 0
    for m, chat, s, part in batch_marks(chats):
        if m in want:
            n += 1
            in_chars += len(SYSTEM) + sum(len("### Фрагмент %d\n" % i) + len(render_window(chat.msgs, lo, hi)[:1500])
                                           for i, (lo, hi) in enumerate(part))
    tin, tout = in_chars / CHARS_PER_TOKEN, n * OUT_TOKENS_PER_CALL
    return {"вызовов": n, "вход, токенов": int(tin), "выход, токенов": tout,
            "стоимость $ (верхняя оценка)": round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 2)}


def print_failed_report(chats) -> None:
    rep = find_failed_batches(chats, load_staging())
    (DATA_DIR / "failed_batches.json").write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    print("пачек всего (= число вызовов прошлого прогона):", rep["total"])
    print("пачек с записями в staging (вызов удался):", rep["with_records"])
    print("пачек без записей:", len(rep["empty"]), "| из них после последней успешной:", len(rep["empty_after_last_ok"]))
    print("оценка для всех «без записей»:", estimate_cost(chats, rep["empty"]))


# ── метрики ─────────────────────────────────────────────────────────────────

def metrics(records: List[Dict], stats: Counter) -> Dict:
    kept = len(records)
    cand = stats["items"]
    pct = lambda a, b: "%d (%.1f%%)" % (a, 100.0 * a / max(b, 1))
    partner = [r for r in records if r["about"] == "partner"]
    fp_kept = sum(first_person_by_kai(r["quote"], r["quote_author"]) for r in partner)
    return {
        "окон": stats["windows"], "вызовов LLM": stats["calls"], "фактов от LLM": cand,
        "RAW (до проверки): about=partner с цитатой Кай в первом лице": pct(stats["raw_partner_kai_first_person"], stats["raw_partner"]),
        "выброшено: цитата не найдена в чате": pct(stats["quote_not_found"], cand),
        "выброшено: about не сходится с автором/местоимениями": pct(stats["about_mismatch"], cand),
        "оставлено": kept, "из них about=partner / about=kai": "%d / %d" % (stats["kept_partner"], stats["kept_kai"]),
        "автор цитаты (оставленные)": {k: pct(v, kept) for k, v in Counter(r["quote_author"] for r in records).most_common()},
        "ПОСЛЕ проверки: about=partner с цитатой Кай в первом лице": pct(fp_kept, len(partner)),
    }


# ── прогоны ─────────────────────────────────────────────────────────────────

async def _gather(jobs, stats, sb=None, done=None, sink=None):
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(chat, ranges):
        async with sem:
            return await extract_windows(chat, ranges, stats, sb, done, sink)

    res = await asyncio.gather(*(one(c, r) for c, r in jobs))
    return [x for part in res for x in part]


def all_windows(chats: Dict[str, Dict[int, Chat]]) -> List[Tuple[Chat, Tuple[int, int]]]:
    return [(c, r) for cs in chats.values() for c in cs.values() for r in window_ranges(c.msgs)]


async def run_sample(chats, n: int, seed: int, out: Path) -> None:
    pool = all_windows(chats)
    random.Random(seed).shuffle(pool)
    pick = pool[:n]
    by_chat: Dict[Tuple[str, int], Tuple[Chat, List[Tuple[int, int]]]] = {}
    for c, r in pick:
        by_chat.setdefault((c.source, c.id), (c, []))[1].append(r)
    stats: Counter = Counter(windows=len(pick))
    recs = await _gather(list(by_chat.values()), stats, load_selfblock(getattr(chats.get('tg'), 'owner', None)))
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    print(json.dumps(metrics(recs, stats), ensure_ascii=False, indent=1))


async def run_full(chats, fresh: bool, resume: bool = False, only: Optional[set] = None) -> None:
    jobs = []
    for cs in chats.values():
        for c in cs.values():
            r = window_ranges(c.msgs)
            if r:
                jobs.append((c, r))
    stats: Counter = Counter(windows=sum(len(r) for _, r in jobs))
    if fresh and STAGING.exists():                   # ДО запуска: старое откладываем, остаются только ДР из API
        old = load_staging()
        (DATA_DIR / "people_staging.old.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in old), encoding="utf-8")
        write_staging([x for x in old if x.get("origin") == "api"])
    done = _done_batches() if (resume or only) else set()
    if only:                                         # только перечисленные пачки: остальные считаем выполненными
        done |= {m for m, *_ in batch_marks(chats)} - set(only)
        stats["windows"] = sum(len(p) for m, c, s, p in batch_marks(chats) if m in only)
    if not (resume or only) and CHECKPOINT.exists():
        CHECKPOINT.unlink()                          # новый прогон — новый чекпойнт
    added = Counter()

    def sink(recs):                                  # каждая пачка пишется в staging сразу
        added["n"] += append_staging(recs)

    recs: List[Dict] = []
    try:
        recs = await _gather(jobs, stats, load_selfblock(getattr(chats.get("tg"), "owner", None)), done, sink)
    except LLMUnavailable as e:
        print("СТОП:", e, "| выполнено пачек: %d, записей в staging добавлено: %d" % (len(done), added["n"]),
              "— после починки запусти с --resume")
        raise SystemExit(2)
    if stats["llm_failed"]:
        print("СТОП: %d пачек вернули пустой ответ — результат НЕПОЛНЫЙ; запусти с --resume" % stats["llm_failed"])
    print(json.dumps({**metrics(recs, stats), "добавлено в staging": added["n"]}, ensure_ascii=False, indent=1))
    if stats["llm_failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg", default="")
    ap.add_argument("--vk", default="")
    ap.add_argument("--sample", type=int, default=0, help="N случайных окон; staging не трогает")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fresh", action="store_true", help="staging заменить заново (ДР из API сохраняются)")
    ap.add_argument("--resume", action="store_true", help="продолжить: пропустить пачки из extract_done.txt")
    ap.add_argument("--find-failed", action="store_true", help="найти пачки без записей в staging + оценка $; API не трогает")
    ap.add_argument("--only-batches", default="", help="JSON-файл со списком меток пачек (ключ 'empty'): прогнать только их")
    a = ap.parse_args()
    ch = load_chats(a.tg, a.vk)
    if a.find_failed:
        print_failed_report(ch)
    elif a.sample:
        asyncio.run(run_sample(ch, a.sample, a.seed, DATA_DIR / "sample_facts.jsonl"))
    elif a.only_batches:
        marks = set(json.loads(Path(a.only_batches).read_text(encoding="utf-8"))["empty"])
        asyncio.run(run_full(ch, False, True, marks))
    else:
        asyncio.run(run_full(ch, a.fresh and not a.resume, a.resume))
