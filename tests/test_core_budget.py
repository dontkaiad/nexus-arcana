"""Unit tests для core/budget.py — парсинг бюджетных записей из Памяти."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.budget import (
    BUDGET_KEY_TO_CATEGORY,
    DEBT_RE,
    GOAL_RE,
    INCOME_RE,
    LIMIT_AMOUNT_RE,
    LIMIT_FACT_RE,
    OBLIGATORY_RE,
    cat_link,
    display_limit_name,
    load_budget_data,
    parse_amount,
)


def test_parse_amount_various_formats():
    assert parse_amount("15000") == 15000.0
    assert parse_amount("15 000") == 15000.0
    assert parse_amount("15,5") == 15.5
    assert parse_amount("1 234.56") == pytest.approx(1234.56)


def test_cat_link_strips_emoji_and_slash():
    assert cat_link("🚬 Привычки") == "привычки"
    assert cat_link("🍱 Кафе/Доставка") == "кафе"
    assert cat_link("💳 Прочее") == "прочее"
    assert cat_link("Здоровье") == "здоровье"


def test_display_limit_name_mapping():
    assert display_limit_name("привычки") == "🚬 Привычки"
    assert display_limit_name("лимит_импульсивные") == "🎲 Импульсивные"
    # fallback: неизвестное имя возвращается как есть
    assert display_limit_name("шмурдяк") == "шмурдяк"


def test_budget_key_to_category_map_complete():
    # все 5 префиксов должны быть мапаны на Notion-категории
    assert set(BUDGET_KEY_TO_CATEGORY) == {
        "income_", "обязательно_", "лимит_", "цель_", "долг_"
    }


# ── Regex: каждый тип записи ────────────────────────────────────────────────

def test_income_regex_extracts_name_and_amount():
    m = INCOME_RE.search("доход: зарплата — 115 000₽/мес")
    assert m
    assert m.group(1).strip() == "зарплата"
    assert parse_amount(m.group(2)) == 115000.0


def test_obligatory_regex():
    m = OBLIGATORY_RE.search("обязательно: аренда — 40000₽")
    assert m and parse_amount(m.group(2)) == 40000.0


def test_goal_regex_with_monthly_saving():
    m = GOAL_RE.search("цель: Samsung Flip — 100 000₽ · откладываю 8000₽")
    assert m
    assert m.group(1).strip() == "Samsung Flip"
    assert parse_amount(m.group(2)) == 100000.0
    assert parse_amount(m.group(3)) == 8000.0


def test_goal_regex_without_saving():
    m = GOAL_RE.search("цель: подушка — 50 000₽")
    assert m
    assert m.group(3) is None


def test_debt_regex_full():
    fact = "долг: Вика — 50 000₽ · дедлайн: апрель · стратегия: равными частями · платёж: 12500"
    m = DEBT_RE.search(fact)
    assert m
    assert m.group(1).strip() == "Вика"
    assert parse_amount(m.group(2)) == 50000.0
    assert m.group(3).strip() == "апрель"
    assert m.group(4).strip().startswith("равными")
    assert parse_amount(m.group(5)) == 12500.0


def test_debt_regex_minimal():
    m = DEBT_RE.search("долг: Аня — 3000₽")
    assert m
    assert m.group(3) is None  # нет дедлайна


def test_limit_fact_regex():
    m = LIMIT_FACT_RE.search("лимит: 🚬 Привычки — 17685₽/мес")
    assert m
    assert "Привычки" in m.group(1)
    assert parse_amount(m.group(2)) == 17685.0


def test_limit_amount_regex_extracts_currency_number():
    m = LIMIT_AMOUNT_RE.search("лимит: что-то — 5 000₽/мес")
    assert m and m.group(1).replace(" ", "") == "5000"


# ── load_budget_data: интеграционный тест с мок-Notion ──────────────────────

@pytest.mark.asyncio
async def test_load_budget_data_routes_keys_into_buckets(monkeypatch):
    """Проверяем что load_budget_data корректно раскладывает записи по bucket'ам
    по prefix ключа и парсит суммы/поля."""
    monkeypatch.setenv("NOTION_DB_MEMORY", "fake-mem-db")

    def page(key: str, fact: str, extra: dict | None = None):
        props = {
            "Ключ": {"rich_text": [{"plain_text": key}]},
            "Текст": {"title": [{"plain_text": fact}]},
            "Актуально": {"checkbox": True},
        }
        if extra:
            props.update(extra)
        return {"properties": props}

    fake_pages = [
        page("income_salary", "доход: зарплата — 115 000₽/мес"),
        page("обязательно_rent", "обязательно: аренда — 40000₽"),
        page("цель_flip", "цель: Samsung Flip — 100 000₽ · откладываю 8000₽"),
        page("долг_vika", "долг: Вика — 50 000₽ · дедлайн: апрель"),
        page("лимит_habits", "лимит: 🚬 Привычки — 17685₽/мес",
             extra={"Связь": {"rich_text": [{"plain_text": "привычки"}]}}),
    ]

    async def fake_db_query(*_, **__):
        return fake_pages

    with patch("core.notion_client.db_query", side_effect=fake_db_query):
        data = await load_budget_data()

    assert len(data["доходы"]) == 1 and data["доходы"][0]["amount"] == 115000
    assert len(data["обязательные"]) == 1
    assert len(data["цели"]) == 1
    assert data["цели"][0]["saving"] == 8000
    assert data["цели"][0]["target"] == 100000
    assert len(data["долги"]) == 1
    assert data["долги"][0]["deadline"] == "апрель"
    assert len(data["лимиты"]) == 1
    assert data["лимиты"][0]["amount"] == 17685


@pytest.mark.asyncio
async def test_load_budget_data_skips_inactive(monkeypatch):
    monkeypatch.setenv("NOTION_DB_MEMORY", "fake-mem-db")

    fake_pages = [{
        "properties": {
            "Ключ": {"rich_text": [{"plain_text": "цель_test"}]},
            "Текст": {"title": [{"plain_text": "цель: X — 1000₽"}]},
            "Актуально": {"checkbox": False},
        }
    }]

    async def fake_db_query(*_, **__):
        return fake_pages

    with patch("core.notion_client.db_query", side_effect=fake_db_query):
        data = await load_budget_data()

    assert data["цели"] == []
