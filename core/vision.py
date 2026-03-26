"""core/vision.py — парсинг фото чеков через Claude Vision."""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from core.claude_client import ask_claude_vision

logger = logging.getLogger("nexus.vision")

_RECEIPT_SYSTEM = """Ты парсишь фото чека из магазина. Извлеки:
1. Каждую позицию: название и цену
2. Общую сумму (ИТОГО)

Определи категорию каждой позиции:
🐾 Коты: корм, наполнитель
🍜 Продукты: еда, напитки (НЕ энергетики)
🚬 Привычки: сигареты, энергетики, monster, burn, вейп
🏥 Здоровье: лекарства, витамины
💅 Бьюти: косметика, шампунь
🏠 Жилье: бытовая химия
💳 Прочее: остальное

Ответь ТОЛЬКО JSON без markdown:
{"items": [{"name": "название", "amount": число, "category": "🍜 Продукты"}], "total": число}

Если на фото НЕ чек — верни: {"error": "not_receipt"}
"""


async def parse_receipt(image_bytes: bytes, media_type: str = "image/jpeg") -> Optional[dict]:
    """Парсить фото чека через Claude Vision.

    Возвращает {"items": [...], "total": N} или None если не чек/ошибка.
    """
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        raw = await ask_claude_vision(
            prompt="Распарси этот чек из магазина. Только JSON.",
            image_b64=image_b64,
            media_type=media_type,
            system=_RECEIPT_SYSTEM,
        )
        if not raw:
            return None

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)

        if result.get("error"):
            logger.info("parse_receipt: not a receipt: %s", result.get("error"))
            return None

        items = result.get("items", [])
        if not items:
            return None

        total = result.get("total") or sum(it.get("amount", 0) for it in items)
        return {"items": items, "total": total}

    except json.JSONDecodeError as e:
        logger.warning("parse_receipt: JSON parse error: %s (raw=%s)", e, raw[:200] if raw else "")
        return None
    except Exception as e:
        logger.error("parse_receipt: %s", e)
        return None
