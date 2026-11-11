"""core/cat_blocks.py — блоки фактов про котов и аудитория (кто их видит).

Факт «🐾 Коты» (с cat_id и «Общее») несёт блок: profile | care | health | relations | owner | unsorted. Блок определяет, кому факт показывать:
  ситтеру — profile, care, relations; из health только факты типа «критично» (important) и (отдельно, из cat_medications) действующие курсы;
  никогда — owner, unsorted. NULL (блок не поставлен) = закрыто: любая неизвестная или пустая метка считается закрытой. Правило «по умолчанию закрыто»
  живёт здесь и в SQL читающей стороны; ложное «открыто» (закрытый факт ушёл ситтеру) недопустимо.
Блок ставится при записи в ТОМ ЖЕ вызове Haiku, что разбирает факт (ключ «блок» в JSON), нового LLM-вызова нет: `wants_block` (без LLM) решает, нужен ли
блок в промпте, `normalize` строго проверяет ответ (сомнение → unsorted).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

BLOCKS = ("profile", "care", "health", "relations", "owner", "unsorted")
SITTER_BLOCKS = ("profile", "care", "relations")
CRITICAL_TYPE = "important"                       # «критично»: существующий тип факта кота (ключ cat_<id>_important_<hash>)
_CRITICAL_KEY_SQL = r"^cat_[0-9]+_important_[0-9a-f]{8}$"
_CRITICAL_KEY_RE = re.compile(_CRITICAL_KEY_SQL)


def critical_key(key_name: Optional[str]) -> Optional[str]:
    """«important», если ключ факта строго вида cat_<id>_important_<hash8>, иначе None. Только ключ, без догадок по тексту: у фактов без такого
    ключа (в том числе «общие») «критично» нет, и health для ситтера остаётся закрытым."""
    return CRITICAL_TYPE if _CRITICAL_KEY_RE.match(key_name or "") else None


def sitter_sql(alias: str = "") -> str:
    """Условие SQL «факт открыт ситтеру» (то же правило, что `audience`): блок profile / care / relations или health с «критичным» ключом.
    NULL и всё остальное в условие не попадают (NULL IN (...) не истина). Вставлять в WHERE каждого запроса, который отдаёт факты про котов ситтеру."""
    a = alias + "." if alias else ""
    return ("(%scat_block IN ('profile', 'care', 'relations') OR (%scat_block = 'health' AND %skey_name ~ '%s'))" % (a, a, a, _CRITICAL_KEY_SQL))

_CAT_WORDS = re.compile(r"\bкот|кошк|котик|котят|котён|котен|питомец|питомц|\bкиса\b|\bкису\b|\bкисе\b", re.I)


def normalize(raw: object) -> str:
    """Ответ модели → блок. Всё, что не точное значение из BLOCKS (пусто, не строка, другой регистр с мусором, выдумка), — unsorted."""
    if isinstance(raw, str) and raw.strip().lower() in BLOCKS:
        return raw.strip().lower()
    return "unsorted"


def audience(block: Optional[str], fact_type: Optional[str] = None) -> str:
    """'sitter' | 'owner'. NULL / неизвестный блок / owner / unsorted → owner (закрыто). health открыт только для «критично»."""
    if block in SITTER_BLOCKS:
        return "sitter"
    if block == "health" and fact_type == CRITICAL_TYPE:
        return "sitter"
    return "owner"


def wants_block(text: str, cat_names: Optional[Iterable[str]] = None) -> bool:
    """Дешёвая проверка без LLM: в тексте есть имя / кличка кота или слово «кот». Нет — промпт не меняется (лишних токенов ноль)."""
    t = text or ""
    if _CAT_WORDS.search(t):
        return True
    low = t.casefold()
    return any(n and n.casefold()[:max(3, len(n) - 2)] in low for n in (cat_names or []))


DEFINITIONS = (
    "Значения (аудитория: ситтер = человек, который присматривает за котами):\n"
    "  profile — только то, как кота узнать: внешность, возраст, окрас, порода, кличка. Оценки, впечатления и эмоции (милый, смешной, странный) — НЕ profile, это unsorted.\n"
    "  care — что делать ситтеру: кормление, лоток, вода, правила и запреты, повадки, которые требуют действий (убирать вещи, не выпускать).\n"
    "  health — болезни, лекарства, анализы, симптомы, аллергии, ветеринар.\n"
    "  relations — как коты ладят друг с другом и с людьми.\n"
    "  owner — личное владельца: деньги, планы на будущее (завести, купить, отдать), чувства, решения, другие люди; ситтеру не показывать.\n"
    "  unsorted — не уверен или факт не подходит ни под одно значение. При сомнении ВСЕГДА unsorted.\n"
)


def prompt_block() -> str:
    return (
        "\n\nБЛОК ФАКТА ПРО КОТА. Если category = \"🐾 Коты\", добавь в JSON ключ \"блок\": одно из значений profile, care, health, relations, owner, unsorted. "
        + DEFINITIONS +
        '  "тучка рыжая, трёхлетняя, с белой грудкой" → ..."категория":"🐾 Коты",...,"блок":"profile"\n'
        '  "пончик не ест сухой корм, только влажный" → ...,"блок":"care"\n'
        '  "кекс принимает таблетки от давления" → ...,"блок":"health"\n'
        '  "тучка шипит на пончика, когда тот рядом с миской" → ...,"блок":"relations"\n'
        '  "рыжик такой милый" → ...,"блок":"unsorted"\n'
        '  "планирую в следующем году второго кота" → ...,"блок":"owner"\n'
        '  "кекс обошёлся мне в 12 тысяч у ветеринара, надо отложить на следующий раз" → ...,"блок":"owner"'
    )
