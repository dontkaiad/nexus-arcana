"""core/payment.py — единый интерфейс записи оплат для 🃏 Расклады и 🕯 Ритуалы.

Не пишем в 💰 Финансы — все денежные данные арканных событий хранятся прямо
в записях раскладов/ритуалов. Финансы Арканы будут отделены от Нексуса позже.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("core.payment")

# Канонические значения select-полей в Notion (синхронны с CONTEXT-схемой задачи).
SOURCE_CASH = "💵 Наличные"
SOURCE_CARD = "💳 Карта"
SOURCE_BARTER = "🔄 Бартер"


def _field_map(target: str) -> dict[str, str]:
    """target='sessions' | 'rituals' → имена полей Notion."""
    if target == "sessions":
        return {
            "sum": "Сумма",
            "source": "Источник",
            "paid": "Оплачено",
            "barter_what": "Бартер · что",
        }
    if target == "rituals":
        return {
            "sum": "Цена за ритуал",
            "source": "Источник оплаты",
            "paid": "Оплачено",
            "barter_what": "Бартер · что",
        }
    raise ValueError(f"unknown target: {target}")


def parse_amount(text: str) -> Optional[int]:
    """'500' / '500₽' / '500р' / '1.5к' / '2,5к' → int рублей. Иначе None."""
    if not text:
        return None
    s = text.strip().lower().replace(" ", "")
    s = s.replace("₽", "").replace("руб", "").replace("р", "")
    m = re.match(r"^(\d+[.,]?\d*)к$", s)
    if m:
        try:
            return int(float(m.group(1).replace(",", ".")) * 1000)
        except ValueError:
            return None
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return None


def build_payment_props(
    target: str,
    kind: str,
    amount: int = 0,
    barter_what: str = "",
) -> dict:
    """kind: money | gift | debt | barter_done | barter_wait | barter_to_money.

    Возвращает dict для notion update_page (props), не вызывает API сам.
    """
    fm = _field_map(target)
    props: dict = {}
    if kind == "money":
        props[fm["sum"]] = {"number": amount}
        props[fm["paid"]] = {"number": amount}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    elif kind == "gift":
        props[fm["sum"]] = {"number": 0}
        props[fm["paid"]] = {"number": 0}
    elif kind == "debt":
        props[fm["sum"]] = {"number": amount}
        props[fm["paid"]] = {"number": 0}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    elif kind in ("barter_done", "barter_wait"):
        # Бартер не учитывается в денежном доходе. Если получен — Сумма=0,
        # Оплачено=0 (формула Долг=0). Если ждём — также Сумма=0/Оплачено=0,
        # но вместе с тем поле «Бартер · что» заполнено и Источник=Бартер.
        props[fm["sum"]] = {"number": 0}
        props[fm["paid"]] = {"number": 0}
        props[fm["source"]] = {"select": {"name": SOURCE_BARTER}}
        if barter_what:
            props[fm["barter_what"]] = {
                "rich_text": [{"text": {"content": barter_what[:200]}}]
            }
    elif kind == "barter_to_money":
        props[fm["sum"]] = {"number": amount}
        props[fm["paid"]] = {"number": amount}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    else:
        raise ValueError(f"unknown payment kind: {kind}")
    return props


async def write_payment(
    page_id: str,
    target: str,
    kind: str,
    amount: int = 0,
    barter_what: str = "",
) -> None:
    """Применяет props к Notion. Падение пишем в лог, не пробрасываем —
    callback-handler сам решит как реагировать."""
    from core.notion_client import update_page
    props = build_payment_props(target, kind, amount=amount, barter_what=barter_what)
    try:
        await update_page(page_id, props)
    except Exception as e:
        logger.error(
            "write_payment failed: target=%s kind=%s pid=%s err=%s",
            target, kind, page_id[:8], e,
        )
        raise


async def resolve_barter_received(page_id: str, target: str) -> None:
    """[✅ Получила] на pending-бартере — выставляет Оплачено = Сумма (если она
    задана и >0). Если Сумма=0 — оставляет как есть (бартер «без рублёвой
    эквивалентной цены», просто помечен полученным через Источник)."""
    from core.notion_client import get_page, update_page
    fm = _field_map(target)
    page = await get_page(page_id)
    sum_val = (page.get("properties", {}).get(fm["sum"], {}) or {}).get("number") or 0
    if sum_val and sum_val > 0:
        await update_page(page_id, {fm["paid"]: {"number": sum_val}})
