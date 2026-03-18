"""core/layout.py — конвертер раскладки EN→RU (QWERTY→ЙЦУКЕН)"""
from __future__ import annotations

_MAPPING = {
    'q':'й','w':'ц','e':'у','r':'к','t':'е','y':'н','u':'г','i':'ш','o':'щ','p':'з',
    '[':'х',']':'ъ',
    'a':'ф','s':'ы','d':'в','f':'а','g':'п','h':'р','j':'о','k':'л','l':'д',
    ';':'ж',"'":'э',
    'z':'я','x':'ч','c':'с','v':'м','b':'и','n':'т','m':'ь',',':'б','.':'ю',
    'Q':'Й','W':'Ц','E':'У','R':'К','T':'Е','Y':'Н','U':'Г','I':'Ш','O':'Щ','P':'З',
    '{':'Х','}':'Ъ',
    'A':'Ф','S':'Ы','D':'В','F':'А','G':'П','H':'Р','J':'О','K':'Л','L':'Д',
    ':':'Ж','Z':'Я','X':'Ч','C':'С','V':'М','B':'И','N':'Т','M':'Ь','<':'Б','>':'Ю',
}

EN2RU = str.maketrans(_MAPPING)


def _ru_ratio(text: str) -> float:
    ru = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    en = sum(1 for c in text if c.isalpha() and c.isascii())
    total = ru + en
    return ru / total if total else 0.0


def maybe_convert(text: str) -> str:
    """Если текст похож на русский в EN раскладке — конвертирует."""
    if not text.strip():
        return text
    if _ru_ratio(text) > 0.3:
        return text
    converted = text.translate(EN2RU)
    if _ru_ratio(converted) > 0.5:
        return converted
    return text
