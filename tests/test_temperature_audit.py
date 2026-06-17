"""tests/test_temperature_audit.py — гарантия что каждый вызов ask_claude /
ask_claude_vision в продовом коде содержит явный kwarg temperature=.

Без температуры API использует дефолт (~1.0), что ломает детерминированные
JSON-парсеры. Статическая AST-проверка — никаких живых API.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

_SKIP_PATHS = {
    _ROOT / "core" / "claude_client.py",  # определения функций, не вызовы
}

_SKIP_DIRS = {"tests", "__pycache__", "node_modules", "venv", ".venv", ".git"}

_TARGET = {"ask_claude", "ask_claude_vision"}


def _missing_temperature(tree: ast.AST) -> list[tuple[int, str]]:
    """Возвращает (lineno, name) для каждого вызова без temperature=."""
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _TARGET:
            name = func.id
        elif isinstance(func, ast.Attribute) and func.attr in _TARGET:
            name = func.attr
        else:
            continue
        if "temperature" not in {kw.arg for kw in node.keywords}:
            result.append((node.lineno, name))
    return result


def _iter_py_files():
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        p = Path(dirpath)
        # пропускаем скрытые папки и служебные
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            if fname.endswith(".py"):
                yield p / fname


def test_all_ask_claude_calls_have_explicit_temperature():
    violations = []
    for path in sorted(_iter_py_files()):
        if path in _SKIP_PATHS:
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:
            continue
        for lineno, name in _missing_temperature(tree):
            violations.append(f"{path.relative_to(_ROOT)}:{lineno} — {name}() без temperature=")

    assert not violations, (
        f"{len(violations)} вызов(ов) без temperature=:\n" + "\n".join(violations)
    )
