"""core/voice.py — Whisper транскрипция голосовых сообщений (OpenAI API)."""
from __future__ import annotations

import logging

import aiohttp

from core.config import config

logger = logging.getLogger("nexus.voice")

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"


async def transcribe(file_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Отправить аудио в OpenAI Whisper, получить текст.

    Возвращает None если OPENAI_API_KEY не задан или ошибка API.
    """
    api_key = config.openai_key
    if not api_key:
        logger.warning("transcribe: OPENAI_API_KEY not set")
        return None

    headers = {"Authorization": f"Bearer {api_key}"}
    data = aiohttp.FormData()
    data.add_field("file", file_bytes, filename=filename, content_type="audio/ogg")
    data.add_field("model", "whisper-1")
    data.add_field("language", "ru")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WHISPER_URL, headers=headers, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    text = result.get("text", "").strip()
                    logger.info("transcribe: OK, %d chars", len(text))
                    return text
                else:
                    error = await resp.text()
                    logger.error("transcribe: Whisper error %d: %s", resp.status, error[:200])
                    return None
    except Exception as e:
        logger.error("transcribe: %s", e)
        return None
