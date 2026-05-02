"""tests/test_card_normalization.py — нормализация коротких форматов карт."""
from miniapp.backend.tarot import find_card, normalize_card_input


def test_num_rus_suit():
    assert normalize_card_input("9 пентаклей") == "девятка пентаклей"
    assert normalize_card_input("2 мечей") == "двойка мечей"
    assert normalize_card_input("10 пентаклей") == "десятка пентаклей"


def test_en_form():
    assert normalize_card_input("Nine of Pentacles") == "девятка пентаклей"
    assert normalize_card_input("ace of swords") == "туз мечей"


def test_already_normal_unchanged():
    # Лоуэркейс ожидаем — функция не должна изменять смысл уже нормальных имён.
    assert normalize_card_input("шут") == "шут"
    assert normalize_card_input("The Fool") == "the fool"
    assert normalize_card_input("королева кубков") == "королева кубков"


def test_short_suit_alias():
    # «Пент» как короткий алиас → пентаклей.
    assert normalize_card_input("9 пент") == "девятка пентаклей"
    assert normalize_card_input("3 куб") == "тройка кубков"


def test_resolve_card_with_short_form():
    c = find_card("rider-waite", "9 пентаклей")
    assert c is not None
    assert c["en"] == "Nine of Pentacles"
    assert c["ru"] == "Девятка Пентаклей"

    c2 = find_card("rider-waite", "2 мечей")
    assert c2 is not None
    assert c2["en"] == "Two of Swords"

    # дно из юзерского ввода — тоже резолвится
    c3 = find_card("rider-waite", "10 пентаклей")
    assert c3 is not None
    assert c3["en"] == "Ten of Pentacles"


def test_resolve_card_lowercase_ru():
    c = find_card("rider-waite", "шут")
    assert c is not None
    assert c["en"] == "The Fool"


def test_empty_input():
    assert normalize_card_input("") == ""
    assert normalize_card_input(None) == ""
