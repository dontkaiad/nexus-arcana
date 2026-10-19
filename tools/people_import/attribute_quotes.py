"""Детерминированная проверка цитат (без LLM) + аудит атрибуции.

  verify(chat, quote, lo, hi)  — цитата должна найтись в ЭТОМ чате (сначала в окне lo..hi,
                                 потом по всему чату); возвращает автора/дату/чат или None.
  python3 -m tools.people_import.attribute_quotes --tg … --vk …
                               — аудит staging: доля фактов по автору цитаты, первое лицо
                                 от Кай, цитаты не из чата человека. Только агрегаты;
                                 построчно — data/attribution.jsonl (в .gitignore).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tools.people_import.chats import Chat, Msg, load_chats, nz
from tools.people_import.common import DATA_DIR, fact_hash, load_staging

_FIRST = re.compile(r"(?<!\w)(я|мне|меня|мной|мой|моя|моё|мои|моей|мою|мы|нам|нас|наш|наша)(?!\w)", re.I)
_SECOND = re.compile(r"(?<!\w)(ты|тебе|тебя|тобой|твой|твоя|твоё|твои|твоей|твою|вы|вам|вас)(?!\w)", re.I)


def first_person_by_kai(quote: str, author: str) -> bool:
    """Цитата Кай в первом лице без обращения на «ты» — это она говорит о СЕБЕ."""
    return author == "kai" and bool(_FIRST.search(quote or "")) and not _SECOND.search(quote or "")


def expected_about(author: str, quote: str) -> Optional[str]:
    """О ком говорит цитата по местоимениям (детерминированно): Кай «я…» → о Кай, Кай «ты…» →
    о собеседнике; собеседник «я…» → о собеседнике, «ты…» → о Кай. Без местоимений или с обоими
    — None (нельзя решить, верим LLM)."""
    first, second = bool(_FIRST.search(quote or "")), bool(_SECOND.search(quote or ""))
    if first == second:
        return None
    if author == "kai":
        return "kai" if first else "partner"
    if author == "partner":
        return "partner" if first else "kai"
    return None


def segments(quote: str) -> List[str]:
    """Цитата → нормализованные сегменты (по строкам, без префикса «Имя: »)."""
    segs = []
    for part in re.split(r"\n+", quote or ""):
        part = re.sub(r"^[^:\n]{1,40}:\s*", "", part)
        n = nz(part)
        if len(n) >= 8:
            segs.append(n)
    if not segs:
        n = nz(quote or "")
        if len(n) >= 8:
            segs = [n]
    return segs


def locate(msgs: List[Msg], quote: str, lo: int = 0, hi: Optional[int] = None) -> List[int]:
    """Индексы сообщений в [lo, hi), в которых содержится цитата (или сегмент цитаты)."""
    segs = segments(quote)
    qn = nz(quote)
    hits = []
    for i in range(lo, len(msgs) if hi is None else min(hi, len(msgs))):
        t = msgs[i].n
        if t and (any(s in t for s in segs) or (len(t) >= 12 and t in qn)):
            hits.append(i)
    return hits


def author_of(msgs: List[Msg], idx: List[int]) -> str:
    who = {msgs[i].author for i in idx}
    if not who:
        return "unknown"
    if len(who) == 1:
        return next(iter(who)) if next(iter(who)) != "other" else "unknown"
    return "mixed"


def verify(chat: Chat, quote: str, lo: int = 0, hi: Optional[int] = None) -> Optional[Dict]:
    """Цитата найдена в этом чате? Окно приоритетно (там реплика, на которую опирался LLM)."""
    for a, b in ((lo, hi), (0, None)):
        idx = locate(chat.msgs, quote, a, b)
        if idx:
            return {"author": author_of(chat.msgs, idx), "date": chat.msgs[idx[0]].date,
                    "chat_id": chat.id, "platform": chat.source, "msg_index": idx[0]}
    return None


# ── аудит staging ───────────────────────────────────────────────────────────

def unique_facts(rows: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for r in rows:
        if r["type"] == "birthday":
            continue
        ident = ("tg", r.get("tg_id")) if r.get("tg_id") else ("vk", r.get("vk_id")) if r.get("vk_id") else ("name", r["name"].lower())
        k = (ident, r["type"], fact_hash(r["fact"]))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def run(tg_path: str, vk_path: str, out_path: Path) -> None:
    chats = load_chats(tg_path, vk_path)
    titles = {src: Counter(c.title for c in cs.values()) for src, cs in chats.items()}
    facts = unique_facts(load_staging())
    stat, author_c, results = Counter(), Counter(), []
    for r in facts:
        src = r["source"]
        cid = r.get("tg_id") if src == "tg" else r.get("vk_id")
        how = "id"
        if not cid:
            cands = [i for i, c in chats[src].items() if c.title == r["name"] and titles[src][c.title] == 1]
            cid, how = (cands[0], "name") if cands else (None, "none")
        own = chats[src].get(cid)
        m = verify(own, r.get("quote", "")) if own else None
        a = m["author"] if m else "unknown"
        elsewhere = []
        if not m and len(nz(r.get("quote", ""))) >= 20:
            seg = (segments(r.get("quote", "")) or [""])[0]
            for s2 in ("tg", "vk"):
                for i, c in chats[s2].items():
                    if (s2, i) != (src, cid) and seg and any(seg in x.n for x in c.msgs):
                        elsewhere.append((s2, i))
                        break
        stat["person_by_" + how] += 1
        author_c[a] += 1
        results.append({"source": src, "person_chat": cid, "type": r["type"], "hash": fact_hash(r["fact"]),
                        "match": how, "author": a, "date": m["date"] if m else "",
                        "found_in_own_chat": bool(m), "found_elsewhere": elsewhere,
                        "first_person_kai": first_person_by_kai(r.get("quote", ""), a)})
    with out_path.open("w", encoding="utf-8") as f:
        for x in results:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    n = max(len(results), 1)
    pct = lambda k: "%d (%.1f%%)" % (k, 100.0 * k / n)
    print("фактов (уникальных, без ДР): %d" % len(results))
    print("автор цитаты:", {k: pct(v) for k, v in author_c.most_common()})
    print("первое лицо от Кай:", pct(sum(x["first_person_kai"] for x in results)))
    print("не найдена в своём чате:", pct(sum(not x["found_in_own_chat"] for x in results)),
          "| из них найдена в другом чате:", pct(sum(1 for x in results if not x["found_in_own_chat"] and x["found_elsewhere"])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg", default="")
    ap.add_argument("--vk", default="")
    ap.add_argument("--out", default=str(DATA_DIR / "attribution.jsonl"))
    a = ap.parse_args()
    run(a.tg, a.vk, Path(a.out))
