"""tests/test_budget_sonnet_prompt.py — наличие инструкций в BUDGET_SONNET_SYSTEM.

Это промпт для Sonnet, не детерминированный код — проверяем только что нужные
инструкции присутствуют в тексте, не арифметику самого Sonnet.
"""
from __future__ import annotations

from nexus.handlers.finance import (
    BUDGET_SONNET_SYSTEM,
    _BUDGET_PARSE_PROMPT_LEGACY,
    _BUDGET_VARIABLE_CATS,
    _format_plan,
)

_BOTH_PROMPTS = {
    "BUDGET_SONNET_SYSTEM": BUDGET_SONNET_SYSTEM,
    "_BUDGET_PARSE_PROMPT_LEGACY": _BUDGET_PARSE_PROMPT_LEGACY,
}
_PRIORITY_ORDER = ["Кафе/Доставка", "Бьюти", "Здоровье", "Гардероб", "Хобби/Учеба"]


def test_prompt_has_step_1_5_subtract_spending():
    """Подшаг 1.5: уже потраченное в периоде вычитается из Распределяемых."""
    assert "Шаг 1.5" in BUDGET_SONNET_SYSTEM
    assert "spending_by_category" in BUDGET_SONNET_SYSTEM
    # вычитание already_spent из распределяемых, до долгов/лимитов
    assert "Распределяемые = Распределяемые − already_spent" in BUDGET_SONNET_SYSTEM
    assert "one_time_total" in BUDGET_SONNET_SYSTEM  # разовые из буфера входят в already_spent
    assert BUDGET_SONNET_SYSTEM.index("Шаг 1.5") < BUDGET_SONNET_SYSTEM.index("Шаг 2:")


def test_prompt_has_step_1_6_add_last_savings():
    """Подшаг 1.6: экономия прошлого периода прибавляется к Распределяемым."""
    assert "Шаг 1.6" in BUDGET_SONNET_SYSTEM
    assert "Распределяемые = Распределяемые + savings_from_last_period" in BUDGET_SONNET_SYSTEM
    # после 1.5, до долгов
    assert BUDGET_SONNET_SYSTEM.index("Шаг 1.5") < BUDGET_SONNET_SYSTEM.index("Шаг 1.6") < BUDGET_SONNET_SYSTEM.index("Шаг 2:")


def test_prompt_json_schema_has_already_spent():
    assert '"already_spent"' in BUDGET_SONNET_SYSTEM
    assert '"savings_from_last_period"' in BUDGET_SONNET_SYSTEM


def test_format_plan_shows_real_spending_when_positive_no_one_time():
    """already_spent без one_time → «реальные траты» (без слова «уже потрачено в периоде»)."""
    out = _format_plan({"already_spent": 12000, "income_total": 100000})
    assert "📤 Уже потрачено (реальные траты): 12,000₽" in out
    assert "Разовые" not in out


def test_format_plan_one_time_only_says_razovye_not_uzhe_potracheno():
    """Только разовые (реальных прошлых трат нет) → одна строка «Разовые в этом периоде»."""
    out = _format_plan({
        "already_spent": 8000, "one_time_total": 8000,
        "one_time": [{"name": "билет", "category": "🚕 Транспорт", "amount": 8000}],
        "income_total": 100000,
    })
    assert "📤 Разовые в этом периоде: 8,000₽" in out
    assert "Уже потрачено" not in out
    assert "из них разовых" not in out


def test_format_plan_real_and_one_time_shown_separately():
    """И реальные траты, и разовые из плана → две раздельные строки с понятными подписями."""
    out = _format_plan({
        "already_spent": 20000, "one_time_total": 8000,
        "one_time": [{"name": "билет", "category": "🚕 Транспорт", "amount": 8000}],
        "income_total": 100000,
    })
    assert "📤 Уже потрачено (реальные траты): 12,000₽" in out  # 20000 - 8000
    assert "📤 Разовые из этого плана: 8,000₽" in out
    assert "из них разовых" not in out


