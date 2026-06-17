"""core/claude_client.py — обёртка над Anthropic API"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import List, Dict, Optional

import anthropic

from core.config import config

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None

# Resilience-политика: до 3 попыток на транзиентные ошибки.
# Встроенные ретраи SDK выключены (max_retries=0) — иначе попытки множатся.
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 60.0  # секунд на один HTTP-запрос
_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0


def get_anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_key,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,
        )
    return _client


def backoff_delay(attempt: int) -> float:
    """Экспоненциальная пауза с jitter: ~1с, ~2с, ~4с..."""
    return min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt)) + random.uniform(0, 1)


def _retry_after_seconds(exc: anthropic.APIStatusError) -> Optional[float]:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _transient_delay(exc: Exception, attempt: int) -> Optional[float]:
    """Пауза перед ретраем или None — ошибка не транзиентная."""
    if isinstance(exc, anthropic.RateLimitError):  # 429: уважаем Retry-After
        retry_after = _retry_after_seconds(exc)
        return retry_after if retry_after is not None else backoff_delay(attempt)
    if isinstance(exc, anthropic.APIConnectionError):  # включая APITimeoutError
        return backoff_delay(attempt)
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return backoff_delay(attempt)
    return None  # остальные 4xx и прочее — наверх без ретрая


def retry_transient(fn):
    """Ретрай транзиентных ошибок Anthropic API: 429 / 5xx / timeout / connection.

    После MAX_ATTEMPTS исключение уходит наверх — вызывающий код
    отвечает за graceful fallback (""/{}).
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await fn(*args, **kwargs)
            except anthropic.APIError as e:
                delay = None
                if attempt < MAX_ATTEMPTS - 1:
                    delay = _transient_delay(e, attempt)
                if delay is None:
                    raise
                logger.warning(
                    "Claude API transient error (%s), retry %d/%d in %.1fs",
                    type(e).__name__, attempt + 1, MAX_ATTEMPTS - 1, delay,
                )
                await asyncio.sleep(delay)
    return wrapper


@retry_transient
async def _create_message(**kwargs):
    return await get_anthropic().messages.create(**kwargs)


async def ask_claude(
    prompt: str,
    system: str = "",
    model: str = "",
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
) -> str:
    """Простой текстовый запрос. Возвращает строку."""
    used_model = model or config.model_haiku

    kwargs: Dict = {
        "model": used_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature

    try:
        resp = await _create_message(**kwargs)
        return resp.content[0].text
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return ""


async def ask_claude_vision(
    prompt: str,
    image_b64: str,
    media_type: str = "image/jpeg",
    system: str = "",
    temperature: Optional[float] = None,
) -> str:
    """Запрос с изображением (base64). Использует Sonnet."""
    messages: List[Dict] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    kwargs: Dict = {
        "model": config.model_sonnet,
        "max_tokens": 2048,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature

    try:
        resp = await _create_message(**kwargs)
        return resp.content[0].text
    except anthropic.APIError as e:
        logger.error("Claude Vision error: %s", e)
        return ""


async def parse_finance(text: str) -> Dict:
    """
    Парсит произвольный текст расхода/дохода.
    Возвращает dict: {amount, type_, category, source}
    """
    system = (
        "Ты — парсер финансовых записей. "
        "Отвечай ТОЛЬКО валидным JSON без пояснений.\n"
        "Поля:\n"
        "  amount: float\n"
        f"  type_: '💰 Доход' | '💸 Расход'\n"
        f"  category: одна из {config.finance_categories}\n"
        f"  source: одна из {config.finance_sources}\n"
        "Если поле неизвестно — используй значение по умолчанию: "
        "type_='💸 Расход', category='💳 Прочее', source='💳 Карта'."
    )
    import json
    raw = await ask_claude(prompt=text, system=system, max_tokens=256)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("parse_finance: bad JSON: %s", raw)
        return {}


async def parse_task(text: str) -> Dict:
    """
    Парсит текст задачи.
    Возвращает dict: {title, category, priority, deadline, reminder}
    """
    system = (
        "Ты — парсер задач. Отвечай ТОЛЬКО валидным JSON без пояснений.\n"
        "Поля:\n"
        "  title: string\n"
        "  category: Покупки | Бьюти | Встречи | Дом | Здоровье | Финансы | Работа | Другое\n"
        "  priority: Срочно | Важно | Можно потом\n"
        "  deadline: ISO date string или null\n"
        "  reminder: string (например 'за 1 час') или ''"
    )
    import json
    raw = await ask_claude(prompt=text, system=system, max_tokens=256)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("parse_task: bad JSON: %s", raw)
        return {}

async def analyze_image(prompt: str, image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """Алиас ask_claude_vision для бинарных данных (bytes)."""
    import base64
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    return await ask_claude_vision(prompt, image_b64, media_type=media_type)