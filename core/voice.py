"""core/voice.py — Whisper транскрипция голосовых сообщений (OpenAI API)."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from core.claude_client import MAX_ATTEMPTS, backoff_delay
from core.config import config

logger = logging.getLogger("nexus.voice")

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


def _form_data(file_bytes: bytes, filename: str) -> aiohttp.FormData:
    # FormData одноразовая — пересобираем на каждую попытку ретрая
    data = aiohttp.FormData()
    data.add_field("file", file_bytes, filename=filename, content_type="audio/ogg")
    data.add_field("model", "whisper-1")
    data.add_field("language", "ru")
    return data


def _whisper_retry_delay(headers, attempt: int) -> float:
    try:
        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            return float(retry_after)
    except (TypeError, ValueError):
        pass
    return backoff_delay(attempt)


async def transcribe(file_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """Отправить аудио в OpenAI Whisper, получить текст.

    Транзиентные ошибки (429 / 5xx / таймаут / обрыв соединения) ретраятся
    до MAX_ATTEMPTS с экспоненциальным backoff + jitter, для 429 уважается
    Retry-After. Возвращает None если OPENAI_API_KEY не задан или ошибка API.
    """
    api_key = config.openai_key
    if not api_key:
        logger.warning("transcribe: OPENAI_API_KEY not set")
        return None

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    async with session.post(
                        WHISPER_URL,
                        headers=headers,
                        data=_form_data(file_bytes, filename),
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            text = result.get("text", "").strip()
                            logger.info("transcribe: OK, %d chars", len(text))
                            # Сам транскрипт — ТОЛЬКО локально (journald/docker logs)
                            # на DEBUG, для отладки мисхёрдов голоса. НИКОГДА не
                            # через log_error/notify_log_group: транскрипт = личный
                            # текст пользователя, в TG-группу логов его слать нельзя.
                            logger.debug("transcribe: text=%r", text)
                            return text
                        transient = resp.status == 429 or resp.status >= 500
                        if not transient or attempt >= MAX_ATTEMPTS - 1:
                            error = await resp.text()
                            logger.error("transcribe: Whisper error %d: %s", resp.status, error[:200])
                            return None
                        delay = _whisper_retry_delay(resp.headers, attempt)
                        logger.warning(
                            "transcribe: Whisper %d, retry %d/%d in %.1fs",
                            resp.status, attempt + 1, MAX_ATTEMPTS - 1, delay,
                        )
                    await asyncio.sleep(delay)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt >= MAX_ATTEMPTS - 1:
                        raise
                    delay = backoff_delay(attempt)
                    logger.warning(
                        "transcribe: %s, retry %d/%d in %.1fs",
                        type(e).__name__, attempt + 1, MAX_ATTEMPTS - 1, delay,
                    )
                    await asyncio.sleep(delay)
    except Exception as e:
        logger.error("transcribe: %s", e)
        return None
    return None
