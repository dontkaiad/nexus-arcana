"""core/claude_client.py — обёртка над Anthropic API"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import AsyncIterator, List, Dict, Optional

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


# ── Модели 5.x: единая обработка параметров ──────────────────────────────────
# Haiku 5.5 / Sonnet 5.5 / Opus 5.x / Fable: `temperature` → 400 («deprecated for this model»; проверено на
# проде), мышление включено по умолчанию (адаптивное: ~3 с на короткий JSON против ~0.7 с с выключенным).
# Установленный SDK (anthropic==0.40.0) не знает полей thinking/output_config — они идут через extra_body.
_GEN5_PREFIXES = ("claude-haiku-5", "claude-sonnet-5", "claude-opus-5", "claude-fable", "claude-mythos")


def is_gen5(model: str) -> bool:
    return (model or "").startswith(_GEN5_PREFIXES)


def apply_model_params(kwargs: Dict, model: str, temperature: Optional[float], thinking: Optional[str]) -> Dict:
    """Добавляет в kwargs температуру/мышление с учётом поколения модели.

    thinking: None — по умолчанию модели; "off" — выключить (где скорость важнее глубины); "low" — минимальное.
    Старые модели (4.x): thinking игнорируется, temperature как раньше. 5.x: temperature НЕ шлём;
    "off" → Haiku 5.x: thinking disabled; Sonnet 5.5: between_tools (disabled там 400); Opus/Fable не выключаются —
    для них "off" и "low" означают effort=low."""
    if not is_gen5(model):
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs
    extra: Dict = dict(kwargs.get("extra_body") or {})
    if thinking == "off":
        if model.startswith("claude-haiku-5"):
            extra["thinking"] = {"type": "disabled"}
        elif model.startswith("claude-sonnet-5-5"):
            extra["thinking"] = {"type": "between_tools"}
        else:
            extra["output_config"] = {"effort": "low"}
    elif thinking == "low":
        extra["output_config"] = {"effort": "low"}
    if extra:
        kwargs["extra_body"] = extra
    return kwargs


def _first_text(resp) -> str:
    """Первый text-блок (у 5.x ответ может начинаться с thinking-блока)."""
    blocks = resp.content or []
    for b in blocks:
        if getattr(b, "type", None) == "text":
            return b.text
    return blocks[0].text if blocks and hasattr(blocks[0], "text") else ""


async def ask_claude(
    prompt: str,
    system: str = "",
    model: str = "",
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
    thinking: Optional[str] = None,
    usage_out: Optional[Dict] = None,
) -> str:
    """Простой текстовый запрос. Возвращает строку.

    thinking — см. apply_model_params (только для моделей 5.x). usage_out — если передан dict, в него кладутся
    input_tokens / output_tokens (output включает мышление, если оно было)."""
    used_model = model or config.model_haiku

    kwargs: Dict = {
        "model": used_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    apply_model_params(kwargs, used_model, temperature, thinking)

    try:
        resp = await _create_message(**kwargs)
        if usage_out is not None:
            u = getattr(resp, "usage", None)
            usage_out["input_tokens"] = getattr(u, "input_tokens", 0) or 0
            usage_out["output_tokens"] = getattr(u, "output_tokens", 0) or 0
        return _first_text(resp)
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return ""


async def ask_claude_stream(
    prompt: str,
    system: str = "",
    model: str = "",
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
) -> AsyncIterator[str]:
    """Стримит текстовые дельты вместо возврата готовой строки — для SSE-
    эндпоинтов Mini App (Server-Sent Events), где текст должен допечатываться
    по мере генерации. Бот в Telegram сюда не ходит — ему нужен только
    финальный текст целиком, для него остаётся ask_claude().

    Без @retry_transient: тот декоратор ретраит целый атомарный вызов с одним
    возвращаемым значением. Здесь часть токенов уже могла уйти клиенту по
    SSE до сетевого сбоя — молча ретраить и слать вторую копию с начала
    нельзя. При ошибке генератор просто останавливается; вызывающий SSE-
    эндпоинт закрывает поток с тем текстом, что успел прийти.
    """
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
        async with get_anthropic().messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
    except anthropic.APIError as e:
        logger.error("Claude API stream error: %s", e)
        return


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