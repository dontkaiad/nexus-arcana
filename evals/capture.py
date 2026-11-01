"""evals/capture.py — сбор входящих сообщений для эвала классификатора: с лимитом и сроком хранения.

Включается командой (не переменной окружения без границ):
    python -m evals.capture enable [--limit 200] [--ttl-days 14]    status | disable | purge

Что делает: каждое сообщение, попавшее в classify(), дописывается (только текст, без user_id, до 600 символов) в
data/evals/captured_messages.jsonl. САМО выключается после `limit` сообщений; файл и состояние удаляются через
`ttl_days` дней от включения (проверка при каждом сообщении и по `purge`). Файл лежит в постоянном томе /app/data на
сервере и в git не попадает (/data/evals/ в .gitignore). EVAL_CAPTURE=1 без команды тоже включает сбор — с теми же
лимитами по умолчанию (чтобы «забытая» переменная не копила сообщения бесконечно).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from evals.common import PRIVATE

STATE = PRIVATE / "capture.json"
FILE = PRIVATE / "captured_messages.jsonl"
DEFAULT_LIMIT = 200
DEFAULT_TTL_DAYS = 14
MAX_CHARS = 600


def _now() -> float:
    return time.time()


def _read() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(st: Dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def enable(limit: int = DEFAULT_LIMIT, ttl_days: int = DEFAULT_TTL_DAYS) -> Dict[str, Any]:
    st = {"enabled": True, "started": _now(), "limit": int(limit), "ttl_days": int(ttl_days), "count": 0}
    FILE.unlink(missing_ok=True)                     # новый сбор — с чистого файла
    _write(st)
    return status()


def disable(delete_file: bool = False) -> Dict[str, Any]:
    st = _read()
    if st:
        st["enabled"] = False
        st["finished"] = _now()
        _write(st)
    if delete_file:
        purge(force=True)
    return status()


def purge(force: bool = False) -> bool:
    """Удалить файл и состояние, если срок истёк (или force). True — удалено."""
    st = _read()
    if st is None and not force:
        return False
    if force or (_now() - float(st.get("started", 0))) > float(st.get("ttl_days", DEFAULT_TTL_DAYS)) * 86400:
        FILE.unlink(missing_ok=True)
        STATE.unlink(missing_ok=True)
        return True
    return False


def status() -> Dict[str, Any]:
    st = _read()
    if st is None:
        return {"enabled": False, "state": "off", "file_lines": 0}
    lines = sum(1 for _ in open(FILE, encoding="utf-8")) if FILE.exists() else 0
    left_days = max(0.0, float(st.get("ttl_days", DEFAULT_TTL_DAYS)) - (_now() - float(st.get("started", 0))) / 86400)
    return {"enabled": bool(st.get("enabled")), "state": "collecting" if st.get("enabled") else "done",
            "count": st.get("count", 0), "limit": st.get("limit"), "file_lines": lines,
            "file_expires_in_days": round(left_days, 1)}


def maybe_capture(text: str) -> None:
    """Вызывается из classify(). Дёшево, когда сбор выключен (одна проверка существования файла состояния)."""
    try:
        st = _read() if STATE.exists() else None
        if st is None:
            if os.environ.get("EVAL_CAPTURE", "0").strip().lower() not in ("1", "true", "on", "yes"):
                return
            st = {"enabled": True, "started": _now(), "limit": DEFAULT_LIMIT, "ttl_days": DEFAULT_TTL_DAYS, "count": 0}
            _write(st)
        if purge():                                   # срок хранения истёк → всё удалено
            return
        if not st.get("enabled"):
            return
        if st.get("count", 0) >= st.get("limit", DEFAULT_LIMIT):
            st["enabled"], st["finished"] = False, _now()
            _write(st)
            return
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(_now()), "text": (text or "")[:MAX_CHARS]}, ensure_ascii=False) + "\n")
        st["count"] = st.get("count", 0) + 1
        if st["count"] >= st.get("limit", DEFAULT_LIMIT):          # набрали → само выключилось
            st["enabled"], st["finished"] = False, _now()
        _write(st)
    except Exception:
        pass                                          # сбор никогда не мешает обработке сообщения


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.capture")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enable"); e.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    e.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS)
    sub.add_parser("status"); d = sub.add_parser("disable"); d.add_argument("--delete-file", action="store_true")
    sub.add_parser("purge")
    a = ap.parse_args()
    if a.cmd == "enable":
        out = enable(a.limit, a.ttl_days)
    elif a.cmd == "disable":
        out = disable(a.delete_file)
    elif a.cmd == "purge":
        out = {"purged": purge(force=True)}
    else:
        out = status()
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
