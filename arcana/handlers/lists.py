"""arcana/handlers/lists.py — хэндлеры 🗒️ Списки для 🌒 Arcana."""
from __future__ import annotations

import json
import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.list_manager import (
    add_items, get_list, check_items, check_items_bulk,
    checklist_toggle, inventory_search, inventory_update,
    pending_get, pending_set, pending_del, pending_pop,
    CATEGORY_TO_FINANCE, LIST_CATEGORIES,
)

logger = logging.getLogger("arcana.lists")
router = Router()

BOT_NAME = "🌒 Arcana"
HEADER = "🗒️ Списки · 🌒 Arcana"

# ── Haiku system prompts (Arcana-specific categories) ─────────────────────────

_ARCANA_CATS = ["🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды", "💳 Прочее"]

_PARSE_BUY_SYSTEM = (
    "Пользователь хочет добавить товары в список покупок (магические расходники). "
    "Извлеки список айтемов. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:\n"
    '[{"name":"ладан","category":"🕯️ Расходники"},{"name":"масло розы","category":"🌿 Травы/Масла"}]\n'
    "\nКатегории: " + ", ".join(LIST_CATEGORIES) + "\n"
    "Для Арканы типичные: 🕯️ Расходники (свечи, ладан), 🌿 Травы/Масла, 🃏 Карты/Колоды\n"
)

_PARSE_DONE_SYSTEM = (
    "Пользователь сообщает о совершённой покупке. Извлеки айтемы с ценами. "
    "Ответь ТОЛЬКО JSON без markdown.\n"
    '{"type":"list_done","items":[{"name":"ладан","price":120}],"category":"🕯️ Расходники"}\n'
    "- к/тыс = ×1000: 4к=4000\n"
)

_PARSE_INV_SYSTEM = (
    "Пользователь добавляет предмет в инвентарь. "
    "Извлеки данные. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"item":"свечи красные","quantity":5,"note":"шкаф алтарной","category":"🕯️ Расходники"}\n'
    "\nКатегории: " + ", ".join(_ARCANA_CATS) + "\n"
)

_PARSE_INV_UPDATE_SYSTEM = (
    "Пользователь сообщает об изменении количества. "
    "Ответь ТОЛЬКО JSON без markdown:\n"
    '{"item":"свечи красные","quantity":2}\n'
    "- 'закончились красные свечи' → quantity=0\n"
)

_PARSE_CHECK_SYSTEM = (
    "Пользователь создаёт чеклист для работы/ритуала. "
    "Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name":"Ритуал защиты","items":["свечи","ладан","масло"]}\n'
)


async def _haiku_parse(text: str, system: str) -> dict | list:
    raw = await ask_claude(text, system=system, max_tokens=500, model="claude-haiku-4-5-20251001")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ── /list command ─────────────────────────────────────────────────────────────

