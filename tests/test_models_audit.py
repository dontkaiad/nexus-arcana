"""tests/test_models_audit.py — гарантия что все «дешёвые» вызовы
ask_claude ЯВНО используют Haiku, а не молча полагаются на дефолт
core/claude_client.py (который сейчас Haiku, но не должен определять
стоимость неявно).

Проверка статическая (грепаем исходник) — никаких живых API.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# (path, ожидаемый model substring) — для каждого вызова из аудита.
HAIKU_REQUIRED = [
    "core/router.py",
    "core/deleter.py",
    "core/reply_update.py",
    "arcana/handlers/finance.py",
    "arcana/handlers/stats.py",
    "arcana/handlers/works.py",
    "arcana/handlers/clients.py",
    "arcana/handlers/grimoire.py",
    "arcana/handlers/rituals.py",
    "nexus/handlers/notes.py",
    "nexus/nexus_bot.py",
    "miniapp/backend/routes/today.py",  # ADHD tip — короткая фраза, Haiku
]


@pytest.mark.parametrize("rel_path", HAIKU_REQUIRED)
def test_file_uses_haiku_for_ask_claude(rel_path: str):
    """Каждый файл из списка должен ЯВНО указывать Haiku хотя бы в одном
    ask_claude. Дефолт в core/claude_client.py — Haiku (`model or
    config.model_haiku`), но мы на дефолт не полагаемся: явный
    model="claude-haiku-..." фиксирует осознанный выбор дешёвой модели и
    защищает от случайной смены дефолта или передачи Sonnet."""
    src = (REPO / rel_path).read_text(encoding="utf-8")
    assert "ask_claude" in src, f"{rel_path}: нет ask_claude — список устарел?"
    assert 'model="claude-haiku' in src, (
        f"{rel_path}: ask_claude найден, но model=\"claude-haiku-...\" не "
        "указан явно. Дефолт сейчас Haiku, но выбор дешёвой модели должен "
        "быть осознанным и не зависеть от дефолта claude_client."
    )


# Контрольные: файлы где Sonnet легитимен (CLAUDE.md разрешает).
# Если Haiku появится — пусть тест упадёт и Кай решит сама.
SONNET_LEGIT = [
    # core/budget.py — это только regex-парсер из Памяти, Claude НЕ зовёт.
    # Бюджетная аналитика на Sonnet живёт в nexus/handlers/finance.py.
    ("core/memory.py",        "СДВГ-советы / запомнить"),
    ("core/vision.py",        "Vision (фото чеков)"),
    ("arcana/handlers/sessions.py",
     "трактовки таро (глубина + эмпатия)"),
    ("miniapp/backend/routes/arcana_sessions.py",
     "саммари сессии — narrative reasoning"),
]


@pytest.mark.parametrize("rel_path,why", SONNET_LEGIT)
def test_sonnet_legit_files_still_call_ask_claude(rel_path: str, why: str):
    """Защита от случайной чистки: эти файлы должны продолжать звать
    Claude (через Sonnet — это допустимо по CLAUDE.md)."""
    src = (REPO / rel_path).read_text(encoding="utf-8")
    assert "ask_claude" in src or "anthropic" in src.lower(), (
        f"{rel_path}: больше не зовёт Claude. Это было OK для {why}; "
        "если намеренно — обнови SONNET_LEGIT."
    )
