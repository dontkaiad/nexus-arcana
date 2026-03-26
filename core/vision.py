"""core/vision.py — парсинг фото финансовых операций через Claude Vision."""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from core.claude_client import ask_claude_vision

logger = logging.getLogger("nexus.vision")

_RECEIPT_SYSTEM = """Ты парсишь изображение финансовой операции. Это может быть:
- Скриншот из банковского приложения (Сбер, Тинькофф, Альфа и др.)
- Скриншот истории операций
- Бумажный чек из магазина

Извлеки:
1. Каждую операцию/позицию: название (получатель или товар) и сумму
2. Общую сумму

Определи категорию:
🐾 Коты: зоомагазин, корм, ветеринар
🍜 Продукты: супермаркет, Лента, Пятёрочка, Магнит, продукты
🍱 Кафе/Доставка: ресторан, кафе, Яндекс Еда, Delivery Club
🚕 Транспорт: такси, Яндекс Go, метро, автобус
🚬 Привычки: табак, сигареты
💅 Бьюти: салон, маникюр, парикмахерская
🏥 Здоровье: аптека, клиника, врач
💻 Подписки: подписка, Netflix, Spotify, YouTube
🏠 Жилье: ЖКХ, аренда, электричество
👗 Гардероб: одежда, обувь, Wildberries, Ozon
💳 Прочее: если не подходит ни одна

Ответь ТОЛЬКО JSON без markdown:
{"items": [{"name": "название", "amount": число, "category": "🍜 Продукты"}], "total": число}
Если не удалось распознать → {"items": [], "total": 0}
"""


async def parse_receipt(image_bytes: bytes, media_type: str = "image/jpeg") -> Optional[dict]:
    """Парсить фото финансовой операции через Claude Vision.

    Возвращает {"items": [...], "total": N} или None если не распознано/ошибка.
    """
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        raw = await ask_claude_vision(
            prompt="Распарси финансовую операцию на изображении. Только JSON.",
            image_b64=image_b64,
            media_type=media_type,
            system=_RECEIPT_SYSTEM,
        )
        if not raw:
            return None

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)

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