@router.message(Command("list"))
async def handle_list_command(msg: Message, user_notion_id: str = "") -> None:
    args = (msg.text or "").split(maxsplit=1)
    sub = args[1].strip().lower() if len(args) > 1 else ""

    type_map = {"buy": "🛒 Покупки", "check": "📋 Чеклист", "inv": "📦 Инвентарь"}
    list_type = type_map.get(sub)

    items = await get_list(list_type=list_type, bot_name=BOT_NAME, user_page_id=user_notion_id)
    if not items:
        label = list_type or "списков"
        await msg.answer(f"📭 Нет активных {label}.")
        return

    by_type: dict[str, list] = {}
    for it in items:
        t = it.get("type", "🛒 Покупки")
        by_type.setdefault(t, []).append(it)

    lines = [f"<b>{HEADER}</b>\n"]

    for lt in ["🛒 Покупки", "📋 Чеклист", "📦 Инвентарь"]:
        group = by_type.get(lt, [])
        if not group:
            continue

        if lt == "📋 Чеклист":
            by_group: dict[str, list] = {}
            for it in group:
                g = it.get("group", "") or "Без группы"
                by_group.setdefault(g, []).append(it)
            for gname, gitems in by_group.items():
                done = sum(1 for i in gitems if i.get("status") == "Done")
                lines.append(f"\n<b>📋 {gname}</b> ({done}/{len(gitems)})")
                for it in gitems:
                    icon = "✅" if it.get("status") == "Done" else "⬜"
                    lines.append(f"  {icon} {it['name']}")
        else:
            emoji = lt.split(" ")[0]
            label = lt.split(" ", 1)[1] if " " in lt else lt
            lines.append(f"\n<b>{emoji} {label.upper()}</b> ({len(group)})")
            for it in group:
                cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
                extra = ""
                if lt == "📦 Инвентарь":
                    qty = it.get("quantity", 0)
                    if qty:
                        extra = f" × {int(qty)}"
                    if it.get("expiry"):
                        extra += f" · до {it['expiry'][:10]}"
                lines.append(f"  ⬜ {it['name']}{extra} · {cat_emoji}")

    await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_buy(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_BUY_SYSTEM)
        if not isinstance(parsed, list):
            parsed = [parsed]
    except Exception as e:
        logger.error("handle_list_buy parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать список.")
        return

    items = [{"name": p.get("name", ""), "category": p.get("category", "🕯️ Расходники")} for p in parsed if p.get("name")]
    if not items:
        await msg.answer("⚠️ Не нашёл айтемов.")
        return

    created = await add_items(items, "🛒 Покупки", BOT_NAME, user_notion_id)
    lines = ["🛒 <b>Добавлено (Arcana):</b>"]
    for c in created:
        cat_emoji = c.get("category", "").split(" ")[0] if c.get("category") else ""
        lines.append(f"  ✓ {c['name']} · {cat_emoji}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_done(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_DONE_SYSTEM)
    except Exception as e:
        logger.error("handle_list_done parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать чек.")
        return

    done_type = parsed.get("type", "list_done") if isinstance(parsed, dict) else "list_done"

    if done_type == "list_done_bulk":
        result = await check_items_bulk(parsed.get("total", 0), parsed.get("breakdown", []), BOT_NAME, user_notion_id)
        lines = [f"🧾 <b>Чек: {parsed.get('total', 0)}₽</b>"]
        for fr in result.get("finance_results", []):
            lines.append(f"  💸 {fr['category']}: {int(fr['amount'])}₽")
        await msg.answer("\n".join(lines), parse_mode="HTML")
    else:
        items_data = parsed.get("items", [])
        category = parsed.get("category")
        if category:
            for it in items_data:
                if not it.get("category"):
                    it["category"] = category
        result = await check_items(items_data, BOT_NAME, user_notion_id)
        lines = ["✅ <b>Чек записан:</b>"]
        total = 0
        for ch in result.get("checked", []):
            price = ch.get("price", 0)
            total += price
            lines.append(f"  ✓ {ch['name']}: {int(price)}₽")
        if total:
            lines.append(f"\n💰 Итого: {int(total)}₽")
        await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_check(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_CHECK_SYSTEM)
    except Exception as e:
        logger.error("handle_list_check parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать чеклист.")
        return

    name = parsed.get("name", "Чеклист")
    items_raw = parsed.get("items", [])

    if not items_raw:
        pending_set(msg.from_user.id, {
            "action": "checklist_items",
            "group": name,
            "user_notion_id": user_notion_id,
            "bot": "arcana",
        })
        await msg.answer(f"📋 <b>{name}</b>\n\nОтправь пункты чеклиста.", parse_mode="HTML")
        return

    # Для Arcana: relation → 🔮 Работы (не ✅ Задачи)
    items = [{"name": it, "group": name} for it in items_raw if it]
    created = await add_items(items, "📋 Чеклист", BOT_NAME, user_notion_id)
    lines = [f"📋 <b>{name}</b> ({len(created)} пунктов)"]
    for c in created:
        lines.append(f"  ⬜ {c['name']}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_inv_add(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_INV_SYSTEM)
    except Exception as e:
        logger.error("handle_list_inv_add parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать.")
        return

    items = [{
        "name": parsed.get("item", ""),
        "quantity": parsed.get("quantity", 1),
        "note": parsed.get("note", ""),
        "category": parsed.get("category", "🕯️ Расходники"),
    }]
    created = await add_items(items, "📦 Инвентарь", BOT_NAME, user_notion_id)
    if created:
        c = created[0]
        await msg.answer(f"📦 <b>Инвентарь:</b> {c['name']} добавлен · {c.get('category', '')}", parse_mode="HTML")


async def handle_list_inv_search(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    query = re.sub(r"^есть\s+(?:ли\s+)?(?:у меня\s+)?", "", text, flags=re.IGNORECASE).strip().rstrip("?")

    results = await inventory_search(query, BOT_NAME, user_notion_id)
    if not results:
        await msg.answer(f"❌ «{query}» не найден в инвентаре.")
        return

    lines = [f"📦 <b>Инвентарь: {query}</b>"]
    for r in results:
        qty = f" × {int(r['quantity'])}" if r.get("quantity") else ""
        note = f" — {r['note']}" if r.get("note") else ""
        lines.append(f"  ✓ {r['name']}{qty}{note}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_inv_update(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_INV_UPDATE_SYSTEM)
    except Exception as e:
        logger.error("handle_list_inv_update parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать.")
        return

    result = await inventory_update(parsed.get("item", ""), parsed.get("quantity", 0), BOT_NAME, user_notion_id)
    if result.get("error") == "not_found":
        await msg.answer(f"❓ «{parsed.get('item', '')}» не найден в инвентаре.")
        return

    if result.get("suggest_buy"):
        await msg.answer(f"📦 {result['updated']} — закончился, архивирован.")
    else:
        await msg.answer(f"📦 {result['updated']}: осталось {result.get('quantity', 0)}")


async def handle_list_pending(msg: Message, user_notion_id: str = "") -> bool:
    """Обработать pending для Arcana списков."""
    uid = msg.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("bot") != "arcana":
        return False

    action = pending.get("action")
    text = (msg.text or "").strip()

    if action == "checklist_items":
        pending_del(uid)
        raw_items = []
        for line in text.split("\n"):
            for part in line.split(","):
                part = part.strip().lstrip("•·-–— ").strip()
                if part:
                    raw_items.append(part)
        group = pending.get("group", "Чеклист")
        items = [{"name": it, "group": group} for it in raw_items]
        p_user_id = pending.get("user_notion_id", user_notion_id)
        created = await add_items(items, "📋 Чеклист", BOT_NAME, p_user_id)
        lines = [f"📋 <b>{group}</b> ({len(created)} пунктов)"]
        for c in created:
            lines.append(f"  ⬜ {c['name']}")
        await msg.answer("\n".join(lines), parse_mode="HTML")
        return True

    return False
