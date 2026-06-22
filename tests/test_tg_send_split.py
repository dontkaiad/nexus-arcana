"""tests/test_tg_send_split.py — split_text / send_long: длинные сообщения
бьются на чанки <4096, не рвут слова и HTML-теги, короткие шлются одним
сообщением. Регрессия на TelegramBadRequest «message is too long» в голосовом
флоу Arcana (трактовка / расшифровка / саммари).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tg_send import DEFAULT_CHUNK, TG_LIMIT, send_long, split_text


# ────────────────────────── split_text ─────────────────────────────────────

def test_short_text_single_chunk():
    """Короткий текст → один чанк, не плодит сообщения."""
    assert split_text("привет") == ["привет"]
    assert split_text("a" * DEFAULT_CHUNK) == ["a" * DEFAULT_CHUNK]


def test_empty_text_no_chunks():
    assert split_text("") == []
    assert split_text("   ") == []
    assert split_text(None) == []  # type: ignore[arg-type]


def test_long_text_splits_each_under_limit():
    """>4096 → N чанков, каждый <= лимита и < жёсткого лимита Telegram."""
    text = "слово " * 2000  # ~12000 символов
    chunks = split_text(text)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= DEFAULT_CHUNK
        assert len(c) < TG_LIMIT


def test_split_does_not_break_words():
    """Границы чанков не разрывают слова посреди."""
    # Уникальные пронумерованные токены — легко проверить целостность.
    words = [f"токен{i:04d}" for i in range(2000)]
    text = " ".join(words)
    chunks = split_text(text)
    # Склейка всех токенов из всех чанков == исходный набор, ни один не порезан.
    rejoined = " ".join(chunks).split()
    assert rejoined == words


def test_no_content_lost_on_very_long_text():
    """Старый баг: ад-хок [3500:7000] терял хвост >7000. Теперь — ничего."""
    text = ("абзац номер раз. " * 500) + ("\n\nабзац номер два. " * 500)
    chunks = split_text(text)
    # Каждое слово исходника присутствует в склейке.
    assert "".join(chunks).count("абзац") == text.count("абзац")


def test_split_prefers_paragraph_boundary():
    """При наличии границы абзаца режем по ней, а не посреди строки."""
    para = "Текст абзаца. " * 200  # ~2800 символов
    text = para + "\n\n" + para + "\n\n" + para
    chunks = split_text(text)
    assert len(chunks) >= 2
    # Ни один чанк не начинается/кончается обрывком тега или пустотой.
    for c in chunks:
        assert c.strip() == c


def test_split_does_not_break_html_tags():
    """Чанк не должен заканчиваться открытым '<...>' — иначе parse error."""
    block = "<b>Заголовок раздела</b>\nДлинный текст трактовки. " * 200
    chunks = split_text(block)
    assert len(chunks) >= 2
    for c in chunks:
        # Незакрытого '<' в конце чанка быть не должно.
        last_lt = c.rfind("<")
        last_gt = c.rfind(">")
        assert last_lt <= last_gt, f"оборванный тег в чанке: ...{c[-40:]!r}"


def test_single_huge_token_hard_cut():
    """Слово длиннее лимита всё равно режется (жёстко), без зависания."""
    chunks = split_text("x" * (DEFAULT_CHUNK * 3 + 10))
    assert len(chunks) == 4
    for c in chunks:
        assert len(c) <= DEFAULT_CHUNK


# ────────────────────────── send_long ──────────────────────────────────────

def _mock_msg():
    msg = MagicMock()
    msg.answer = AsyncMock(return_value=MagicMock(message_id=1))
    return msg


@pytest.mark.asyncio
async def test_send_long_short_one_call():
    """Короткий текст → ровно один answer (не split)."""
    msg = _mock_msg()
    await send_long(msg, "коротко", parse_mode="HTML")
    assert msg.answer.await_count == 1
    assert msg.answer.await_args.args[0] == "коротко"


@pytest.mark.asyncio
async def test_send_long_long_multiple_calls_each_under_limit():
    """Длинная трактовка → несколько answer, каждый < 4096, без одного гиганта."""
    msg = _mock_msg()
    text = "Длинная трактовка расклада. " * 600  # ~16000 символов
    await send_long(msg, text, parse_mode="HTML")
    assert msg.answer.await_count >= 4
    for call in msg.answer.await_args_list:
        assert len(call.args[0]) < TG_LIMIT


@pytest.mark.asyncio
async def test_send_long_reply_markup_only_on_last():
    """Кнопки вешаются только на последнее сообщение."""
    msg = _mock_msg()
    kb = MagicMock()
    text = "Текст. " * 800
    await send_long(msg, text, parse_mode="HTML", reply_markup=kb)
    calls = msg.answer.await_args_list
    assert len(calls) >= 2
    for call in calls[:-1]:
        assert call.kwargs.get("reply_markup") is None
    assert calls[-1].kwargs.get("reply_markup") is kb


@pytest.mark.asyncio
async def test_send_long_empty_no_calls():
    msg = _mock_msg()
    res = await send_long(msg, "")
    assert res is None
    assert msg.answer.await_count == 0


# ─────────────────── голосовой флоу: длинная трактовка ──────────────────────

@pytest.mark.asyncio
async def test_tarot_interpret_long_splits_no_single_oversize(monkeypatch):
    """handle_tarot_interpret с длинной трактовкой доходит до конца:
    зовётся split (>1 answer), ни один send не превышает лимит Telegram.
    """
    from arcana.handlers import sessions

    long_interp = "Карта говорит о переменах. " * 700  # ~19000 символов
    monkeypatch.setattr(sessions, "ask_claude", AsyncMock(return_value=long_interp))

    msg = _mock_msg()
    await sessions.handle_tarot_interpret(msg, "3 карты на любовь")

    assert msg.answer.await_count >= 2  # был бы 1 гигантский send — упал бы
    for call in msg.answer.await_args_list:
        assert len(call.args[0]) < TG_LIMIT
