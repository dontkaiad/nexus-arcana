"""core/html_for_telegram.py — конвертация sanitized HTML в Telegram-safe HTML.

Telegram parse_mode='HTML' поддерживает только узкий allowlist:
b, strong, i, em, u, s, code, pre, a, span, blockquote.
Notion и miniapp хранят и используют <h3>/<p>; перед отправкой в чат
их нужно заменить на разрешённые теги/переносы.
"""
from __future__ import annotations

import re

_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.DOTALL | re.IGNORECASE)
_P_RE = re.compile(r"<p>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_MULTI_NL_RE = re.compile(r"\n{3,}")


def html_to_telegram(html: str) -> str:
    """sanitize_interpretation HTML → Telegram-safe HTML (parse_mode='HTML').

    <h3>X</h3>  → <b>X</b>\n
    <p>X</p>    → X\n\n
    <br>        → \n
    Схлопывает 3+ переносов в 2.
    """
    if not html:
        return ""
    s = _H3_RE.sub(lambda m: f"<b>{m.group(1).strip()}</b>\n", html)
    s = _P_RE.sub(lambda m: f"{m.group(1).strip()}\n\n", s)
    s = _BR_RE.sub("\n", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()
