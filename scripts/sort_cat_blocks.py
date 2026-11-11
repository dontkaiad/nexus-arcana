"""scripts/sort_cat_blocks.py — разовая раскладка существующих фактов про котов по блокам (profile | care | health | relations | owner | unsorted).

Берёт факты «🐾 Коты» (актуальные, не архивные, без блока: cat_block IS NULL), каждому ищет блок и ПЕЧАТАЕТ ТАБЛИЦУ «id → блок → аудитория».
В базу пишет только `--apply --plan FILE` и только тот план, который владелец уже видела: повторный вызов модели при записи исключён.

Как решается блок (по убыванию силы):
  1. правило: факт карточки человека (person_id) — owner, без вызова модели (факты «Я» про котов остаются у владельца);
  2. владелец: `--override ID=БЛОК` (поверх всего остального);
  3. защита: подозрение на деньги / планы / чувства → owner; пароли, коды, ссылки, длинные числа → unsorted; слова про лекарство, а у модели не owner →
     health (ситтер видит курсы из cat_medications, но не текст факта);
  4. модель (один вызов Haiku на все факты): блок ситтера принимается только при `"уверен": true`, иначе unsorted; нет ответа / вне списка → unsorted.
Ложное «открыто» недопустимо: любое сомнение закрывает факт.

Режимы:
  (по умолчанию)                       dry-run: читает БД (или `--from-json FILE`), зовёт модель, печатает таблицу, пишет план `--save-plan FILE` (права 600)
  --apply --plan FILE --rollback FILE  запись одной транзакцией; rollback-файл (права 600) пишется ДО записи; строка должна совпасть с планом (текст,
                                       блок ещё NULL), иначе вся запись откатывается
  --undo FILE [--apply]                вернуть блоки из rollback-файла (dry-run без --apply)
Перед `--apply` — дамп БД (это делает человек, не скрипт). В таблице печатается текст фактов: это данные владельца, не логируем и не коммитим.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cat_blocks  # noqa: E402
from core.cats import fact_kind  # noqa: E402

CATS_CATEGORY = "🐾 Коты"
SELECT = ("SELECT m.id, m.fact_text, m.key_name, m.cat_id, m.person_id, (SELECT count(*) FROM cat_medications d WHERE d.cat_id = m.cat_id "
          "AND d.status = 'active' AND d.starts_on <= current_date AND (d.ends_on IS NULL OR d.ends_on >= current_date)) AS courses FROM memories m WHERE m.category = :cat AND m.is_current AND NOT m.is_archived "
          "AND m.review_status <> 'rejected' AND m.fact_text <> '' AND m.cat_block IS NULL AND m.user_id = :u ORDER BY m.id")

_OWNER_RE = re.compile(r"руб|тысяч|тыс\.|₽|\$|стоил|стоит\s+\d|обошл|отложить|заплатил|хочу\s+(?:завести|взять|купить|отдать)|планирую|собираюсь|переживаю|"
                       r"боюсь\s+за|скучаю|мне\s+(?:тяжело|грустно)", re.I)
_SECRET_RE = re.compile(r"парол|\bPIN\b|\bкод\b|https?://|\d{6,}", re.I)
_MED_RE = re.compile(r"препарат|лекарств|таблет|капл|укол|антибиот|обезбол|витамин|гормон|успокоител", re.I)


@dataclass
class Decision:
    id: int
    text: str
    kind: str                     # тип факта (important | likes | dislikes | other): из ключа, иначе по тексту, как при показе
    cat_id: Optional[int]
    block: str
    source: str                   # правило | владелец | защита | сомнение | модель
    note: str = ""
    courses: int = 0              # действующих курсов у кота в cat_medications

    @property
    def audience(self) -> str:
        return cat_blocks.audience(self.block, self.kind)

    @property
    def sha(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:12]


def kind_of(row: Dict[str, Any]) -> str:
    return fact_kind(row.get("key_name") or "", row["fact_text"])


def build_prompt(rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    system = ("Ты раскладываешь факты про котов владельца по блокам. Отвечай ТОЛЬКО валидным JSON без пояснений: массив, по одному объекту на каждый факт: "
              '{"id": <число>, "блок": "<значение>", "уверен": true|false}. "уверен": true, только если блок очевиден; иначе false и блок unsorted.\n'
              + cat_blocks.DEFINITIONS)
    return system, "\n".join("%d: %s" % (r["id"], " ".join(r["fact_text"].split())) for r in rows)


def parse_answer(raw: str, ids: List[int]) -> Dict[int, Tuple[str, bool]]:
    """Ответ модели → {id: (блок, уверен)}. Мусор / нет id / дубль id / блок вне списка → (unsorted, False): ничего не открываем по догадке."""
    out: Dict[int, Tuple[str, bool]] = {}
    try:
        data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    except Exception:
        data = []
    seen: Dict[int, int] = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
            continue
        seen[item["id"]] = seen.get(item["id"], 0) + 1
        out[item["id"]] = (cat_blocks.normalize(item.get("блок")), item.get("уверен") is True)
    for i in ids:
        if seen.get(i, 0) != 1:
            out[i] = ("unsorted", False)
    return {i: out[i] for i in ids}


async def ask_model(rows: List[Dict[str, Any]]) -> Dict[int, Tuple[str, bool]]:
    from core.claude_client import ask_claude
    system, user = build_prompt(rows)
    raw = await ask_claude(user, system=system, max_tokens=60 * len(rows) + 200, role="parse", temperature=0)
    return parse_answer(raw or "", [r["id"] for r in rows])


def decide(row: Dict[str, Any], answer: Optional[Tuple[str, bool]], override: Optional[str] = None) -> Decision:
    d = Decision(row["id"], row["fact_text"], kind_of(row), row.get("cat_id"), "unsorted", "сомнение", courses=int(row.get("courses") or 0))
    if override:
        d.block, d.source = cat_blocks.normalize(override), "владелец"
        return d
    if row.get("person_id") is not None:
        d.block, d.source, d.note = "owner", "правило", "факт карточки человека: остаётся у владельца, без привязки к коту"
        return d
    block, sure = answer if answer else ("unsorted", False)
    d.block, d.source = block, "модель"
    if _SECRET_RE.search(d.text):
        d.block, d.source, d.note = "unsorted", "защита", "похоже на пароль, код, ссылку или длинное число"
    elif _OWNER_RE.search(d.text) and block != "owner":
        d.block, d.source, d.note = "owner", "защита", "похоже на деньги, планы или чувства владельца"
    elif _MED_RE.search(d.text) and block != "owner":
        d.block, d.source, d.note = "health", "защита", "про лекарство: ситтер видит курс из cat_medications, не этот текст"
    elif cat_blocks.audience(block, d.kind) == "sitter" and not sure:
        d.block, d.source, d.note = "unsorted", "сомнение", "модель не уверена"
    elif block == "unsorted":
        d.source = "сомнение"
    return d


def make_plan(rows: List[Dict[str, Any]], answers: Dict[int, Tuple[str, bool]], overrides: Optional[Dict[int, str]] = None) -> List[Decision]:
    return [decide(r, answers.get(r["id"]), (overrides or {}).get(r["id"])) for r in rows]


def render_table(plan: List[Decision]) -> str:
    lines = ["| id | кот | тип | блок | аудитория | источник | текст |", "|---|---|---|---|---|---|---|"]
    for d in plan:
        lines.append("| %d | %s | %s | %s | %s | %s%s | %s |" % (d.id, d.cat_id if d.cat_id else "общее", d.kind, d.block, d.audience, d.source,
                                                             " (%s)" % d.note if d.note else "", " ".join(d.text.split()).replace("|", "/")))
    n = len(plan)
    by_aud = {a: sum(1 for d in plan if d.audience == a) for a in ("sitter", "owner")}
    lines.append("")
    lines.append("Итого %d: ситтер увидит %d, закрыто %d. Блоки: %s." % (n, by_aud["sitter"], by_aud["owner"],
                                                                   ", ".join("%s %d" % (b, sum(1 for d in plan if d.block == b)) for b in cat_blocks.BLOCKS)))
    return "\n".join(lines)


def med_report(plan: List[Decision]) -> str:
    """Факты про лекарства: блок health (любой факт) или слова про лекарство в тексте. Что станет с каждым и есть ли курс у кота."""
    out = []
    for d in plan:
        if d.block != "health" and not _MED_RE.search(d.text):
            continue
        if d.cat_id:
            link = ("есть действующих курсов у кота: %d (ситтер видит курс, не этот факт)" % d.courses) if d.courses else "курса в cat_medications нет"
            need = "; нужен курс: его заводит владелец (скрипт курсы не создаёт, доз и дат нет)" if not d.courses and d.audience == "owner" else ""
        else:
            link, need = "без привязки к коту: курс не определить", ""
        out.append("#%d → блок %s, аудитория %s; %s%s" % (d.id, d.block, d.audience, link, need))
    return "\n".join(out) or "фактов про лекарства у котов не найдено"


def _write_private(path: str, data: Dict[str, Any], overwrite: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)


def plan_to_json(plan: List[Decision], user_id: str) -> Dict[str, Any]:
    return {"created": datetime.now().isoformat(), "user_id": user_id,
            "items": [{"id": d.id, "sha": d.sha, "block": d.block, "source": d.source} for d in plan]}


def apply_plan(engine, user_id: str, plan_json: Dict[str, Any], rollback_path: str) -> int:
    """Одна транзакция. Каждая строка должна быть той же (хэш текста), всё ещё без блока и того же владельца, иначе RuntimeError и откат всего."""
    from sqlalchemy import text
    items = plan_json["items"]
    if plan_json.get("user_id") != user_id:
        raise RuntimeError("план другого владельца")
    for it in items:
        if it["block"] not in cat_blocks.BLOCKS:
            raise RuntimeError("неизвестный блок в плане: id %s" % it["id"])
    _write_private(rollback_path, {"created": datetime.now().isoformat(), "user_id": user_id,
                                   "updated": {str(it["id"]): {"old": None, "new": it["block"]} for it in items}})
    with engine.begin() as c:
        for it in items:
            row = c.execute(text("SELECT fact_text FROM memories WHERE id = :i AND user_id = :u AND category = :cat AND cat_block IS NULL FOR UPDATE"),
                            {"i": it["id"], "u": user_id, "cat": CATS_CATEGORY}).first()
            if row is None or hashlib.sha1(row[0].encode("utf-8")).hexdigest()[:12] != it["sha"]:
                raise RuntimeError("строка изменилась после dry-run: id %s; запись отменена" % it["id"])
            c.execute(text("UPDATE memories SET cat_block = :b WHERE id = :i"), {"b": it["block"], "i": it["id"]})
    return len(items)


def undo(engine, user_id: str, rollback: Dict[str, Any], apply: bool) -> int:
    from sqlalchemy import text
    todo = rollback["updated"]
    if not apply:
        return len(todo)
    n = 0
    with engine.begin() as c:
        for i, v in todo.items():
            n += c.execute(text("UPDATE memories SET cat_block = :o WHERE id = :i AND user_id = :u AND cat_block IS NOT DISTINCT FROM :n"),
                           {"o": v["old"], "i": int(i), "u": user_id, "n": v["new"]}).rowcount
    return n


def load_rows(engine, user_id: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(SELECT), {"cat": CATS_CATEGORY, "u": user_id})]


def main(argv: Optional[List[str]] = None, ask: Callable[[List[Dict[str, Any]]], Awaitable[Dict[int, Tuple[str, bool]]]] = ask_model) -> int:
    ap = argparse.ArgumentParser(prog="sort_cat_blocks")
    ap.add_argument("--user-id", default=os.environ.get("SORT_USER_ID", ""))
    ap.add_argument("--from-json", help="строки из файла (только dry-run): {'rows': [...]}")
    ap.add_argument("--override", action="append", default=[], metavar="ID=БЛОК")
    ap.add_argument("--save-plan")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan")
    ap.add_argument("--rollback")
    ap.add_argument("--undo")
    a = ap.parse_args(argv)
    if not a.user_id:
        sys.exit("нужен --user-id")
    if a.undo:
        from core.db import get_engine
        n = undo(get_engine(), a.user_id, json.load(open(a.undo, encoding="utf-8")), a.apply)
        print(("откат записан: %d" if a.apply else "dry-run отката: вернёт %d") % n)
        return 0
    if a.apply:
        if not (a.plan and a.rollback) or a.from_json:
            sys.exit("--apply требует --plan и --rollback и не работает с --from-json")
        from core.db import get_engine
        n = apply_plan(get_engine(), a.user_id, json.load(open(a.plan, encoding="utf-8")), a.rollback)
        print("записано блоков: %d; rollback: %s" % (n, a.rollback))
        return 0
    overrides = {}
    for o in a.override:
        k, _, v = o.partition("=")
        if not k.isdigit() or v not in cat_blocks.BLOCKS:
            sys.exit("--override: ожидается ID=БЛОК из %s" % ", ".join(cat_blocks.BLOCKS))
        overrides[int(k)] = v
    if a.from_json:
        rows = json.load(open(a.from_json, encoding="utf-8"))["rows"]
    else:
        from core.db import get_engine
        rows = load_rows(get_engine(), a.user_id)
    to_ask = [r for r in rows if r.get("person_id") is None and r["id"] not in overrides]
    answers = asyncio.run(ask(to_ask)) if to_ask else {}
    plan = make_plan(rows, answers, overrides)
    print(render_table(plan))
    print("\nФакты про лекарства:\n" + med_report(plan))
    if a.save_plan:
        _write_private(a.save_plan, plan_to_json(plan, a.user_id))
        print("\nплан сохранён: %s (права 600); DRY-RUN: в базу ничего не записано" % a.save_plan)
    else:
        print("\nDRY-RUN: в базу ничего не записано")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