def test_format_plan_hides_already_spent_when_zero_or_missing():
    assert "потрачено" not in _format_plan({"already_spent": 0, "income_total": 100000})
    assert "Разовые" not in _format_plan({"already_spent": 0, "income_total": 100000})
    assert "потрачено" not in _format_plan({"income_total": 100000})


def test_format_plan_distributable_subtracts_already_spent():
    """'Распределяемые' в выводе = доход - фикс - already_spent (цифры бьются с низом)."""
    plan = {
        "income_total": 100000,
        "fixed": [{"name": "аренда", "category": "🏠 Жильё", "amount": 30000}],
        "fixed_total": 30000,
        "already_spent": 12000,
    }
    out = _format_plan(plan)
    # 100000 - 30000 - 12000 = 58000
    assert "💳 Распределяемые: <b>58,000₽</b>" in out
    assert "70,000₽" not in out  # старое поведение (без вычета) не должно светиться


def test_format_plan_distributable_adds_last_savings():
    """'Распределяемые' = доход - фикс - already_spent + savings_from_last_period."""
    plan = {
        "income_total": 100000,
        "fixed": [{"name": "аренда", "category": "🏠 Жильё", "amount": 30000}],
        "fixed_total": 30000,
        "already_spent": 12000,
        "savings_from_last_period": 5000,
    }
    out = _format_plan(plan)
    assert "🛡️ Экономия с прошлого периода: +5,000₽" in out
    # 100000 - 30000 - 12000 + 5000 = 63000
    assert "💳 Распределяемые: <b>63,000₽</b>" in out


def test_format_plan_income_block_comes_first():
    """Порядок чтения: заголовок → 📥 Доход → 📤 траты/разовые → 🔒 Фикс."""
    from nexus.handlers.finance import _format_plan
    plan = {
        "income_total": 100000,
        "income": [{"source": "ЗП", "amount": 100000}],
        "already_spent": 12000,
        "savings_from_last_period": 5000,
        "fixed": [{"name": "аренда", "category": "🏠 Жильё", "amount": 30000}],
        "fixed_total": 30000,
    }
    out = _format_plan(plan)
    i_income = out.index("📥 Доход")
    i_spent = out.index("Уже потрачено")
    i_savings = out.index("Экономия с прошлого периода")
    i_fixed = out.index("🔒 Фикс")
    assert i_income < i_spent < i_savings < i_fixed
    assert out.index("💰 Финансовый план") < i_income


# ── _format_limits_block: суффикс изменения ──────────────────────────────────

def test_format_limits_block_new_change_hidden():
    from nexus.handlers.finance import _format_limits_block
    out = "\n".join(_format_limits_block(
        [{"category": "🍜 Продукты", "amount": 15000, "change": "new"}], 15000))
    assert "(new)" not in out
    assert "new" not in out
    assert "🍜 Продукты — 15,000₽" in out


def test_format_limits_block_change_russian_suffix():
    from nexus.handlers.finance import _format_limits_block
    out = "\n".join(_format_limits_block([
        {"category": "🍜 Продукты", "amount": 18000, "change": "increased"},
        {"category": "🚕 Транспорт", "amount": 3000, "change": "decreased"},
    ], 21000))
    assert "🍜 Продукты — 18,000₽ (выросла)" in out
    assert "🚕 Транспорт — 3,000₽ (снизилась)" in out
    assert "increased" not in out and "decreased" not in out


def test_format_plan_hides_last_savings_when_zero_or_missing():
    assert "Экономия с прошлого периода" not in _format_plan(
        {"income_total": 100000, "savings_from_last_period": 0})
    assert "Экономия с прошлого периода" not in _format_plan({"income_total": 100000})


# ── Привычки: потолок 50% + приоритетный дележ остатка ──────────────────────

