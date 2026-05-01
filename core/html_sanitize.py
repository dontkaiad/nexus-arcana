"""core/html_sanitize.py — нормализация трактовок таро в чистый HTML.

Разрешённый allowlist: <h3>, <b>, <i>, <p>, <br>.
Никаких атрибутов, классов, инлайн-стилей. Markdown переводится в HTML.
Saммари (триплета/сессии) — отдельная функция: всегда plain-text.
"""
from __future__ import annotations

import re

ALLOWED_TAGS = {"h3", "b", "i", "p", "br"}

# md → html
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"__([^_\n]+?)__")
# одинарный _x_ — курсив, но _ часто встречается внутри слов; требуем
# границу слова + не-_-символ внутри.
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_([^_\n][^_\n]*?)_(?!\w)")
# *x* — курсив (но осторожно, не путать с **), требуем не-* по бокам
_ITALIC_STAR_RE = re.compile(r"(?<![\*\w])\*([^*\n]+?)\*(?!\*)")

# ## heading → <h3>
_H_RE = re.compile(r"^[ \t]*#{2,4}[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Чужие/неразрешённые теги — снять, оставив текст внутри.
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")

_EMPTY_P_RE = re.compile(r"<p>\s*</p>")
_MULTI_BR_RE = re.compile(r"(?:<br\s*/?>\s*){2,}")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK_BOUNDARY_RE = re.compile(r"\s*(</?(?:h3|p)>)\s*")


def _strip_disallowed(html: str) -> str:
    def repl(m: re.Match) -> str:
        tag = m.group(1).lower()
        if tag in ALLOWED_TAGS:
            # Очищаем атрибуты: возвращаем тег без них.
            slash = "/" if m.group(0).startswith("</") else ""
            self_close = m.group(0).endswith("/>")
            if tag == "br":
                return "<br>"
            return f"<{slash}{tag}>" if not self_close else f"<{tag}/>"
        return ""
    return _TAG_RE.sub(repl, html)


def sanitize_interpretation(text: str) -> str:
    """md+html-смесь → нормализованный HTML c allowlist'ом.

    1. ## заголовок → <h3>заголовок</h3>
    2. **жирный** / __жирный__ → <b>жирный</b>
    3. *курсив* / _курсив_ → <i>курсив</i>
    4. Все теги вне allowlist (h3/b/i/p/br) — удалить, оставив текст.
    5. Свернуть пустые <p></p>, лишние <br>.
    6. Trim пробелов в начале/конце.
    """
    if not text:
        return ""

    s = str(text).replace("\r\n", "\n").replace("\r", "\n")

    # 0. снимаем script/style вместе с содержимым.
    s = _SCRIPT_STYLE_RE.sub("", s)

    # 1. headings
    s = _H_RE.sub(lambda m: f"\n<h3>{m.group(1).strip()}</h3>\n", s)

    # 2. bold
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1).strip()}</b>", s)
    s = _BOLD_UNDERSCORE_RE.sub(lambda m: f"<b>{m.group(1).strip()}</b>", s)

    # 3. italic
    s = _ITALIC_UNDERSCORE_RE.sub(lambda m: f"<i>{m.group(1).strip()}</i>", s)
    s = _ITALIC_STAR_RE.sub(lambda m: f"<i>{m.group(1).strip()}</i>", s)

    # 4. drop disallowed tags
    s = _strip_disallowed(s)

    # 5a. Делим по блочным границам (h3/p), оборачиваем сиротский текст в <p>.
    # Сегменты вида '<h3>…</h3>', '<p>…</p>' оставляем, остальное — в <p>.
    parts = re.split(r"(<h3>.*?</h3>|<p>.*?</p>)", s, flags=re.DOTALL)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<h3>") or part.startswith("<p>"):
            out.append(part)
            continue
        # Сиротский текст — разбиваем по двойным переносам, оборачиваем в <p>.
        for chunk in re.split(r"\n{2,}", part):
            chunk = chunk.strip()
            chunk = re.sub(r"\n+", " ", chunk)
            chunk = re.sub(r"[ \t]{2,}", " ", chunk)
            if chunk:
                out.append(f"<p>{chunk}</p>")
    s = "".join(out)

    # 5b. cleanup
    s = _EMPTY_P_RE.sub("", s)
    s = _MULTI_BR_RE.sub("<br>", s)
    # Схлопнуть пробелы только на границах БЛОКОВ h3/p, не трогая инлайн.
    s = _BLOCK_BOUNDARY_RE.sub(r"\1", s)

    return s.strip()


def sanitize_summary(text: str) -> str:
    """Саммари — всегда чистый plain-текст. Удаляем теги и markdown-разметку,
    схлопываем переносы и пробелы."""
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    # Снять script/style вместе с содержимым; остальные теги — в пробел,
    # чтобы соседние слова не склеились.
    s = _SCRIPT_STYLE_RE.sub("", s)
    s = _TAG_RE.sub(" ", s)
    # Markdown → текст.
    s = _BOLD_RE.sub(lambda m: m.group(1), s)
    s = _BOLD_UNDERSCORE_RE.sub(lambda m: m.group(1), s)
    s = _ITALIC_UNDERSCORE_RE.sub(lambda m: m.group(1), s)
    s = _ITALIC_STAR_RE.sub(lambda m: m.group(1), s)
    s = _H_RE.sub(lambda m: m.group(1), s)
    # Схлопнуть переносы и пробелы в одну строку.
    s = re.sub(r"\s+", " ", s).strip()
    return s
