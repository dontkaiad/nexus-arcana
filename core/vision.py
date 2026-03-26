"""core/vision.py — парсинг фото финансовых операций через Claude Vision."""
from __future__ import annotations

import base64
import json
import logging
import math
from typing import Optional

from core.claude_client import ask_claude_vision

logger = logging.getLogger("nexus.vision")

# Валидные категории — ТОЛЬКО из существующих в Notion
_VALID_EXPENSE_CATS = {
    "🐾 Коты", "🍜 Продукты", "🍱 Кафе/Доставка", "🚕 Транспорт",
    "🚬 Привычки", "💅 Бьюти", "🏥 Здоровье", "💻 Подписки",
    "🏠 Жилье", "👗 Гардероб", "💳 Прочее",
}
_VALID_INCOME_CATS = {"💰 Зарплата", "💳 Прочее"}

_RECEIPT_SYSTEM = """Ты парсишь изображение финансовой операции. Это может быть:
- Скриншот из банковского приложения (Сбер, Тинькофф, Альфа и др.)
- Скриншот истории операций
- Бумажный чек из магазина

Определи ИСТОЧНИК изображения:
- "bank_app" — скриншот банковского приложения (есть логотип банка, интерфейс приложения)
- "paper_receipt" — бумажный чек или скан

Извлеки:
1. Каждую операцию/позицию: название (получатель или товар) и сумму
2. Тип каждой операции: доход или расход

РАЗЛИЧАЙ ДОХОДЫ И РАСХОДЫ:
- Сумма с "+" или написано "зачисление/пополнение/перевод от/входящий" → type: "income"
- Сумма с "-" или написано "покупка/оплата/списание/перевод/исходящий" → type: "expense"
- Переводы ОТ других людей (входящие) = income
- Переводы другим людям (исходящие) = expense
- Если непонятно → type: "expense"

КАТЕГОРИИ РАСХОДОВ (ТОЛЬКО из этого списка, других нет):
🐾 Коты: зоомагазин, корм, ветеринар
🍜 Продукты: супермаркет, Лента, Пятёрочка, Магнит
🍱 Кафе/Доставка: ресторан, кафе, Яндекс Еда, Delivery Club
🚕 Транспорт: такси, Яндекс Go, метро, автобус
🚬 Привычки: табак, сигареты, энергетики
💅 Бьюти: салон, маникюр, парикмахерская
🏥 Здоровье: аптека, клиника, врач
💻 Подписки: подписка, Netflix, Spotify, YouTube
🏠 Жилье: ЖКХ, аренда, электричество
👗 Гардероб: одежда, обувь
💳 Прочее: всё остальное (маркетплейсы без позиций, неизвестное)

КАТЕГОРИИ ДОХОДОВ (ТОЛЬКО из этого списка):
💰 Зарплата: зарплата, аванс
💳 Прочее: переводы, возвраты

МАРКЕТПЛЕЙСЫ (Ozon, Wildberries, Яндекс Маркет) без видимых позиций:
Ставь category = "💳 Прочее" и need_clarify = true

МАГАЗИНЫ (Красное&Белое, Лента) без видимых позиций чека:
Ставь category = "💳 Прочее" и need_clarify = true

Ответь ТОЛЬКО JSON без markdown:
{"source": "bank_app", "items": [{"name": "...", "amount": число, "type": "expense", "category": "🍜 Продукты", "need_clarify": false}]}
Если не удалось распознать → {"source": "unknown", "items": []}
"""


async def parse_receipt(image_bytes: bytes, media_type: str = "image/jpeg") -> Optional[dict]:
    """Парсить фото финансовой операции через Claude Vision.

    Возвращает {"source": str, "items": [...], "total_expense": N, "total_income": N}
    или None если не распознано/ошибка.
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

        source = result.get("source", "unknown")

        # Валидация и округление
        for item in items:
            amt = item.get("amount", 0)
            if isinstance(amt, (int, float)):
                item["amount"] = math.ceil(abs(amt))

            # Дефолт типа
            if item.get("type") not in ("income", "expense"):
                item["type"] = "expense"

            # Валидация категории — только из разрешённых
            cat = item.get("category", "💳 Прочее")
            valid = _VALID_INCOME_CATS if item["type"] == "income" else _VALID_EXPENSE_CATS
            if cat not in valid:
                item["category"] = "💳 Прочее"
                item["need_clarify"] = True

        total_expense = sum(it["amount"] for it in items if it["type"] == "expense")
        total_income = sum(it["amount"] for it in items if it["type"] == "income")

        return {
            "source": source,
            "items": items,
            "total_expense": total_expense,
            "total_income": total_income,
        }

    except json.JSONDecodeError as e:
        logger.warning("parse_receipt: JSON parse error: %s (raw=%s)", e, raw[:200] if raw else "")
        return None
    except Exception as e:
        logger.error("parse_receipt: %s", e)
        return None
