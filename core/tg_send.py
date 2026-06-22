"""core/tg_send.py — безопасная отправка длинных сообщений в Telegram.

Telegram отбивает сообщения длиннее 4096 символов (`TelegramBadRequest:
message is too long`). LLM-трактовки раскладов и Whisper-расшифровки голосовых
легко превышают лимит → раньше падало неперехваченным исключением.

`split_text` бьёт текст на чанки <= лимита, предпочитая границы в порядке
абзац → строка → предложение → слово, не разрывая слова и не кромсая HTML-теги
(parse_mode='HTML' не прощает незакрытый `<b>` в чанке). `send_long` шлёт
чанки последовательно одним вызовом; reply_markup вешается только на последнее
сообщение (кнопки логичны в конце).
"""
from __future__ import annotations

import re
from typing import List, Optional

# Жёсткий лимит Telegram — 4096. Берём запас: HTML-теги и эмодзи (в UTF-16
# многие emoji = 2 code unit) раздувают «сырую» длину строки.
TG_LIMIT = 4096
DEFAULT_CHUNK = 3900

# Конец предложения + пробел/конец строки — точка для мягкого реза.
_SENTENCE_RE = re.compile(r"[.!?…](?:\s|$)")


def _avoid_tag_cut(s: str, idx: int) -> int:
    """Если рез на idx попадает внутрь открытого '<...>' — отодвинуть к его '<'.

    Без этого второй чанк начнётся битым тегом и Telegram отобьёт parse_mode.
    """
    if idx <= 0 or idx >= len(s):
        return idx
    lt = s.rfind("<", 0, idx)
    gt = s.rfind(">", 0, idx)
    if lt > gt and lt > 0:  # есть '<' без закрывающего '>' до реза
        return lt
    return idx


def _find_cut(s: str, limit: int) -> int:
    """Лучшая позиция реза в пределах limit. Возвращает индекс конца чанка."""
    window = s[:limit]
    floor = limit // 2  # не плодим слишком мелкие чанки ради «красивой» границы
    # 1) граница абзаца
    p = window.rfind("\n\n")
    if p >= floor:
        return p + 2
    # 2) граница строки
    n = window.rfind("\n")
    if n >= floor:
        return n + 1
    # 3) конец предложения
    last_sentence = -1
    for m in _SENTENCE_RE.finditer(window):
        last_sentence = m.end()
    if last_sentence >= floor:
        return last_sentence
    # 4) граница слова
    sp = window.rfind(" ")
    if sp >= floor:
        return _avoid_tag_cut(s, sp + 1)
    # 5) жёсткий рез (слово/тег длиннее окна)
    return _avoid_tag_cut(s, limit)


def split_text(text: str, limit: int = DEFAULT_CHUNK) -> List[str]:
    """Разбить text на чанки <= limit по «человеческим» границам.

    Короткий текст возвращается одним чанком (не плодит сообщения). Пустой —
    пустым списком.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    rest = text
    while len(rest) > limit:
        cut = _find_cut(rest, limit)
        if cut <= 0 or cut > limit:
            cut = limit
        head = rest[:cut].rstrip()
        if head:
            chunks.append(head)
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def send_long(
    message,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    reply_markup=None,
    limit: int = DEFAULT_CHUNK,
    **kwargs,
):
    """Отправить text, при необходимости разбив на чанки <= limit.

    reply_markup вешается ТОЛЬКО на последнее сообщение. Возвращает последнее
    отправленное Message (None — если text пустой). Дополнительные kwargs
    (например disable_web_page_preview) прокидываются в message.answer.
    """
    chunks = split_text(text, limit)
    if not chunks:
        return None
    last = None
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        last = await message.answer(
            chunk,
            parse_mode=parse_mode,
            reply_markup=reply_markup if is_last else None,
            **kwargs,
        )
    return last
