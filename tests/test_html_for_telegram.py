"""tests/test_html_for_telegram.py — Telegram-safe HTML конвертация."""
from core.html_for_telegram import html_to_telegram


def test_h3_to_bold():
    assert html_to_telegram("<h3>Title</h3>") == "<b>Title</b>"


def test_p_to_double_newline():
    assert html_to_telegram("<p>A</p><p>B</p>") == "A\n\nB"


def test_br_to_newline():
    assert html_to_telegram("Раз<br>Два") == "Раз\nДва"


def test_collapses_excess_newlines():
    # Несколько <p> подряд без сжатия дали бы 4 переноса;
    # html_to_telegram сводит к двум.
    out = html_to_telegram("<p>A</p>\n\n<p>B</p>")
    assert "\n\n\n" not in out
    assert out == "A\n\nB"


def test_full_interpretation():
    inp = (
        "<h3>Общий смысл</h3>"
        "<p>Текст с <b>жирным</b>.</p>"
        "<h3>🃏 Шут</h3>"
        "<p>Описание с <i>курсивом</i>.</p>"
    )
    out = html_to_telegram(inp)
    assert "<h3>" not in out
    assert "<p>" not in out
    assert "<b>Общий смысл</b>" in out
    assert "<b>жирным</b>" in out
    assert "<i>курсивом</i>" in out
    assert "Текст" in out


def test_empty_input():
    assert html_to_telegram("") == ""
    assert html_to_telegram(None) == ""


def test_inline_tags_preserved():
    # Наши allowed-теги (b/i) не трогаем, только block-теги.
    s = "<p><b>X</b> и <i>Y</i></p>"
    assert html_to_telegram(s) == "<b>X</b> и <i>Y</i>"
