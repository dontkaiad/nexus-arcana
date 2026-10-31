"""tests/test_owner_fact_prefix.py — owner_fact срезает старый префикс «Имя: » любого имени/алиаса карточки.

Склеенная карточка приносит в алиасы имя присоединённого человека, а его факты хранят префикс «Имя: ».
В ответе бота должно быть одно основное имя, без «Ольга: Черепашка: …». Вымышленные имена.
"""
from core.people import owner_fact


def test_alias_prefix_from_merged_card_is_replaced_by_primary_name():
    assert owner_fact("Черепашка: любит чай", "Ольга | Оля | Черепашка", 5) == "Ольга: любит чай"


def test_primary_name_and_other_alias_prefixes():
    assert owner_fact("Оля: любит чай", "Ольга | Оля", 5) == "Ольга: любит чай"
    assert owner_fact("Ольга: любит чай", "Ольга | Оля", 5) == "Ольга: любит чай"


def test_multiword_alias_words_count():
    assert owner_fact("Островская: любит чай", "Ольга | Ольга Островская", 5) == "Ольга: любит чай"


def test_unrelated_prefix_and_meaningful_colons_are_kept():
    assert owner_fact("Важно: аллергия на орехи", "Ольга | Оля", 5) == "Ольга: Важно: аллергия на орехи"
    assert owner_fact("Инна: любит чай", "Ольга | Оля", 5).endswith("Инна: любит чай")


def test_no_prefix_gets_primary_name_and_non_person_untouched():
    assert owner_fact("любит чай", "Ольга | Оля", 5) == "Ольга: любит чай"
    assert owner_fact("Черепашка: любит чай", "Ольга | Оля | Черепашка", None) == "Черепашка: любит чай"
    assert owner_fact("любит чай", "", 5) == "любит чай"
