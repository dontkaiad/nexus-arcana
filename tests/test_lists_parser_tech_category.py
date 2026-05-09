"""tests/test_lists_parser_tech_category.py — категория 💻 Техника в Haiku промпте.

Контекст: до этого фикса электроника парсилась в 💻 Подписки (галлюцинация
Haiku, потому что Подписки — единственная «техно»-связанная категория).
Теперь добавлена 💻 Техника + few-shot для устройств и аксессуаров.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


from core.list_manager import LIST_CATEGORIES
from core.lists_parser import build_buy_system, parse_buy_text


# ── Constants & prompt ────────────────────────────────────────────────────────


def test_tech_category_in_list():
    assert "💻 Техника" in LIST_CATEGORIES


def test_prompt_mentions_tech_category():
    sys_p = build_buy_system()
    assert "💻 Техника" in sys_p
    # Должны быть явные few-shot, иначе Haiku по-прежнему будет путать с 💻 Подписки
    assert "iPhone" in sys_p or "ноутбук" in sys_p
    assert "💻 Подписки" in sys_p  # обязан остаться чтобы было противопоставление


def test_prompt_distinguishes_subscription_vs_device():
    sys_p = build_buy_system()
    low = sys_p.lower()
    assert "не путать" in low or "не путай" in low or "разовая" in low


# ── Mock Haiku — happy path ───────────────────────────────────────────────────


def test_parse_assigns_tech_category_for_phone():
    """Если Haiku возвращает 💻 Техника для гаджета — нормализуется корректно."""
    mock_resp = (
        '{"items":[{"name":"телефон","category":"💻 Техника",'
        '"price_plan":50000,"source":"Ozon","stage":null,'
        '"group":null,"note":null,"priority":null,"qty":null,"expires":null}]}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text("купи телефон 50к в Ozon"))

    assert len(items) == 1
    assert items[0]["name"] == "телефон"
    assert items[0]["category"] == "💻 Техника"
    assert items[0]["price_plan"] == 50000
    assert items[0]["source"] == "Ozon"


def test_parse_grouped_list_with_multiple_tech_items():
    """Реалистичный случай: список из нескольких устройств в группе с ценами."""
    mock_resp = (
        '{"items":['
        '{"name":"телефон","category":"💻 Техника","price_plan":100000,'
        '"source":"магазин","stage":null,"group":"тех-апгрейд","note":null,'
        '"priority":null,"qty":null,"expires":null},'
        '{"name":"наушники","category":"💻 Техника","price_plan":25000,'
        '"source":"магазин","stage":null,"group":"тех-апгрейд","note":null,'
        '"priority":null,"qty":null,"expires":null},'
        '{"name":"ремешок","category":"💻 Техника","price_plan":3000,'
        '"source":null,"stage":null,"group":"тех-апгрейд","note":null,'
        '"priority":null,"qty":null,"expires":null}'
        ']}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text(
            "добавь в тех-апгрейд: телефон 100к в магазин, наушники 25к, ремешок 3к"
        ))

    assert len(items) == 3
    assert all(i["category"] == "💻 Техника" for i in items)
    assert all(i["group"] == "тех-апгрейд" for i in items)


# ── Регресс: продукты остаются 🍜 Продукты ───────────────────────────────────


def test_parse_food_stays_in_products_category():
    """«молоко 89р» → 🍜 Продукты, не 💻 Техника."""
    mock_resp = (
        '{"items":[{"name":"молоко","category":"🍜 Продукты",'
        '"price_plan":89,"source":null,"stage":null,'
        '"group":null,"note":null,"priority":null,"qty":null,"expires":null}]}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text("купи молоко 89р"))

    assert items[0]["category"] == "🍜 Продукты"


def test_parse_arcana_supplies_unchanged():
    """Регресс Arcana: «свеча красная 50р» → 🕯️ Расходники."""
    mock_resp = (
        '{"items":[{"name":"свеча красная","category":"🕯️ Расходники",'
        '"price_plan":50,"source":null,"stage":null,'
        '"group":null,"note":null,"priority":null,"qty":null,"expires":null}]}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text(
            "купи свеча красная 50р", bot_hint="🌒 Arcana",
        ))

    assert items[0]["category"] == "🕯️ Расходники"
