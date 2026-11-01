"""tests/test_models_audit.py — страж стоимости и моделей: ВСЕ LLM-вызовы идут через РОЛИ (core/models.py).

Статический анализ (ast), без живых API. Что гарантируется:
  1. у каждого вызова ask_claude / ask_claude_stream есть `role="<известная роль>"` (ask_claude_vision по умолчанию vision);
  2. нигде вне core/models.py, core/config.py и core/claude_client.py нет литералов `claude-haiku…` / `claude-sonnet…`
     и обращений к config.model_haiku / model_sonnet — иначе откат переменной окружения перестал бы работать;
  3. Sonnet-роли (дорогие) используются только в разрешённых местах (CLAUDE.md: «Sonnet — только где нужна глубина»);
  4. умолчания ролей = ожидаемые модели своей группы; env-переопределения работают (откат без правки кода).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import models

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("tests", "venv", "node_modules", "alembic", "scripts", "tools", ".git", "miniapp/frontend")
CALLS = {"ask_claude", "ask_claude_stream", "ask_claude_vision"}
LITERAL_OK = {"core/models.py", "core/config.py"}


def _py_files():
    for p in REPO.rglob("*.py"):
        rel = p.relative_to(REPO).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        yield rel, p


def _calls():
    out = []
    for rel, p in _py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                if name in CALLS:
                    out.append((rel, n.lineno, name, {k.arg: k.value for k in n.keywords if k.arg}))
    return out


CALL_SITES = _calls()


def test_audit_actually_finds_the_calls():
    assert len(CALL_SITES) >= 60, "ast-обход перестал находить вызовы — страж ничего не проверяет"


@pytest.mark.parametrize("rel,line,name,kw", [c for c in CALL_SITES if c[2] != "ask_claude_vision"],
                         ids=lambda v: str(v) if isinstance(v, (str, int)) else "")
def test_every_call_has_a_known_role(rel, line, name, kw):
    if rel == "core/claude_client.py" and "role" not in kw:
        pytest.skip("внутренние обёртки claude_client сами задают роль")
    role = kw.get("role")
    assert isinstance(role, ast.Constant) and role.value in models.ROLES, (
        f"{rel}:{line} {name}: нужен role=\"<роль из core.models.ROLES>\" (модель выбирает роль, не вызов)")


def test_no_model_literals_or_config_models_outside_models_module():
    bad = []
    for rel, p in _py_files():
        if rel in LITERAL_OK or rel == "core/claude_client.py":
            continue
        src = p.read_text(encoding="utf-8")
        for needle in ("claude-haiku", "claude-sonnet", "model_haiku", "model_sonnet"):
            if needle in src:
                bad.append(f"{rel}: «{needle}»")
    assert not bad, "модель задаётся только ролью (core/models.py), иначе откат env перестанет работать:\n" + "\n".join(bad)


# Sonnet (дорогая) — только эти роли и только в этих файлах.
SONNET_ROLE_FILES = {
    "narrative": {"arcana/handlers/sessions.py", "arcana/handlers/base.py"},
    "analysis": {"nexus/handlers/finance.py"},
    "wellbeing": {"core/memory.py", "nexus/handlers/memory.py", "miniapp/backend/routes/memory.py",
                  "miniapp/backend/routes/weather.py"},
    "summary": {"miniapp/backend/routes/arcana_sessions.py"},
    "vision": None,      # vision-вызовы идут через ask_claude_vision — отдельная проверка ниже
}


def test_sonnet_roles_used_only_where_allowed():
    for rel, line, name, kw in CALL_SITES:
        role = kw.get("role")
        if not isinstance(role, ast.Constant) or role.value not in models.ROLES:
            continue
        if models.ROLES[role.value].tier == "sonnet":
            allowed = SONNET_ROLE_FILES.get(role.value)
            assert allowed is None or rel in allowed, (
                f"{rel}:{line}: роль {role.value} (Sonnet) здесь не разрешена. CLAUDE.md: Sonnet — только там, где нужна "
                "глубина; если это осознанно — обнови SONNET_ROLE_FILES и реши с Кай.")


def test_sonnet_files_still_call_claude():
    for files in SONNET_ROLE_FILES.values():
        for rel in files or ():
            src = (REPO / rel).read_text(encoding="utf-8")
            assert "ask_claude" in src, f"{rel}: больше не зовёт Claude — обнови SONNET_ROLE_FILES"


# ── умолчания и откат переменными окружения ──────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith(("MODEL_", "THINKING_", "CACHE_TTL_")):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_default_models_per_tier(clean_env):
    for name, r in models.ROLES.items():
        m = models.model_for(name)
        if name == "memory_answer":
            assert m == "claude-haiku-5-5"                 # уже на 5.5 (этап 4)
        elif r.tier == "haiku":
            assert m == "claude-haiku-4-5-20251001", name
        else:
            assert m == "claude-sonnet-4-6", name


def test_env_overrides_role_then_group_then_default(clean_env):
    clean_env.setenv("MODEL_HAIKU", "claude-haiku-5-5")
    assert models.model_for("parse") == "claude-haiku-5-5" and models.model_for("classify") == "claude-haiku-5-5"
    assert models.model_for("narrative") == "claude-sonnet-4-6"                  # другая группа не затронута
    clean_env.setenv("MODEL_CLASSIFY", "claude-haiku-4-5-20251001")
    assert models.model_for("classify") == "claude-haiku-4-5-20251001" and models.model_for("route") == "claude-haiku-5-5"
    clean_env.setenv("MODEL_SONNET", "claude-sonnet-5-5")
    assert models.model_for("analysis") == "claude-sonnet-5-5"
    clean_env.setenv("MODEL_MEMORY_ANSWER", "claude-haiku-4-5-20251001")          # откат generated-answer на 4.5
    assert models.model_for("memory_answer") == "claude-haiku-4-5-20251001"


def test_thinking_and_cache_overrides(clean_env):
    assert models.thinking_for("narrative") is None and models.thinking_for("parse") == "off"
    clean_env.setenv("THINKING_NARRATIVE", "low")
    assert models.thinking_for("narrative") == "low"
    clean_env.setenv("THINKING_PARSE", "default")
    assert models.thinking_for("parse") is None
    assert models.cache_ttl_for("classify") == "5m"
    clean_env.setenv("CACHE_TTL_CLASSIFY", "off")
    assert models.cache_ttl_for("classify") is None


def test_unknown_role_is_an_error_not_a_silent_default():
    with pytest.raises(KeyError):
        models.model_for("pars")                                                  # опечатка не должна уйти на дефолт


def test_ask_claude_resolves_model_and_thinking_from_role(clean_env):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from core import claude_client as cc

    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="x")], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    clean_env.setenv("MODEL_PARSE", "claude-haiku-5-5")
    create = AsyncMock(return_value=resp)
    with patch.object(cc, "_create_message", create):
        asyncio.run(cc.ask_claude("p", role="parse", temperature=0))
    kw = create.await_args.kwargs
    assert kw["model"] == "claude-haiku-5-5" and "temperature" not in kw and kw["extra_body"] == {"thinking": {"type": "disabled"}}
    clean_env.delenv("MODEL_PARSE")
    with patch.object(cc, "_create_message", create):
        asyncio.run(cc.ask_claude("p", role="parse", temperature=0))
    kw = create.await_args.kwargs
    assert kw["model"] == "claude-haiku-4-5-20251001" and kw["temperature"] == 0 and "extra_body" not in kw   # 4.x как раньше