import pytest


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_habits_cap_50_percent_described(name, prompt):
    """Привычки ≤ discretionary_pool × 0.5 — процент, не абсолют. Оба промпта."""
    assert "discretionary_pool" in prompt, name
    assert "× 0.5" in prompt, name
    assert "ПОТОЛОК" in prompt, name
    # products_min правило не трогали
    assert "max(3000, привычки / 2)" in prompt, name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_priority_split_order(name, prompt):
    """Остаток пула делится по фиксированному приоритету в правильном порядке."""
    positions = [prompt.find(cat) for cat in _PRIORITY_ORDER]
    assert all(p != -1 for p in positions), f"{name}: не все категории приоритета найдены"
    assert positions == sorted(positions), f"{name}: порядок приоритета нарушен"


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_priority_split_in_both_normal_and_tight(name, prompt):
    """Правило описано и в общем распределении, и в варианте А тяжёлого месяца
    (два места, как было с бьюти — дублируются намеренно)."""
    assert prompt.count("discretionary_pool") >= 2, name
    # приоритетный список встречается минимум дважды (обычный + вариант А)
    assert prompt.count("🍱 Кафе/Доставка") >= 2, name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_budget_prompts_do_not_name_arcana_categories(name, prompt):
    """🔮 Практика и 🕯️ Расходники — категории Арканы, НЕ личного бюджета.
    В промптах их быть не должно даже как негативных примеров."""
    assert "Практика" not in prompt, name
    assert "Расходники" not in prompt, name


# ── Sonnet видит ТОЛЬКО 8 бюджетных категорий, не все 19 общих Финансов ─────

from core.config import FINANCE_CATEGORIES

_NON_BUDGET_CATS = [c for c in FINANCE_CATEGORIES if c not in _BUDGET_VARIABLE_CATS]


def test_budget_variable_cats_has_no_arcana():
    assert "🔮 Практика" not in _BUDGET_VARIABLE_CATS
    assert "🕯️ Расходники" not in _BUDGET_VARIABLE_CATS
    assert len(_BUDGET_VARIABLE_CATS) == 8


def test_budget_variable_cats_matches_priority_split():
    """_BUDGET_VARIABLE_CATS == 3 якорных + 5 приоритетного дележа (из прошлого
    коммита). Расхождений быть не должно — теперь список реально используется."""
    priority = ["🍱 Кафе/Доставка", "💅 Бьюти", "🏥 Здоровье", "👗 Гардероб", "📚 Хобби/Учеба"]
    anchors = ["🚬 Привычки", "🍜 Продукты", "🚕 Транспорт"]
    assert set(priority) | set(anchors) == set(_BUDGET_VARIABLE_CATS)


def test_legacy_prompt_limit_categories_only_8_no_leak():
    """Отрендеренный legacy-промпт: 8 бюджетных категорий есть, 11 остальных
    (Практика/Расходники/Зарплата/Фриланс/Коты/Жильё/…) отсутствуют."""
    rendered = _BUDGET_PARSE_PROMPT_LEGACY.format(
        all_messages="", budget_limit_categories=", ".join(_BUDGET_VARIABLE_CATS),
        current_date="", already_spent=0, savings_from_last_period=0,
    )
    for cat in _BUDGET_VARIABLE_CATS:
        assert cat in rendered, cat
    for cat in _NON_BUDGET_CATS:
        assert cat not in rendered, f"утечка не-бюджетной категории: {cat}"
    assert "{finance_categories}" not in _BUDGET_PARSE_PROMPT_LEGACY  # старый плейсхолдер убран


