"""tests/test_card_normalization.py — нормализация коротких форматов карт."""
import pytest

from miniapp.backend.tarot import find_card, normalize_card_input


@pytest.mark.parametrize("raw,expected", [
    # «N <масть>» → числительное прописью
    pytest.param("9 пентаклей", "девятка пентаклей", id="num-9-pentacles"),
    pytest.param("2 мечей", "двойка мечей", id="num-2-swords"),
    pytest.param("10 пентаклей", "десятка пентаклей", id="num-10-pentacles"),
    # английская форма → русская
    pytest.param("Nine of Pentacles", "девятка пентаклей", id="en-nine-of-pentacles"),
    pytest.param("ace of swords", "туз мечей", id="en-ace-of-swords"),
    # уже нормальные имена — лоуэркейс ожидаем, смысл не меняется
    pytest.param("шут", "шут", id="normal-shut-unchanged"),
    pytest.param("The Fool", "the fool", id="normal-the-fool-lowercased"),
    pytest.param("королева кубков", "королева кубков", id="normal-queen-of-cups-unchanged"),
    # короткие алиасы мастей: «пент» → пентаклей, «куб» → кубков
    pytest.param("9 пент", "девятка пентаклей", id="short-suit-pent"),
    pytest.param("3 куб", "тройка кубков", id="short-suit-kub"),
    # пустой ввод
    pytest.param("", "", id="empty-string"),
    pytest.param(None, "", id="none-input"),
])
def test_normalize_card_input(raw, expected):
    """normalize_card_input: цифры/EN/алиасы → каноничное RU-имя (лоуэркейс)."""
    assert normalize_card_input(raw) == expected


@pytest.mark.parametrize("query,en,ru", [
    # короткие формы (включая дно из юзерского ввода) — резолвятся в карту
    pytest.param("9 пентаклей", "Nine of Pentacles", "Девятка Пентаклей", id="resolve-9-pentacles"),
    pytest.param("2 мечей", "Two of Swords", None, id="resolve-2-swords"),
    pytest.param("10 пентаклей", "Ten of Pentacles", None, id="resolve-10-pentacles"),
    # лоуэркейс-RU тоже резолвится
    pytest.param("шут", "The Fool", None, id="resolve-lowercase-ru-shut"),
])
def test_find_card_resolves_short_forms(query, en, ru):
    """find_card: короткие/лоуэркейс формы находят карту в колоде."""
    c = find_card("rider-waite", query)
    assert c is not None
    assert c["en"] == en
    if ru is not None:
        assert c["ru"] == ru
