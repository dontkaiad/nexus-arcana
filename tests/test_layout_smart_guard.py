"""tests/test_layout_smart_guard.py — guard'ы layout converter.

Покрытие:
- brand whitelist (бренды техники / магазинов / сервисов)
- mixed-script token (латиница+кириллица в одном токене через дефис/слэш)
- ≥2 латинских слов с гласными = реальный English → не конвертим
- Pure layout-typed (без гласных, например «pflfxf») продолжает конвертиться
- Граничные случаи: пусто/None/уже кириллица/только цифры

Контекст: до guard'ов сообщение «бренд-кириллица: бренд цифра в магазин»
конвертилось целиком в кашу — Haiku-парсер списков затем не мог его понять.
"""
from __future__ import annotations

import logging


from core.layout import (
    _english_word_count,
    _has_brand,
    _has_mixed_script_token,
    maybe_convert,
)


# ── Brand whitelist ───────────────────────────────────────────────────────────


def test_brand_whitelist_apple_watch():
    """«купи Apple Watch» — apple/watch в whitelist → не трогаем."""
    text = "купи Apple Watch"
    assert maybe_convert(text) == text


def test_brand_whitelist_iphone_alone():
    text = "iPhone 17 Pro"
    assert maybe_convert(text) == text


def test_brand_whitelist_case_insensitive():
    text = "ОЗОН доставка"
    # OZON есть в whitelist; case-insensitive
    assert maybe_convert(text) == text


def test_brand_whitelist_complex_purchase_list():
    """Сложное сообщение со списком покупок: бренды, цены, магазин в смешанном
    токене и через слэш — должно вернуться ИДЕНТИЧНО."""
    text = (
        "добавь товары: iPhone 17 Pro 100к в Ozon, "
        "AirPods Pro 3 25к/premium"
    )
    assert maybe_convert(text) == text


def test_has_brand_helper():
    assert _has_brand("купи iPhone")
    assert _has_brand("заказ с Ozon")
    assert _has_brand("apple")
    assert not _has_brand("просто молоко")


# ── Mixed-script ──────────────────────────────────────────────────────────────


def test_mixed_script_apple_dash_styk():
    """«Apple-стек» — один токен с латиницей+кириллицей → не конвертим."""
    text = "добавь в Apple-стек что-нибудь"
    assert maybe_convert(text) == text


def test_mixed_script_helper():
    assert _has_mixed_script_token("Apple-стек")
    assert _has_mixed_script_token("iPiter/премиум")
    assert not _has_mixed_script_token("Apple watch")
    assert not _has_mixed_script_token("просто молоко")
    assert not _has_mixed_script_token("pflfxf")


# ── Real-English words count ──────────────────────────────────────────────────


def test_real_english_two_words_not_converted():
    """«Random words like premium ultra» — 2+ английских → не трогаем.

    NB: в whitelist уже есть premium и ultra, так что сработает brand guard.
    Этот тест на случай когда слов нет в whitelist но они выглядят как English.
    """
    # подбираем слова которых ТОЧНО нет в whitelist — например рандомные
    text = "купи Charlotte Tilbury"  # обе в whitelist, но проверим механику
    # Этот будет blocked brand'ом. Возьмём слова не из whitelist:
    text2 = "tested random books online"
    assert maybe_convert(text2) == text2  # 4 латинских слова с гласными


def test_real_english_one_short_word_continues_logic():
    """«купи pro» — 1 латинское слово с гласной + 'купи' (cyrillic)."""
    text = "купи pro"
    # «pro» → brand whitelist (premium-style), сработает guard 1
    assert maybe_convert(text) == text


def test_english_word_count_helper():
    assert _english_word_count("iPhone Pro") == 2     # обе с гласными ≥3
    assert _english_word_count("pflfxf") == 0          # нет гласных
    assert _english_word_count("vjkjrj") == 0
    assert _english_word_count("erm pf") == 1          # erm 3 чара с гласной
    assert _english_word_count("молоко") == 0


# ── Pure-layout typing (старое поведение сохранено) ───────────────────────────


def test_pure_layout_pflfxf_still_converts():
    """«pflfxf» (= «задача») — без гласных, без брендов, без миксов.
    Должен продолжать конвертиться."""
    out = maybe_convert("pflfxf")
    assert out == "задача"


def test_pure_layout_multiple_words_no_vowels():
    """«pf,erm vjkjrj» — должен конвертиться.

    Только «erm» (3 чара с гласной) считается реально-English. Остальные
    слова без гласных (vjkjrj, pf) — раскладка-мусор. en_count=1 < 2 → guard
    не срабатывает, ratio_before=0, конверсия проходит.
    """
    out = maybe_convert("pf,erm vjkjrj")
    # Не делаем строгую проверку результата — только что конверсия произошла
    # (текст изменился). Важно что guard не сработал.
    assert out != "pf,erm vjkjrj"
    # И результат содержит кириллицу
    assert any("Ѐ" <= c <= "ӿ" for c in out)


# ── Граничные случаи ──────────────────────────────────────────────────────────


def test_empty_string():
    assert maybe_convert("") == ""


def test_whitespace_only():
    assert maybe_convert("   ") == "   "


def test_none_no_crash():
    """None не должен ронять (используется в pipelines с fallback)."""
    assert maybe_convert(None) == ""


def test_already_cyrillic():
    text = "купи молоко в магазине"
    assert maybe_convert(text) == text


def test_digits_and_punct_only():
    text = "12345 ! ? ."
    # Нет ни латиницы ни кириллицы → ratio undefined → no convert
    assert maybe_convert(text) == text


# ── Logging ──────────────────────────────────────────────────────────────────


def test_logging_emits_skip_brand(caplog):
    with caplog.at_level(logging.DEBUG, logger="core.layout"):
        maybe_convert("купи iPhone")
    assert any("brand_whitelist" in rec.message for rec in caplog.records)


def test_logging_emits_convert_info(caplog):
    """Реальная конверсия → INFO с before/after ratio."""
    with caplog.at_level(logging.INFO, logger="core.layout"):
        maybe_convert("pflfxf")
    convert_logs = [r for r in caplog.records if "layout: convert" in r.message]
    assert convert_logs, "expected a convert log"


def test_logging_emits_skip_mixed(caplog):
    with caplog.at_level(logging.DEBUG, logger="core.layout"):
        maybe_convert("Apple-стек тестовый")
    assert any("mixed_script" in rec.message or "brand_whitelist" in rec.message
               for rec in caplog.records)
