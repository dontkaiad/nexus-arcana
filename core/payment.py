"""core/payment.py — единый интерфейс записи оплат для 🃏 Расклады и 🕯 Ритуалы.

Не пишем в 💰 Финансы — все денежные данные арканных событий хранятся прямо
в записях раскладов/ритуалов.
"""
from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("core.payment")

# Канонические значения select-полей Notion (используются в build_payment_props).
SOURCE_CASH   = "💵 Наличные"
SOURCE_CARD   = "💳 Карта"
SOURCE_BARTER = "🔄 Бартер"

# PG payment_source codes
_PG_SRC_CASH   = "cash"
_PG_SRC_BARTER = "barter"


def _field_map(target: str) -> dict:
    """target='sessions' | 'rituals' → имена полей Notion (для build_payment_props)."""
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
    Используется в тестах и для проверки логики без записи в БД.
    """
    fm = _field_map(target)
    props: dict = {}
    if kind == "money":
        props[fm["sum"]]    = {"number": amount}
        props[fm["paid"]]   = {"number": amount}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    elif kind == "gift":
        props[fm["sum"]]  = {"number": 0}
        props[fm["paid"]] = {"number": 0}
    elif kind == "debt":
        props[fm["sum"]]    = {"number": amount}
        props[fm["paid"]]   = {"number": 0}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    elif kind in ("barter_done", "barter_wait"):
        props[fm["sum"]]    = {"number": 0}
        props[fm["paid"]]   = {"number": 0}
        props[fm["source"]] = {"select": {"name": SOURCE_BARTER}}
        if barter_what:
            props[fm["barter_what"]] = {
                "rich_text": [{"text": {"content": barter_what[:200]}}]
            }
    elif kind == "barter_to_money":
        props[fm["sum"]]    = {"number": amount}
        props[fm["paid"]]   = {"number": amount}
        props[fm["source"]] = {"select": {"name": SOURCE_CASH}}
    else:
        raise ValueError(f"unknown payment kind: {kind}")
    return props


def _resolve_src_id(conn, payment_source_table, code: str) -> Optional[int]:
    from sqlalchemy import select
    row = conn.execute(
        select(payment_source_table.c.id).where(payment_source_table.c.code == code)
    ).fetchone()
    return row[0] if row else None


def _write_payment_sync(
    page_id: str,
    target: str,
    kind: str,
    amount: int = 0,
    barter_what: str = "",
) -> None:
    from core.db import get_engine

    try:
        pid = int(page_id)
    except (ValueError, TypeError):
        logger.error("write_payment: invalid page_id %r", page_id)
        raise ValueError(f"invalid page_id: {page_id!r}")

    if target == "sessions":
        from arcana.repos.sessions_tables import sessions, payment_source
        table     = sessions
        sum_field = "amount"
    elif target == "rituals":
        from arcana.repos.rituals_tables import rituals, payment_source
        table     = rituals
        sum_field = "price"
    else:
        raise ValueError(f"unknown target: {target}")

    vals: dict = {}
    src_code: Optional[str] = None

    if kind == "money":
        vals[sum_field] = Decimal(str(amount))
        vals["paid"]    = Decimal(str(amount))
        src_code = _PG_SRC_CASH
    elif kind == "gift":
        vals[sum_field] = Decimal("0")
        vals["paid"]    = Decimal("0")
    elif kind == "debt":
        vals[sum_field] = Decimal(str(amount))
        vals["paid"]    = Decimal("0")
        src_code = _PG_SRC_CASH
    elif kind in ("barter_done", "barter_wait"):
        vals[sum_field] = Decimal("0")
        vals["paid"]    = Decimal("0")
        src_code = _PG_SRC_BARTER
        if barter_what:
            vals["barter_what"] = barter_what[:200]
    elif kind == "barter_to_money":
        vals[sum_field] = Decimal(str(amount))
        vals["paid"]    = Decimal(str(amount))
        src_code = _PG_SRC_CASH
    else:
        raise ValueError(f"unknown payment kind: {kind}")

    with get_engine().begin() as conn:
        if src_code:
            src_id = _resolve_src_id(conn, payment_source, src_code)
            if src_id:
                vals["payment_src_id"] = src_id
        conn.execute(table.update().where(table.c.id == pid).values(**vals))


def _resolve_barter_received_sync(page_id: str, target: str) -> None:
    from core.db import get_engine
    from sqlalchemy import select

    try:
        pid = int(page_id)
    except (ValueError, TypeError):
        logger.error("resolve_barter_received: invalid page_id %r", page_id)
        return

    if target == "sessions":
        from arcana.repos.sessions_tables import sessions
        table   = sessions
        sum_col = sessions.c.amount
    elif target == "rituals":
        from arcana.repos.rituals_tables import rituals
        table   = rituals
        sum_col = rituals.c.price
    else:
        return

    with get_engine().begin() as conn:
        row = conn.execute(select(sum_col).where(table.c.id == pid)).fetchone()
        if row and row[0] and row[0] > 0:
            conn.execute(table.update().where(table.c.id == pid).values(paid=row[0]))


async def write_payment(
    page_id: str,
    target: str,
    kind: str,
    amount: int = 0,
    barter_what: str = "",
) -> None:
    """Записывает оплату в PG sessions или rituals."""
    try:
        await asyncio.to_thread(
            _write_payment_sync, page_id, target, kind, amount, barter_what
        )
    except Exception as e:
        logger.error(
            "write_payment failed: target=%s kind=%s pid=%s err=%s",
            target, kind, str(page_id)[:8], e,
        )
        raise


async def resolve_barter_received(page_id: str, target: str) -> None:
    """[✅ Получила] на pending-бартере — выставляет Оплачено = Сумма."""
    await asyncio.to_thread(_resolve_barter_received_sync, page_id, target)
