"""tests/test_payment_inline.py — parse_amount + build_payment_props."""
import pytest

from core.payment import parse_amount, build_payment_props


@pytest.mark.parametrize("inp,expected", [
    ("500", 500),
    ("500р", 500),
    ("500₽", 500),
    ("500 руб", 500),
    ("1.5к", 1500),
    ("2,5к", 2500),
    ("3к", 3000),
    ("0", 0),
])
def test_parse_amount_valid(inp, expected):
    assert parse_amount(inp) == expected


@pytest.mark.parametrize("inp", ["", "xyz", "abc500", None])
def test_parse_amount_invalid(inp):
    assert parse_amount(inp) is None


def test_money_sessions_props():
    props = build_payment_props("sessions", "money", 500)
    assert props["Сумма"]["number"] == 500
    assert props["Оплачено"]["number"] == 500
    assert props["Источник"]["select"]["name"] == "💵 Наличные"


def test_gift_sessions_props():
    props = build_payment_props("sessions", "gift")
    assert props["Сумма"]["number"] == 0
    assert props["Оплачено"]["number"] == 0
    # gift НЕ ставит Источник — это «нет оплаты», источника не было.
    assert "Источник" not in props


def test_debt_sessions_props():
    props = build_payment_props("sessions", "debt", 500)
    assert props["Сумма"]["number"] == 500
    assert props["Оплачено"]["number"] == 0
    assert props["Источник"]["select"]["name"] == "💵 Наличные"


def test_barter_wait_sessions_props():
    props = build_payment_props("sessions", "barter_wait", barter_what="массаж × 2")
    assert props["Сумма"]["number"] == 0
    assert props["Оплачено"]["number"] == 0
    assert props["Источник"]["select"]["name"] == "🔄 Бартер"
    assert props["Бартер · что"]["rich_text"][0]["text"]["content"] == "массаж × 2"


def test_barter_to_money_rituals_props():
    """Поля ритуала отличаются по именам."""
    props = build_payment_props("rituals", "barter_to_money", amount=2000)
    assert props["Цена за ритуал"]["number"] == 2000
    assert props["Оплачено"]["number"] == 2000
    assert props["Источник оплаты"]["select"]["name"] == "💵 Наличные"


def test_money_rituals_props():
    props = build_payment_props("rituals", "money", 1000)
    assert props["Цена за ритуал"]["number"] == 1000
    assert props["Оплачено"]["number"] == 1000
    assert props["Источник оплаты"]["select"]["name"] == "💵 Наличные"


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        build_payment_props("sessions", "wat", 0)


def test_unknown_target_raises():
    with pytest.raises(ValueError):
        build_payment_props("things", "money", 100)
