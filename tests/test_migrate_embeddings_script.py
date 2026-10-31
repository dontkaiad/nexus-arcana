"""scripts/migrate_memory_embeddings.py: вывод dry-run не раскрывает содержимое записей.

В 🧠 Памяти лежат личные данные (суммы, здоровье), а вывод скрипта попадает в терминал/логи
сессий — поэтому сэмпл = только id, категория и длина.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SECRET = "СЕКРЕТНЫЙ-ТЕКСТ-15000-руб"
PATH = Path(__file__).resolve().parent.parent / "scripts" / "migrate_memory_embeddings.py"


def _load():
    spec = importlib.util.spec_from_file_location("_mig_emb_script", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_unindexed_carries_only_metadata_for_display():
    mod = _load()
    rows = [SimpleNamespace(id=7, fact_text=SECRET, related_to="связь", category="💰 Лимит")]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = rows
    with patch.object(mod, "get_engine", return_value=engine):
        items = mod._fetch_unindexed(None)
    assert items[0]["memory_id"] == "7" and items[0]["category"] == "💰 Лимит" and items[0]["length"] == len(SECRET)
    assert SECRET in items[0]["embed_text"]                      # текст для эмбеддинга остаётся полным


def test_dry_run_summary_prints_id_category_length_but_never_content(capsys):
    mod = _load()
    items = [{"memory_id": str(i), "embed_text": "обязательно: %s %d" % (SECRET, i), "category": "💰 Лимит",
              "length": len(SECRET)} for i in range(5)]
    mod._print_summary({"scanned": 5, "applied": 0, "sample": items[:3]}, apply=False, batch_size=20)
    out = capsys.readouterr().out
    assert SECRET not in out and "обязательно" not in out and "15000" not in out
    assert out.count("category=💰 Лимит") == 3 and "len=%d" % len(SECRET) in out and "id=0" in out


def test_apply_mode_summary_is_equally_content_free(capsys):
    mod = _load()
    item = {"memory_id": "1", "embed_text": SECRET, "category": "", "length": 3}
    mod._print_summary({"scanned": 1, "applied": 1, "sample": [item]}, apply=True, batch_size=20)
    out = capsys.readouterr().out
    assert SECRET not in out and "category=—" in out


def test_format_sample():
    mod = _load()
    assert mod.format_sample({"memory_id": "9", "category": "🏠 Быт", "length": 12, "embed_text": "x"}) == "id=9 category=🏠 Быт len=12"
