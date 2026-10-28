"""zarya/build-inputs.txt — входы сборки Зари (читает deploy.sh для GIT_SHA).

Если Заря импортирует модуль вне перечисленных путей — её GIT_SHA не поменяется
при правке этого модуля, а образ (COPY) не содержит файла. Тест это ловит.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INPUTS = REPO / "zarya" / "build-inputs.txt"
TOP = ("core", "zarya", "nexus", "arcana")


def _inputs() -> list[str]:
    return [l.strip().rstrip("/") for l in INPUTS.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def _covered(rel: str, inputs: list[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in inputs)


def _module_file(mod: str) -> Path | None:
    base = REPO.joinpath(*mod.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Все импортируемые модули репо (включая ленивые внутри функций) + родительские пакеты."""
    pkg = path.relative_to(REPO).with_suffix("").parts[:-1]
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg)
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            names = [mod] + [f"{mod}.{a.name}" for a in node.names]
        for n in names:
            if n.split(".")[0] in TOP:
                out.add(n)
    return out


def _closure() -> set[Path]:
    seen: set[Path] = set()
    todo = [p for p in (REPO / "zarya").rglob("*.py")]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for mod in _imports(f):
            parts = mod.split(".")
            for i in range(1, len(parts) + 1):          # и пакеты-родители (__init__)
                mf = _module_file(".".join(parts[:i]))
                if mf and mf not in seen:
                    todo.append(mf)
    return seen


def test_inputs_file_paths_exist():
    for p in _inputs():
        assert (REPO / p).exists(), f"{p} из build-inputs.txt не существует"


def test_every_zarya_import_is_covered():
    inputs = _inputs()
    files = _closure()
    assert any(f.name == "bot.py" for f in files)       # обход вообще что-то нашёл
    missing = sorted(str(f.relative_to(REPO)) for f in files
                     if not _covered(str(f.relative_to(REPO)), inputs))
    assert not missing, f"Заря импортирует файлы вне zarya/build-inputs.txt: {missing}"


def test_dockerfile_copy_sources_are_in_inputs():
    """COPY в zarya/Dockerfile и build-inputs.txt не расходятся."""
    inputs = set(_inputs())
    for line in (REPO / "zarya" / "Dockerfile").read_text().splitlines():
        m = re.match(r"\s*COPY\s+(.+)", line)
        if not m:
            continue
        *srcs, _dst = m.group(1).split()
        for s in srcs:
            s = s.rstrip("/")
            assert s in inputs, f"COPY {s} нет в zarya/build-inputs.txt"
