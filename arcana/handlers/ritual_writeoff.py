"""arcana/handlers/ritual_writeoff.py — списание расходников после ритуала.

После сохранения ритуала с непустым полем «Расходники» бот:
 1. Парсит строку через Haiku → [{name, qty, unit}].
 2. Ищет в инвентаре (Тип=📦 Инвентарь, Бот=🌒 Arcana, name LIKE).
 3. Кидает inline-сообщение с превью списания + кнопками
    [✅ Списать] [✏️ Поправить] [❌ Не списывать].
 4. На ✅ — обновляет Количество. Если новое <= 0 → Archived.
 5. На ✏️ — pending_state, ждём правок текстом «соль 100г», пересчитывает.
 6. На ❌ — ничего не делает.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Any, List, Optional

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.claude_client import ask_claude
from core.config import config
from core.list_manager import inventory_search, inventory_update

logger = logging.getLogger("arcana.ritual_writeoff")

router = Router()

BOT_NAME = "🌒 Arcana"

# ── Pending state (sqlite) ───────────────────────────────────────────────────

_DB = os.path.join(os.path.dirname(__file__), "..", "..", "pending_writeoff.db")
_TTL = 1800  # 30 min


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending_writeoff "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.commit()
    return con


def _save(uid: int, data: dict) -> None:
    with _con() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending_writeoff (uid, data, ts) VALUES (?,?,?)",
            (uid, json.dumps(data, ensure_ascii=False), time.time()),
        )


def _load(uid: int) -> Optional[dict]:
    with _con() as c:
        row = c.execute("SELECT data, ts FROM pending_writeoff WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    if time.time() - row[1] > _TTL:
        _drop(uid)
        return None
    return json.loads(row[0])


def _drop(uid: int) -> None:
    with _con() as c:
        c.execute("DELETE FROM pending_writeoff WHERE uid=?", (uid,))


# ── Parser ───────────────────────────────────────────────────────────────────

PARSE_SUPPLIES_SYSTEM = (
    "Извлеки список расходников из текста. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"items": [{"name": "соль", "qty": 50, "unit": "г"}]}\n'
    "qty — число, unit — «г», «мл», «шт», «капл» или null."
)


def _heuristic_parse(text: str) -> List[dict]:
    """Резервный парсер если Haiku не отвечает."""
    items: List[dict] = []
    for chunk in re.split(r"[,;\n]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s*(г|мл|шт|капл|кап)?$", chunk, re.IGNORECASE)
        if m:
            items.append({"name": m.group(1).strip(), "qty": float(m.group(2)),
                           "unit": (m.group(3) or "").lower()})
        else:
            items.append({"name": chunk, "qty": None, "unit": ""})
    return items


async def parse_supplies(text: str) -> List[dict]:
    if not text or not text.strip():
        return []
    try:
        raw = await ask_claude(
            f"Текст: {text}",
            system=PARSE_SUPPLIES_SYSTEM,
            max_tokens=300,
            model="claude-haiku-4-5-20251001",
        )
        # Снимаем ```json ... ``` если есть
        s = (raw or "").strip()
        s = re.sub(r"```(?:json)?\s*|\s*```", "", s, flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            items = data.get("items") or []
            if items:
                return items
    except Exception as e:
        logger.warning("parse_supplies haiku failed: %s", e)
    return _heuristic_parse(text)


# ── Match against inventory ──────────────────────────────────────────────────

async def _match_inventory(items: List[dict], user_notion_id: str) -> List[dict]:
    """Для каждого айтема — ищем в инвентаре, считаем превью списания."""
    out: List[dict] = []
    for it in items:
        name = (it.get("name") or "").strip()
        qty = it.get("qty")
        unit = it.get("unit") or ""
        if not name:
            continue
        matches = await inventory_search(name, BOT_NAME, user_notion_id)
        if matches:
            inv = matches[0]
            current = float(inv.get("quantity") or 0)
            new_qty = current - float(qty or 0)
            out.append({
                "name": inv.get("name") or name,
                "needed": qty,
                "unit": unit,
                "current": current,
                "after": new_qty,
                "found": True,
                "inventory_name": inv.get("name") or name,
            })
        else:
            out.append({
                "name": name, "needed": qty, "unit": unit,
                "current": None, "after": None, "found": False,
            })
    return out


def _format_preview(rows: List[dict]) -> str:
    lines = ["🕯️ Списать из инвентаря?"]
    for r in rows:
        unit = r.get("unit") or ""
        if r["found"]:
            lines.append(
                f"• {r['name']} (есть {_fmt_num(r['current'])}{unit}) "
                f"→ −{_fmt_num(r['needed'])}{unit} = {_fmt_num(r['after'])}{unit}"
            )
        else:
            lines.append(f"• {r['name']} (НЕТ В ИНВЕНТАРЕ) — добавить?")
    return "\n".join(lines)


def _fmt_num(x) -> str:
    if x is None:
        return "?"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Списать", callback_data=f"wo_apply:{uid}"),
        InlineKeyboardButton(text="✏️ Поправить", callback_data=f"wo_edit:{uid}"),
        InlineKeyboardButton(text="❌ Не списывать", callback_data=f"wo_cancel:{uid}"),
    ]])


# ── Public API: вызывается из handle_add_ritual ──────────────────────────────

async def propose_writeoff(
    message: Message,
    supplies_text: str,
    user_notion_id: str = "",
) -> None:
    """Распарсить расходники + показать сообщение с превью + сохранить pending."""
    if not supplies_text or not supplies_text.strip():
        return
    items = await parse_supplies(supplies_text)
    if not items:
        return
    rows = await _match_inventory(items, user_notion_id)
    if not rows:
        return
    uid = message.from_user.id
    _save(uid, {"rows": rows, "user_notion_id": user_notion_id})
    await message.answer(_format_preview(rows), reply_markup=_kb(uid))


# ── Callbacks ────────────────────────────────────────────────────────────────

async def _apply(rows: List[dict], user_notion_id: str) -> List[str]:
    """Списать из инвентаря, вернуть human-сообщения о результатах."""
    notes: List[str] = []
    for r in rows:
        if not r["found"]:
            notes.append(f"{r['name']}: добавь в инвентарь")
            continue
        new_qty = float(r.get("after") or 0)
        res = await inventory_update(
            r["inventory_name"], int(new_qty) if new_qty.is_integer() else new_qty,
            BOT_NAME, user_notion_id,
        )
        if res.get("error"):
            notes.append(f"{r['name']}: ошибка ({res['error']})")
        elif res.get("archived"):
            notes.append(f"{r['name']}: закончился")
        else:
            notes.append(f"{r['name']}: −{_fmt_num(r['needed'])}{r.get('unit') or ''} (осталось {_fmt_num(new_qty)})")
    return notes


@router.callback_query(F.data.startswith("wo_apply:"))
async def cb_apply(cb: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return
    pending = _load(uid)
    if not pending:
        await cb.answer("Запрос устарел.")
        return
    notes = await _apply(pending["rows"], pending.get("user_notion_id") or user_notion_id)
    _drop(uid)
    await cb.answer("Списано.")
    text = "🕯️ Списано:\n" + "\n".join(f"• {n}" for n in notes)
    try:
        await cb.message.edit_text(text)
    except Exception:
        await cb.message.answer(text)


@router.callback_query(F.data.startswith("wo_cancel:"))
async def cb_cancel(cb: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return
    _drop(uid)
    await cb.answer("Не списываем.")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("wo_edit:"))
async def cb_edit(cb: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return
    pending = _load(uid)
    if not pending:
        await cb.answer("Запрос устарел.")
        return
    pending["awaiting_edit"] = True
    _save(uid, pending)
    await cb.answer()
    await cb.message.answer(
        "✏️ Пришли исправленный список одной строкой, например:\n"
        "<code>соль 100г, свеча 1шт</code>",
        parse_mode="HTML",
    )


# ── Text intercept (called from base.py) ─────────────────────────────────────

async def handle_pending_edit(message: Message, text: str, user_notion_id: str = "") -> bool:
    """Если у юзера есть pending writeoff в режиме awaiting_edit — обновить
    список и показать новое превью. Возвращает True если перехватили."""
    uid = message.from_user.id
    pending = _load(uid)
    if not pending or not pending.get("awaiting_edit"):
        return False
    items = await parse_supplies(text)
    if not items:
        await message.answer("Не понял список — попробуй ещё раз.")
        return True
    rows = await _match_inventory(items, pending.get("user_notion_id") or user_notion_id)
    pending["rows"] = rows
    pending["awaiting_edit"] = False
    _save(uid, pending)
    await message.answer(_format_preview(rows), reply_markup=_kb(uid))
    return True
