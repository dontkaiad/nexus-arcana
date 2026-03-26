"""core/vision.py — парсинг фото финансовых операций через Claude Vision."""
from __future__ import annotations

import base64
import json
import logging
import math
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
3. Тип: доход или расход

РАЗЛИЧАЙ ДОХОДЫ И РАСХОДЫ:
- Сумма с "+" или написано "зачисление/пополнение/перевод от/входящий" → type: "income"
- Сумма с "-" или написано "покупка/оплата/списание/перевод/исходящий" → type: "expense"
- Если непонятно → type: "expense"

КАТЕГОРИИ РАСХОДОВ:
🐾 Коты: зоомагазин, корм, ветеринар
🍜 Продукты: супермаркет, Лента, Пятёрочка, Магнит, продукты
🍱 Кафе/Доставка: ресторан, кафе, Яндекс Еда, Delivery Club
🚕 Транспорт: такси, Яндекс Go, метро, автобус
🚬 Привычки: табак, сигареты
💅 Бьюти: салон, маникюр, парикмахерская
🏥 Здоровье: аптека, клиника, врач
💻 Подписки: подписка, Netflix, Spotify, YouTube
🏠 Жилье: ЖКХ, аренда, электричество
👗 Гардероб: одежда, обувь
💳 Прочее: если не подходит ни одна

КАТЕГОРИИ ДОХОДОВ:
💰 Зарплата: зарплата, аванс
💳 Прочее: переводы, возвраты, прочее

МАРКЕТПЛЕЙСЫ (Ozon, Wildberries, Яндекс Маркет):
Если видна только общая сумма без конкретных позиций → category: "❓ Маркетплейс"
Не угадывай категорию для маркетплейсов!

МАГАЗИНЫ С РАЗНЫМИ ТОВАРАМИ (Красное&Белое, Лента, Ашан, Fix Price):
Если видны конкретные позиции чека → разбей по категориям.
Если видна только общая сумма → category: "❓ Уточнить"
(лучше спросить, чем угадать неправильно)

Ответь ТОЛЬКО JSON без markdown:
{"items": [{"name": "название", "amount": число, "type": "expense", "category": "🍜 Продукты"}], "total": число}
Если не удалось распознать → {"items": [], "total": 0}
"""


async def parse_receipt(image_bytes: bytes, media_type: str = "image/jpeg") -> Optional[dict]:
    """Парсить фото финансовой операции через Claude Vision.

    Возвращает {"items": [...], "total": N} или None если не распознано/ошибка.
    Суммы округляются вверх до целых рублей.
    """
    raw = ""
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

        # Округление сумм до целых (ceil)
        for item in items:
            amt = item.get("amount", 0)
            if isinstance(amt, (int, float)):
                item["amount"] = math.ceil(abs(amt))
            # Дефолт типа
            if "type" not in item:
                item["type"] = "expense"

        total = result.get("total") or sum(it.get("amount", 0) for it in items)
        total = math.ceil(abs(total)) if isinstance(total, (int, float)) else total
        return {"items": items, "total": total}

    except json.JSONDecodeError as e:
        logger.warning("parse_receipt: JSON parse error: %s (raw=%s)", e, raw[:200] if raw else "")
        return None
    except Exception as e:
        logger.error("parse_receipt: %s", e)
        return None
