"""core/inventory_ai.py — «что у меня дома подойдёт от/для X» через Haiku.

ILIKE по названию не находит «что-то от похмелья» → «Алка-Зельцер». Вместо
эмбеддингов (требуют описаний/заметок) отдаём Haiku ВЕСЬ инвентарь пользователя
(название + категория) — он сам знает, что для чего, и объясняет почему.
Заметки заполнять не нужно. Инвентарь одного человека небольшой, один дешёвый
вызов.

Graceful: любой сбой / невалидный JSON → [] (остаётся обычное «не найден»).
Вызывается только когда ILIKE ничего не нашёл.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List

from core.claude_client import ask_claude

logger = logging.getLogger("core.inventory_ai")

# Потолок позиций в промпте — защита от раздувания токенов.
MAX_ITEMS = 300

_SYSTEM = (
    "Ты помогаешь найти в домашнем инвентаре то, что подходит под запрос. "
    "Тебе дан запрос пользователя и список позиций (id, название, категория, "
    "иногда заметка). Опирайся на общие знания о том, для чего служит каждая "
    "вещь (лекарство, продукт, расходник), а не только на совпадение слов. "
    "Выбери ТОЛЬКО позиции, которые реально подходят; если таких нет — верни []. "
    "Не выдумывай позиции, которых нет в списке. "
    "Для каждой выбранной дай короткое пояснение «почему» (до 12 слов, по-русски). "
    "Если вещь безрецептурная, но есть оговорка (например, противопоказания), "
    "не добавляй её — ты не врач. "
    'Верни ТОЛЬКО JSON-массив вида [{"id": "...", "why": "..."}], без текста и markdown.'
)


def _line(it: dict) -> str:
    parts = [f"id={it['id']}", f'название="{it.get("name", "")}"']
    if it.get("category"):
        parts.append(f'категория="{it["category"]}"')
    if it.get("note"):
        parts.append(f'заметка="{it["note"]}"')
    return " ".join(parts)


async def suggest_from_inventory(query: str, items: List[Dict]) -> List[Dict]:
    """items — dict'ы из list_manager (_item_to_dict/_inv_to_dict). Возвращает
    подмножество items (порядок как у модели), каждому добавлен ключ "why"."""
    if not query or not query.strip() or not items:
        return []
    pool = items[:MAX_ITEMS]
    prompt = f"Запрос пользователя: {query!r}\n\nПозиции:\n" + "\n".join(_line(i) for i in pool)
    try:
        raw = await ask_claude(
            prompt=prompt,
            system=_SYSTEM,
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            temperature=0,
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        picks = json.loads(cleaned)
        if not isinstance(picks, list):
            raise ValueError("not a list")
    except Exception as e:
        logger.warning("suggest_from_inventory failed: %s", e)
        return []
    by_id = {str(i["id"]): i for i in pool}
    out: List[Dict] = []
    seen = set()
    for p in picks:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", ""))
        if pid in by_id and pid not in seen:
            seen.add(pid)
            out.append({**by_id[pid], "why": str(p.get("why", "")).strip()})
    logger.info("suggest_from_inventory: query=%r pool=%d picked=%s", query[:60], len(pool), sorted(seen))
    return out