@pytest.mark.asyncio
async def test_sonnet_context_limit_categories_only_8_no_leak():
    """_build_sonnet_input кладёт в контекст ТОЛЬКО _BUDGET_VARIABLE_CATS,
    поля finance_categories больше нет, ни одна из 11 не-бюджетных не просочилась."""
    import json as _json
    from unittest.mock import AsyncMock, patch
    from nexus.handlers import finance

    empty = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[])), \
         patch.object(finance, "_budget_get", lambda uid: {}):
        raw = await finance._build_sonnet_input(uid=1, user_notion_id="u")

    ctx = _json.loads(raw)
    assert ctx["budget_limit_categories"] == list(_BUDGET_VARIABLE_CATS)
    assert "finance_categories" not in ctx
    for cat in _NON_BUDGET_CATS:
        assert cat not in raw, f"утечка не-бюджетной категории в контекст: {cat}"


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_prompts_instruct_limits_only_from_budget_list(name, prompt):
    assert "budget_limit_categories" in prompt, name


# ── Вариант Б только при реальном платеже по долгу ──────────────────────────

@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_fork_requires_active_debt_payment(name, prompt):
    """total_debt_payment == 0 в тяжёлый месяц → один план, без А/Б."""
    assert "total_debt_payment == 0" in prompt, name
    assert "total_debt_payment > 0" in prompt, name


# ── Разовые из composite-дампа: отдельный массив one_time, НЕ fixed ─────────

@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_prompt_splits_fixed_and_one_time(name, prompt):
    """Разовые (метка 'разовый:'/'разово:') → отдельный массив one_time,
    НЕ смешиваются с fixed, НЕ входят в fixed_total."""
    assert '"one_time"' in prompt, name
    assert '"one_time_total"' in prompt, name
    assert "one_time" in prompt and "fixed_total" in prompt, name
    # метка разового явно упомянута
    assert "разовый:" in prompt or "разово:" in prompt, name
    # приписка "(разовый)" в name больше НЕ нужна
    assert '"(разовый)"' in prompt or "(разовый)" in prompt, name  # упомянута как «НЕ добавлять»


def test_legacy_prompt_one_time_feeds_already_spent():
    """already_spent legacy = finance-траты + one_time_total (буфер composite-дампа)."""
    from nexus.handlers.finance import _BUDGET_PARSE_PROMPT_LEGACY as P
    assert "already_spent = {already_spent} (реальные finance-траты периода) + one_time_total" in P
    assert "one_time НЕ входит в fixed_total" in P


def test_sonnet_prompt_one_time_feeds_already_spent():
    from nexus.handlers.finance import BUDGET_SONNET_SYSTEM as S
    assert "one_time_total" in S
    # в разделе Шаг 1.5
    step = S.split("Шаг 1.5:")[1].split("Шаг 1.6:")[0]
    assert "one_time_total" in step


# ── fix: категория фикс-расходов эмодзи без дублей слов ───────────────────────

@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_fixed_category_is_emoji_prefix_not_word(name, prompt):
    """Категорию фикс-расхода ставить эмодзи-префиксом, НЕ словом."""
    assert "эмодзи-префиксом" in prompt, name
    assert "НЕ словом" in prompt or "не словом" in prompt.lower(), name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_fixed_category_no_word_duplication_rule(name, prompt):
    """Не дублировать слово, уже присутствующее в названии позиции."""
    assert "Коммуналка Коммуналка Питер" in prompt, name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_goal_starts_after_without_leading_posle(name, prompt):
    """starts_after: значение БЕЗ слова 'после' в начале (шаблон добавит сам)."""
    assert "БЕЗ слова 'после' в начале" in prompt, name


# ── fix: двойное "после" в целях ─────────────────────────────────────────────

def test_format_plan_goal_starts_after_no_double_posle():
    plan = {"goals": [{"name": "Отпуск", "total": 120000, "monthly": 0,
                       "starts_after": "после закрытия Ани"}]}
    out = _format_plan(plan)
    assert "после закрытия Ани" in out
    assert "после после" not in out


def test_format_plan_goal_starts_after_adds_posle_when_missing():
    plan = {"goals": [{"name": "Отпуск", "total": 120000, "monthly": 0,
                       "starts_after": "закрытия Ани"}]}
    out = _format_plan(plan)
    assert "после закрытия Ани" in out
