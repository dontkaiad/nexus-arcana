"""tests/test_migrate_arcana_legacy.py — миграция legacy 🃏 Раскладов
без живого Notion.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# импорт скрипта как модуля
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import migrate_arcana_legacy as m  # noqa: E402


# ── _detect_html_in_interp ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("<h3>Карта</h3><p>текст</p>", True),
    ("**жирный** текст", True),
    ("## Заголовок\nтекст", True),
    ("__bold__ underscore", True),
    ("просто плейн текст без разметки", False),
    ("", False),
    (None, False),
])
def test_detect_html_in_interp(text, expected):
    assert m._detect_html_in_interp(text) is expected


# ── _extract_bottom_from_legacy_interp ──────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Блок про карты\n🂠 Дно: Король Кубков", "Король Кубков"),
    ("Что-то\n🂠 Король Кубков · фон\nещё", "Король Кубков"),
    ("<p>текст</p>\n<h3>🂠 King of Cups · фон</h3>", "King of Cups"),
    ("Просто плейн без дна", None),
    ("", None),
    (None, None),
    # «🂠 [Дно колоды]» — структурный header, не имя карты
    ("Текст\n🂠 [Дно колоды]\nТуз", None),
])
def test_extract_bottom_from_legacy_interp(text, expected):
    got = m._extract_bottom_from_legacy_interp(text)
    if expected is None:
        assert got is None or got.lower() in {"дно колоды", "[дно колоды]"}
    else:
        assert got == expected


# ── _normalize_card_name ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("король кубков",  "Король Кубков"),
    ("КОРОЛЬ КУБКОВ",  "Король Кубков"),
    ("Король Кубков",  "Король Кубков"),
    ("корль кубков",   "Король Кубков"),  # fuzzy
])
def test_normalize_card_name_canonicalizes(raw, expected):
    from core.preprocess import _tarot_card_names_ru
    canonical = _tarot_card_names_ru()
    assert m._normalize_card_name(raw, canonical) == expected


def test_normalize_card_name_unknown_returns_as_is(capsys):
    from core.preprocess import _tarot_card_names_ru
    canonical = _tarot_card_names_ru()
    out = m._normalize_card_name("Несуществующая Карта", canonical)
    assert out == "Несуществующая Карта"
    captured = capsys.readouterr()
    assert "WARN" in captured.out
    assert "Несуществующая Карта" in captured.out


# ── _migrate_record ─────────────────────────────────────────────────────────

def _page(interp: str, bottom: str = "") -> dict:
    return {
        "id": "page-x",
        "properties": {
            "Трактовка": {"rich_text": [{"plain_text": interp}]},
            "Дно колоды": {"rich_text": (
                [{"plain_text": bottom}] if bottom else []
            )},
        },
    }


def test_migrate_record_sanitizes_html():
    p = _page("## Заголовок\n**жирный** текст")
    changes = m._migrate_record(p)
    assert "Трактовка" in changes
    new_text = changes["Трактовка"]["rich_text"][0]["text"]["content"]
    assert "<b>" in new_text
    assert "<h3>" in new_text


def test_migrate_record_extracts_bottom_when_empty():
    p = _page("какой-то текст\n🂠 Дно: Король Кубков", bottom="")
    changes = m._migrate_record(p)
    assert "Дно колоды" in changes
    assert (
        changes["Дно колоды"]["rich_text"][0]["text"]["content"]
        == "Король Кубков"
    )


def test_migrate_record_skips_bottom_when_already_set():
    p = _page("текст\n🂠 Дно: Король Кубков", bottom="King of Cups")
    changes = m._migrate_record(p)
    assert "Дно колоды" not in changes


def test_migrate_record_idempotent():
    """Повторный прогон по уже мигрированной записи → пустой changes."""
    p = _page("<p>текст</p>", bottom="King of Cups")
    changes = m._migrate_record(p)
    assert changes == {}


def test_migrate_record_no_op_for_plain_text():
    p = _page("просто плейн текст")
    assert m._migrate_record(p) == {}


# ── CLI driver: dry-run не пишет, --apply пишет ─────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_does_not_call_update_page():
    pages = [
        _page("**жирный** текст"),
        _page("плейн", bottom=""),
    ]
    update_mock = AsyncMock()
    with patch("core.notion_client.query_pages", AsyncMock(return_value=pages)), \
         patch("core.notion_client.update_page", update_mock), \
         patch("core.config.config") as cfg:
        cfg.arcana.db_sessions = "fake-db-id"
        summary = await m._scan_and_migrate(limit=10, apply=False)
    update_mock.assert_not_called()
    assert summary["scanned"] == 2
    assert summary["needs_migration"] == 1
    assert summary["applied"] == 0


@pytest.mark.asyncio
async def test_apply_writes_to_notion():
    pages = [_page("**жирный** текст")]
    update_mock = AsyncMock()
    # Сбиваем sleep до нуля, иначе тест ждёт реальные 0.3s
    with patch("core.notion_client.query_pages",
               AsyncMock(return_value=pages)), \
         patch("core.notion_client.update_page", update_mock), \
         patch("scripts.migrate_arcana_legacy._RATE_LIMIT_SEC", 0), \
         patch("migrate_arcana_legacy._RATE_LIMIT_SEC", 0), \
         patch("asyncio.sleep", AsyncMock()), \
         patch("core.config.config") as cfg:
        cfg.arcana.db_sessions = "fake-db-id"
        summary = await m._scan_and_migrate(limit=10, apply=True)
    update_mock.assert_awaited_once()
    assert summary["applied"] == 1
