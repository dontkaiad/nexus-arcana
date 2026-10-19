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

from tools.people_import.attribute_quotes import expected_about, first_person_by_kai, verify
from tools.people_import.chats import Chat, Msg, load_chats
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
    partner_is_self = sb.is_self(name=chat.title, tg_id=chat.id if chat.source == "tg" else None,
                                 vk_id=chat.id if chat.source == "vk" else None)
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
        rec = {"name": SELF_NAME if about == "kai" else chat.title, "aliases": [], "fact": str(it["fact"]).strip(),
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


async def run_full(chats, fresh: bool, resume: bool = False) -> None:
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
    done = _done_batches() if resume else set()
    if not resume and CHECKPOINT.exists():
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
    a = ap.parse_args()
    ch = load_chats(a.tg, a.vk)
    if a.sample:
        asyncio.run(run_sample(ch, a.sample, a.seed, DATA_DIR / "sample_facts.jsonl"))
    else:
        asyncio.run(run_full(ch, a.fresh and not a.resume, a.resume))
