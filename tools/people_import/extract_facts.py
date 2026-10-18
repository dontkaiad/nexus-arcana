"""Факты о людях из переписок: TG JSON export + VK HTML архив → Haiku → staging.

Запуск:
  python3 -m tools.people_import.extract_facts --tg data/result.json --vk data/vk_archive [--max-windows 400]
Только личные чаты. Regex-префильтр → окно ±2 сообщения → батчи в Haiku.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from tools.people_import.common import TYPES, append_staging

from core.claude_client import ask_claude  # retry-обёртка (правило репо)

PREFILTER = re.compile(
    r"люблю|обожаю|ненавижу|не ем|аллерги|не пью|\bдр\b|день рождения|подари|мечтаю|бесит",
    re.IGNORECASE,
)
WINDOW = 2
BATCH = 12

SYSTEM = (
    "Ты извлекаешь устойчивые факты о человеке из фрагментов личной переписки Кай. "
    "Для каждого фрагмента указан собеседник (partner). Факт — о собеседнике (его вкусы, "
    "аллергии, ДР, мечты, что бесит), либо о ком-то, кого явно назвали по имени. "
    "Не извлекай разовые планы, настроение, бытовуху. Формулируй факт коротко, в третьем лице, "
    "по-русски. type: likes | dislikes | birthday | important | other. "
    "important — то, что важно знать, чтобы не навредить и не ошибиться: аллергии и здоровье, "
    "больные темы, дети и питомцы, работа/профессия, что нельзя дарить или говорить. "
    "Не ест/не пьёт по вкусу → dislikes; мечты/хочет в подарок → likes. quote — дословная цитата-основание. "
    "Ответ — ТОЛЬКО JSON-массив: [{\"i\": <номер фрагмента>, \"name\": \"...\", \"fact\": \"...\", "
    "\"type\": \"...\", \"quote\": \"...\"}]. Нет фактов — []."
)


# ── TG JSON ─────────────────────────────────────────────────────────────────

def _tg_text(t) -> str:
    if isinstance(t, str):
        return t
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in t)


def iter_tg(path: Path) -> Iterator[Tuple[str, int, List[Tuple[str, str]]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for chat in data.get("chats", {}).get("list", []):
        if chat.get("type") != "personal_chat" or not chat.get("name"):
            continue
        msgs = [(m.get("from") or "", _tg_text(m.get("text", "")))
                for m in chat.get("messages", []) if m.get("type") == "message"]
        yield chat["name"], chat.get("id"), msgs


# ── VK HTML ─────────────────────────────────────────────────────────────────

class _VkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.msgs: List[Tuple[str, str]] = []
        self._stack: List[str] = []
        self._in_title = False
        self._in_header = False
        self._cur_sender = ""
        self._buf: List[str] = []
        self._in_body = False
        self._depth_body = 0
        self._skip_at = None  # type: Optional[int]

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        if tag == "div" and "ui_crumb" in cls:
            self._in_title = True
        if tag == "div" and "message__header" in cls:
            self._in_header = True
        elif self._in_body and tag == "div":
            self._depth_body += 1
            if self._skip_at is None and ("kludges" in cls or "attachment" in cls):
                self._skip_at = self._depth_body
        if tag == "br" and self._in_body:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "div":
            if self._in_title:
                self._in_title = False
            elif self._in_header:
                self._in_header = False
                self._in_body = True
                self._depth_body = 1  # следующий div с телом
                self._buf = []
            elif self._in_body:
                if self._skip_at == self._depth_body:
                    self._skip_at = None
                self._depth_body -= 1
                if self._depth_body <= 0:
                    self._in_body = False
                    self.msgs.append((self._cur_sender, " ".join("".join(self._buf).split())))

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif self._in_header:
            self._cur_sender = data.split(",")[0].strip()
        elif self._in_body and self._skip_at is None:
            self._buf.append(data)


def iter_vk(root: Path) -> Iterator[Tuple[str, int, List[Tuple[str, str]]]]:
    """Архив VK: messages/<peer_id>/messages*.html (cp1251). Групповые (id>=2e9) и сообщества пропускаем."""
    msg_dir = next((p for p in root.rglob("*") if p.is_dir() and p.name.lower() == "messages"), None)
    if not msg_dir:
        return
    for d in sorted(p for p in msg_dir.iterdir() if p.is_dir() and p.name.isdigit()):
        if int(d.name) >= 2_000_000_000:
            continue
        title, msgs = "", []
        for f in sorted(d.glob("messages*.html"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0), reverse=True):
            parser = _VkParser()
            parser.feed(f.read_bytes().decode("cp1251", errors="replace"))
            title = title or parser.title
            msgs += parser.msgs
        if title and msgs:
            yield title, int(d.name), msgs


# ── окна + Haiku ────────────────────────────────────────────────────────────

def windows(msgs: List[Tuple[str, str]]) -> List[str]:
    hits = [i for i, (_, t) in enumerate(msgs) if t and PREFILTER.search(t)]
    out, last_end = [], -1
    for i in hits:
        lo, hi = max(0, i - WINDOW), min(len(msgs), i + WINDOW + 1)
        if lo <= last_end and out:  # склеиваем пересекающиеся окна
            lo = last_end + 1
            if lo >= hi:
                continue
            out[-1] += "\n" + "\n".join("%s: %s" % m for m in msgs[lo:hi] if m[1])
        else:
            out.append("\n".join("%s: %s" % m for m in msgs[lo:hi] if m[1]))
        last_end = hi - 1
    return out


def _parse_json(raw: str) -> List[Dict]:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        return []


async def extract_batch(partner: str, source: str, wins: List[str], pid: Optional[int] = None) -> List[Dict]:
    prompt = "\n\n".join("### Фрагмент %d (partner: %s)\n%s" % (i, partner, w[:1500]) for i, w in enumerate(wins))
    items = _parse_json(await ask_claude(prompt, system=SYSTEM, max_tokens=2000, temperature=0))
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("fact"):
            continue
        t = it.get("type") if it.get("type") in TYPES else "other"
        out.append({"name": (it.get("name") or partner).strip(), "aliases": [partner] if it.get("name") else [],
                    "fact": it["fact"].strip(), "type": t, "quote": (it.get("quote") or "")[:300],
                    "source": source, "origin": "chat",
                    # id собеседника относится только к самому собеседнику
                    "tg_id": pid if source == "tg" and not it.get("name") else None,
                    "vk_id": pid if source == "vk" and not it.get("name") else None})
    return out


async def run(tg: str, vk: str, max_windows: int) -> None:
    chats = []
    if tg:
        chats += [("tg", n, i, m) for n, i, m in iter_tg(Path(tg))]
    if vk:
        chats += [("vk", n, i, m) for n, i, m in iter_vk(Path(vk))]
    total_w = added = 0
    for source, name, pid, msgs in chats:
        wins = windows(msgs)
        if not wins:
            continue
        wins = wins[: max(0, max_windows - total_w)]
        total_w += len(wins)
        for i in range(0, len(wins), BATCH):
            added += append_staging(await extract_batch(name, source, wins[i:i + BATCH], pid))
        print("%s %s: окон=%d" % (source, name, len(wins)))
        if total_w >= max_windows:
            print("Достигнут --max-windows")
            break
    print("Всего окон=%d, добавлено в staging=%d" % (total_w, added))


def backfill_ids(tg: str, vk: str) -> None:
    """Без LLM: проставляет tg_id/vk_id в уже извлечённые записи по названию
    чата (имя собеседника). Неоднозначные названия (два чата с одним именем) не трогает."""
    from tools.people_import.common import load_staging, write_staging
    ids = {"tg": {}, "vk": {}}  # type: Dict[str, Dict[str, List[int]]]
    if tg:
        for n, i, _ in iter_tg(Path(tg)):
            ids["tg"].setdefault(n, []).append(i)
    if vk:
        for n, i, _ in iter_vk(Path(vk)):
            ids["vk"].setdefault(n, []).append(i)
    rows = load_staging()
    filled = ambiguous = 0
    for r in rows:
        src = r.get("source")
        field = "tg_id" if src == "tg" else "vk_id" if src == "vk" else None
        if not field or r.get(field) or r.get("origin") == "api":
            continue
        for key in [r["name"]] + list(r.get("aliases") or []):
            cands = ids[src].get(key)
            if cands:
                if len(cands) == 1:
                    r[field] = cands[0]
                    filled += 1
                else:
                    ambiguous += 1
                break
        r.setdefault("origin", "chat" if r.get("quote") else "api")
    write_staging(rows)
    print("backfill: id проставлен=%d, неоднозначных названий=%d" % (filled, ambiguous))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg", default="", help="путь к result.json (TG export)")
    ap.add_argument("--vk", default="", help="папка VK-архива")
    ap.add_argument("--max-windows", type=int, default=400, help="потолок окон (деньги!)")
    ap.add_argument("--backfill-ids", action="store_true", help="без LLM: проставить tg_id/vk_id в staging")
    a = ap.parse_args()
    if a.backfill_ids:
        backfill_ids(a.tg, a.vk)
    else:
        asyncio.run(run(a.tg, a.vk, a.max_windows))
