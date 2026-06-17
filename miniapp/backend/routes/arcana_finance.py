"""miniapp/backend/routes/arcana_finance.py — Касса Арканы + P&L + выплата.

Pay-self записывает ОДНУ запись в Финансы Бот=Nexus, Тип=Доход, Категория=Зарплата.
Касса вычитает её. Никаких новых категорий.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from core.cash_register import (
    BOT_NEXUS,
    SALARY_CATEGORY,
    compute_pnl,
)
from core.repos.finance_repo import FinanceRepo
from core.user_manager import get_user_notion_id
from core.bot_notify import notify_user

from miniapp.backend.auth import current_user_id
from core.repos.idempotency_repo import idempotent

logger = logging.getLogger("miniapp.arcana.finance")

router = APIRouter()

_fin_repo = FinanceRepo()


@router.get("/arcana/finance/pnl")
async def get_pnl(
    period: str = Query("current_month"),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    today = _date.today()
    year, month = today.year, today.month
    # period поддерживает только current_month сейчас
    if period and period != "current_month":
        # «2026-04» формата
        try:
            y, m = period.split("-")
            year, month = int(y), int(m)
        except Exception:
            pass
    return await compute_pnl(user_notion_id, year, month)


class PaySalaryBody(BaseModel):
    amount: float = Field(gt=0)
    description: Optional[str] = None
    force: bool = False  # если касса < amount → потребуется force=True (или предупредить)


@router.post("/arcana/finance/pay_salary")
async def pay_salary(
    body: PaySalaryBody,
    tg_id: int = Depends(current_user_id),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    today = _date.today()
    pnl = await compute_pnl(user_notion_id, today.year, today.month)
    cash = pnl["cash_balance"]
    if body.amount > cash and not body.force:
        return {
            "ok": False,
            "warning": "low_cash",
            "cash_balance": cash,
            "message": f"В кассе {cash:,}₽ — выплатить всё равно? Передай force=true.",
        }

    async def _run() -> dict:
        fin_id = await _fin_repo.add(
            date=today.strftime("%Y-%m-%d"),
            amount=float(body.amount),
            category=SALARY_CATEGORY,
            type_="💰 Доход",
            source="💳 Карта",
            description=body.description or "Выплата себе",
            bot_label=BOT_NEXUS,
            user_notion_id=user_notion_id,
        )
        new_cash = cash - body.amount
        await notify_user(
            tg_id,
            f"💰 Выплата себе: <b>{int(round(body.amount))}₽</b> (в кассе осталось {int(round(new_cash))}₽)",
            bot="arcana",
        )
        return {
            "ok": True,
            "finance_id": fin_id,
            "amount": int(round(body.amount)),
            "cash_balance_before": cash,
            "cash_balance_after": int(round(new_cash)),
        }

    return await idempotent(tg_id, idempotency_key, _run)
