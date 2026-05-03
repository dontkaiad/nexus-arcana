"""tests/test_client_object_photos_parser.py — формат «URL | заметка»."""
from __future__ import annotations

from core.client_object_photos import (
    SEP, append, delete, edit_note, parse, serialize,
)


def test_parse_old_format_no_notes():
    raw = "https://a/1.jpg\nhttps://a/2.jpg"
    items = parse(raw)
    assert items == [
        {"url": "https://a/1.jpg", "note": ""},
        {"url": "https://a/2.jpg", "note": ""},
    ]


def test_parse_new_format_with_notes():
    raw = (
        "https://a/1.jpg | Игорь, начальник\n"
        "https://a/2.jpg | мама\n"
        "https://a/3.jpg"
    )
    items = parse(raw)
    assert items == [
        {"url": "https://a/1.jpg", "note": "Игорь, начальник"},
        {"url": "https://a/2.jpg", "note": "мама"},
        {"url": "https://a/3.jpg", "note": ""},
    ]


def test_parse_legacy_comma_split():
    """Совсем старый legacy: URL'ы через запятую — поддерживаем."""
    raw = "https://a/1.jpg, https://a/2.jpg"
    items = parse(raw)
    assert [i["url"] for i in items] == ["https://a/1.jpg", "https://a/2.jpg"]


def test_parse_skips_garbage_lines():
    raw = "не URL\nhttps://a/1.jpg | ok\n\n"
    items = parse(raw)
    assert items == [{"url": "https://a/1.jpg", "note": "ok"}]


def test_serialize_round_trip():
    items = [
        {"url": "https://a/1.jpg", "note": "первая"},
        {"url": "https://a/2.jpg", "note": ""},
    ]
    raw = serialize(items)
    assert raw == f"https://a/1.jpg{SEP}первая\nhttps://a/2.jpg"
    assert parse(raw) == items


def test_append_with_note():
    raw = "https://a/1.jpg"
    new_raw, items = append(raw, "https://a/2.jpg", note="мама")
    assert items[-1] == {"url": "https://a/2.jpg", "note": "мама"}
    assert "https://a/2.jpg | мама" in new_raw


def test_edit_note_overwrites():
    raw = "https://a/1.jpg | old\nhttps://a/2.jpg"
    new_raw, items = edit_note(raw, 0, "new")
    assert items[0]["note"] == "new"
    assert "https://a/1.jpg | new" in new_raw


def test_edit_note_clear():
    raw = "https://a/1.jpg | note"
    new_raw, items = edit_note(raw, 0, "")
    assert items[0]["note"] == ""
    assert new_raw == "https://a/1.jpg"


def test_delete_removes_line():
    raw = "https://a/1.jpg | a\nhttps://a/2.jpg | b\nhttps://a/3.jpg | c"
    new_raw, items = delete(raw, 1)
    assert [i["note"] for i in items] == ["a", "c"]
    assert "https://a/2.jpg" not in new_raw


def test_index_out_of_range_raises():
    import pytest
    with pytest.raises(IndexError):
        edit_note("https://a/1.jpg", 5, "x")
    with pytest.raises(IndexError):
        delete("https://a/1.jpg", -1)
