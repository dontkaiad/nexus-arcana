"""core/self_gender.py — тексты фактов «Я»: м.р. → ж.р. (Haiku пересказывал слова Кай в мужском роде).

Чистая часть: промпт, разбор ответа и ПРОВЕРКА правки. Правка принимается, только если это замена окончаний рода:
то же число слов, цифры те же, текст почти тот же (difflib ≥ 0.8). Всё остальное — в «подозрительные», в базу не пишется."""
from __future__ import annotations

import difflib
import json
import re
from typing import Dict, List, Sequence, Tuple

# местоимения, которыми факт говорит о ДРУГОМ человеке: их род менять нельзя
_OTHERS = {"он", "его", "ему", "им", "нём", "нем", "ним", "него"}

SYSTEM = (
    "Ты правишь короткие факты о женщине (автор записей — она сама). Факт записан от третьего лица или без подлежащего; "
    "часть слов, относящихся к ней, ошибочно стоит в мужском роде.\n"
    "Исправь ТОЛЬКО формы рода, относящиеся к ней: глаголы прошедшего времени (-л → -ла, -лся → -лась), краткие "
    "прилагательные (готов → готова, должен → должна), прилагательные и причастия при «себя» (нейроотличным → нейроотличной), "
    "«сам» → «сама», роли-существительные, если это явно она (фанат → фанатка).\n"
    "НЕ меняй формы, относящиеся к другим людям (он, его, партнёр, бывший, друг, Имя и т.п.) и слова без рода. "
    "Не перефразируй, не добавляй и не убирай слов, число слов остаётся тем же.\n"
    "Вход: строки «id|текст». Ответ — ТОЛЬКО JSON {\"id\": \"новый текст\"} для изменённых фактов; если менять нечего — {}. "
    "Без пояснений и markdown."
)


def build_prompt(rows: Sequence[Tuple[int, str]]) -> str:
    return "\n".join(f"{i}|{(t or '').replace(chr(10), ' ')}" for i, t in rows)


def parse_reply(raw: str) -> Dict[int, str]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    out: Dict[int, str] = {}
    for k, v in (data or {}).items():
        if str(k).isdigit() and isinstance(v, str) and v.strip():
            out[int(k)] = " ".join(v.split())
    return out


def validate(old: str, new: str) -> Tuple[bool, str]:
    """(ок, причина). Принимаем только замену окончаний: те же слова по числу и цифрам, текст ≥ 0.8 похож, и он действительно изменился."""
    o, n = " ".join((old or "").split()), " ".join((new or "").split())
    if o == n:
        return False, "без изменений"
    if len(o.split()) != len(n.split()):
        return False, "число слов изменилось"
    if re.findall(r"\d+", o) != re.findall(r"\d+", n):
        return False, "цифры изменились"
    if o.lower().replace("ё", "е") == n.lower().replace("ё", "е"):
        return False, "только ё/е"
    if difflib.SequenceMatcher(None, o.lower(), n.lower()).ratio() < 0.8:
        return False, "слишком большая правка"
    # слово целиком заменено другим (не окончание): общий префикс слова ≥ 3 букв или слово совпадает
    for a, b in zip(o.lower().split(), n.lower().split()):
        if a != b and a.strip(".,;:!?") in _OTHERS:
            return False, f"тронуто местоимение другого человека: {a} → {b}"
        if a != b and len(a) > 3 and len(b) > 3 and a[:3] != b[:3]:
            return False, f"заменено слово целиком: {a} → {b}"
    return True, ""
