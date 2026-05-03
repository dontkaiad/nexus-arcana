"""core/client_object_photos.py — парсер/сериализатор поля «Фото объектов».

Notion-схема: rich_text. Формат каждой строки:
    URL                         (старый формат — заметка пустая)
    URL | произвольная заметка   (новый формат)

Разделитель ровно « | » (пробел, пайп, пробел) — у Cloudinary URL внутри пайпа
не бывает, но если вдруг — split с maxsplit=1 берёт только первый.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

SEP = " | "


def _split_legacy_commas(line: str) -> List[str]:
    """Если в одной строке несколько http-URL'ов через запятую — это legacy
    формат до нот. Дробим. Иначе оставляем строку целиком (запятые могут быть
    внутри note)."""
    if line.count("http") <= 1:
        return [line]
    return [s.strip() for s in line.split(",") if s.strip()]


def parse(raw: str) -> List[dict]:
    """Превращает rich_text в [{url, note}, ...]. Нечитаемые строки игнорирует."""
    if not raw:
        return []
    items: List[dict] = []
    for raw_line in str(raw).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for sub in _split_legacy_commas(line):
            sub = sub.strip()
            if not sub:
                continue
            url, _, note = sub.partition(SEP)
            url = url.strip()
            if not url.startswith("http"):
                continue
            items.append({"url": url, "note": note.strip()})
    return items


def serialize(items: Iterable[dict]) -> str:
    """[{url, note}, ...] → rich_text-строка."""
    lines: List[str] = []
    for it in items:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        note = (it.get("note") or "").strip()
        lines.append(f"{url}{SEP}{note}" if note else url)
    return "\n".join(lines)


def append(raw: str, url: str, note: str = "") -> Tuple[str, List[dict]]:
    """Добавить новый объект в конец. Возвращает (новый_raw, items_after)."""
    items = parse(raw)
    items.append({"url": url.strip(), "note": (note or "").strip()})
    return serialize(items), items


def edit_note(raw: str, index: int, note: str) -> Tuple[str, List[dict]]:
    """Перезаписать заметку у фото по индексу. KeyError при out-of-range."""
    items = parse(raw)
    if index < 0 or index >= len(items):
        raise IndexError(f"object photo index {index} out of range")
    items[index]["note"] = (note or "").strip()
    return serialize(items), items


def delete(raw: str, index: int) -> Tuple[str, List[dict]]:
    """Удалить фото по индексу. KeyError при out-of-range."""
    items = parse(raw)
    if index < 0 or index >= len(items):
        raise IndexError(f"object photo index {index} out of range")
    del items[index]
    return serialize(items), items
