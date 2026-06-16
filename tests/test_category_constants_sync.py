"""tests/test_category_constants_sync.py — sync констант категорий с
живой Notion-схемой (NOTION_DATABASES_v4.md).

Контекст: автогенерация v4 нашла 3 расхождения между константами в коде
и опциями select-полей в Notion. Этот файл закрепляет фикс всех трёх:
1) 🔄 Бартер в LIST_CATEGORIES, 2) три финансовые категории в classifier
prompt, 3) 🤖 Боты убрана из finance-cats (есть только в ✅ Задачах).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch



# ── Расхождение 1: 🔄 Бартер в LIST_CATEGORIES ───────────────────────────────


def test_list_categories_includes_barter():
    """🔄 Бартер должна быть в LIST_CATEGORIES (есть в Notion опциях)."""
    from core.list_manager import LIST_CATEGORIES
    assert "🔄 Бартер" in LIST_CATEGORIES


def test_lists_parser_prompt_lists_barter():
    """build_buy_system передаёт LIST_CATEGORIES в промпт — Бартер должен попасть."""
    from core.lists_parser import build_buy_system
    sys_p = build_buy_system()
    assert "🔄 Бартер" in sys_p


def test_parse_buy_accepts_barter_category():
    """Mock Haiku возвращает category=🔄 Бартер — нормализация её сохраняет."""
    from core.lists_parser import parse_buy_text

    mock_resp = (
        '{"items":[{"name":"свечи в обмен на расклад","category":"🔄 Бартер",'
        '"price_plan":null,"source":null,"stage":null,"group":null,'
        '"note":null,"priority":null,"qty":null,"expires":null}]}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text("в бартер: свечи на расклад"))
    assert items[0]["category"] == "🔄 Бартер"


# ── Расхождение 2: 3 финансовые категории в classifier prompt ────────────────


def test_classifier_prompt_includes_impulsive():
    """🎲 Импульсивные должна быть в системном промпте expense-категорий."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3)
    assert "🎲 Импульсивные" in sys_p


def test_classifier_prompt_includes_practice():
    """🔮 Практика — услуги для практики (наставник, курсы)."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3)
    assert "🔮 Практика" in sys_p


def test_classifier_prompt_includes_supplies():
    """🕯️ Расходники — материалы (соль, травы, свечи)."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3)
    assert "🕯️ Расходники" in sys_p


def test_classifier_prompt_distinguishes_practice_vs_supplies():
    """Промпт должен различать практику (услуги) и расходники (материалы)."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3).lower()
    # Услуги/обучение в Практике
    assert "услуги" in sys_p or "обучение" in sys_p
    # Материалы в Расходниках
    assert "материалы" in sys_p


def test_classifier_prompt_has_few_shot_for_impulsive():
    """Должен быть пример с явным маркером 'импульсивно/спонтанно/сорвалась'."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3).lower()
    assert any(m in sys_p for m in ("импульсивно", "спонтанно", "сорвал"))


# ── Расхождение 3: 🤖 Боты убрана из finance-cats ─────────────────────────────


def test_classifier_finance_cats_excludes_bots():
    """🤖 Боты — task-категория, не finance. В expense/income JSON-примерах
    её быть не должно (иначе Notion silent-create добавит опцию)."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3)
    # Проверяем именно expense-блок, чтобы не зацепить task-промпт ниже,
    # где 🤖 Боты валидна как категория ✅ Задач.
    expense_idx = sys_p.find('"type":"expense"')
    income_idx = sys_p.find('"type":"income"')
    update_idx = sys_p.find('"type":"update"')
    assert expense_idx > 0 and income_idx > 0 and update_idx > 0
    finance_block = sys_p[expense_idx:update_idx]
    assert "🤖 Боты" not in finance_block, (
        "🤖 Боты в finance-prompt: в БД 💰 Финансы.Категория такой опции нет"
    )


# ── Регресс: существующие категории не сломаны ───────────────────────────────


def test_classifier_prompt_keeps_legacy_categories():
    """Регресс: старые категории не удалены (Коты/Жильё/Подписки/...)."""
    from core.classifier import build_system
    sys_p = build_system(tz_offset=3)
    for cat in ("🐾 Коты", "🏠 Жильё", "🚬 Привычки", "🍜 Продукты",
                "💻 Подписки", "💰 Зарплата", "💳 Прочее"):
        assert cat in sys_p, f"missing legacy category: {cat}"


def test_list_categories_keeps_legacy():
    """Регресс: список не теряет существующие категории."""
    from core.list_manager import LIST_CATEGORIES
    for cat in ("🐾 Коты", "🍜 Продукты", "💻 Техника", "💳 Прочее",
                "🕯️ Расходники", "🃏 Карты/Колоды"):
        assert cat in LIST_CATEGORIES, f"missing legacy list category: {cat}"


def test_lists_parser_arcana_supplies_unchanged():
    """Регресс Arcana: «свеча красная 50р» по-прежнему 🕯️ Расходники."""
    from core.lists_parser import parse_buy_text

    mock_resp = (
        '{"items":[{"name":"свеча красная","category":"🕯️ Расходники",'
        '"price_plan":50,"source":null,"stage":null,"group":null,'
        '"note":null,"priority":null,"qty":null,"expires":null}]}'
    )
    with patch("core.lists_parser.ask_claude",
               AsyncMock(return_value=mock_resp)):
        items = asyncio.run(parse_buy_text("купи свеча красная 50р",
                                           bot_hint="🌒 Arcana"))
    assert items[0]["category"] == "🕯️ Расходники"
