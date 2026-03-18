"""core/claude_client.py — обёртка над Anthropic API"""
from __future__ import annotations

import logging
from typing import List, Dict, Optional

import anthropic

from core.config import config

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None


def get_anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.anthropic_key)
    return _client


async def ask_claude(
    prompt: str,
    system: str = "",
    model: str = "",
    max_tokens: int = 1024,
) -> str:
    """Простой текстовый запрос. Возвращает строку."""
    client = get_anthropic()
    used_model = model or config.model_haiku

    kwargs: Dict = {
        "model": used_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        resp = await client.messages.create(**kwargs)
        return resp.content[0].text
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return ""


async def ask_claude_vision(
    prompt: str,
    image_b64: str,
    media_type: str = "image/jpeg",
    system: str = "",
) -> str:
    """Запрос с изображением (base64). Использует Sonnet."""
    client = get_anthropic()

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

    try:
        resp = await client.messages.create(**kwargs)
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
        "  priority: Высокий | Средний | Низкий\n"
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