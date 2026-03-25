"""arcana/handlers/lists.py — хэндлеры 🗒️ Списки для 🌒 Arcana.

Мультиселект + умный чек (без бюджетных лимитов).
"""
from __future__ import annotations

import json
import logging
import re

from aiogram import Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.list_manager import (
    add_items, get_list, check_items, check_items_bulk,
    checklist_toggle, checklist_toggle_by_id, buy_mark_done_by_id,
    inventory_search, inventory_update, archive_items, mark_items_done,
    pending_get, pending_set, pending_del,
    CATEGORY_TO_FINANCE, LIST_CATEGORIES,
)
from core.notion_client import finance_add

logger = logging.getLogger("arcana.lists")
router = Router()

BOT_NAME = "🌒 Arcana"
HEADER = "🗒️ Списки · 🌒 Arcana"

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


def _checkout_parse_system(categories: dict[str, list[str]]) -> str:
    cats_desc = ", ".join(f"{c} ({', '.join(items)})" for c, items in categories.items())
    return (
        f"Разбери чек покупок. Контекст категорий: {cats_desc}.\n"
        "Верни ТОЛЬКО JSON без markdown:\n"
        '{"total": 2500, "source": "💳 Карта", "breakdown": [{"category": "🕯️ Расходники", "amount": 600}]}\n'
        '- "карта/картой" → "💳 Карта", "наличка/нал/наличные" → "💵 Наличные"\n'
        "- к/тыс = ×1000\n"
    )


async def _haiku_parse(text: str, system: str) -> dict | list:
    raw = await ask_claude(text, system=system, max_tokens=500, model="claude-haiku-4-5-20251001")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ── Shared view builder (same as Nexus but with HEADER) ──────────────────────

def _build_list_text_and_buttons(
    all_items: list[dict],
    selected: set[str],
    header: str,
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    by_type: dict[str, list] = {}
    for it in all_items:
        t = it.get("type", "🛒 Покупки")
        by_type.setdefault(t, []).append(it)

    lines = [f"<b>{header}</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []

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
                    is_done = it.get("status") == "Done"
                    is_sel = it["id"] in selected
                    icon = "✅" if (is_done or is_sel) else "◻️"
                    lines.append(f"  {icon} {it['name']}")
                    if not is_done:
                        btn_text = f"✅ {it['name']}" if is_sel else it['name']
                        buttons.append([InlineKeyboardButton(
                            text=btn_text,
                            callback_data=f"lt_{it['id'][:28]}",
                        )])
        elif lt == "🛒 Покупки":
            not_done = [it for it in group if it.get("status") != "Done"]
            lines.append(f"\n<b>🛒 ПОКУПКИ</b> ({len(not_done)})")
            for it in not_done:
                cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
                is_sel = it["id"] in selected
                icon = "✅" if is_sel else "◻️"
                lines.append(f"  {icon} {it['name']} · {cat_emoji}")
                btn_text = f"✅ {it['name']}" if is_sel else it['name']
                buttons.append([InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"lt_{it['id'][:28]}",
                )])
        else:
            lines.append(f"\n<b>📦 ИНВЕНТАРЬ</b> ({len(group)})")
            for it in group:
                cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
                qty = it.get("quantity") or 0
                extra = f" × {int(qty)}" if qty else ""
                if it.get("expiry"):
                    extra += f" · до {it['expiry'][:10]}"
                lines.append(f"  📦 {it['name']}{extra} · {cat_emoji}")

    if selected:
        buttons.append([InlineKeyboardButton(text="✅ Чек", callback_data="lt_checkout")])

    return "\n".join(lines), buttons


async def _fetch_all_display_items(list_type, bot_name, user_page_id):
    active = await get_list(list_type=list_type, bot_name=bot_name, user_page_id=user_page_id, status="Not started")
    done_checks: list[dict] = []
    if list_type is None or list_type == "📋 Чеклист":
        done_checks = await get_list(list_type="📋 Чеклист", bot_name=bot_name, user_page_id=user_page_id, status="Done")
    return active + done_checks


# ── /list command ─────────────────────────────────────────────────────────────

async def handle_list_command(msg: Message, user_notion_id: str = "") -> None:
    args = (msg.text or "").split(maxsplit=1)
    sub = args[1].strip().lower() if len(args) > 1 else ""
    type_map = {"buy": "🛒 Покупки", "check": "📋 Чеклист", "inv": "📦 Инвентарь"}
    list_type = type_map.get(sub)

    all_items = await _fetch_all_display_items(list_type, BOT_NAME, user_notion_id)
    if not all_items:
        await msg.answer(f"📭 Нет активных {list_type or 'списков'}.")
        return

    uid = msg.from_user.id
    pending_del(uid)
    text, buttons = _build_list_text_and_buttons(all_items, set(), HEADER)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    sent = await msg.answer(text, parse_mode="HTML", reply_markup=kb)
    pending_set(uid, {
        "action": "list_select",
        "selected": [],
        "msg_id": sent.message_id,
        "user_notion_id": user_notion_id,
        "list_type": list_type,
        "bot": "arcana",
    })


# ── Callback: toggle ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("lt_") and c.data != "lt_checkout" and not c.data.startswith("lt_remain_"))
async def on_list_toggle(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    page_id_short = query.data.replace("lt_", "")

    if page_id_short.startswith("remain_"):
        await _handle_remain(query, user_notion_id)
        return

    pending = pending_get(uid)
    if not pending or pending.get("action") not in ("list_select", "list_remain_select"):
        await query.answer("⏰ Сессия истекла. Отправь /list заново.")
        return

    selected: list[str] = pending.get("selected", [])
    all_items = await _fetch_all_display_items(pending.get("list_type"), BOT_NAME, pending.get("user_notion_id", user_notion_id))

    full_id = None
    for it in all_items:
        if it["id"].startswith(page_id_short):
            full_id = it["id"]
            break
    if not full_id:
        await query.answer("❓ Айтем не найден.")
        return

    if full_id in selected:
        selected.remove(full_id)
    else:
        selected.append(full_id)
    pending["selected"] = selected
    pending_set(uid, pending)

    is_remain = pending.get("action") == "list_remain_select"
    text, buttons = _build_list_text_and_buttons(all_items, set(selected), HEADER)

    if is_remain:
        buttons = [b for b in buttons if b[0].callback_data != "lt_checkout"]
        bottom = []
        if selected:
            bottom.append([InlineKeyboardButton(text="🗑️ Архивировать выбранное", callback_data="lt_remain_archive")])
        bottom.append([
            InlineKeyboardButton(text="✅ Оставить всё", callback_data="lt_remain_keep"),
            InlineKeyboardButton(text="🗑️ Архивировать всё", callback_data="lt_remain_archive_all"),
        ])
        buttons.extend(bottom)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await query.answer()


async def _handle_remain(query: CallbackQuery, user_notion_id: str) -> None:
    uid = query.from_user.id
    action = query.data

    if action == "lt_remain_keep":
        pending_del(uid)
        await query.answer("✅ Оставлено")
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    pending = pending_get(uid)
    p_user_id = pending.get("user_notion_id", user_notion_id) if pending else user_notion_id

    if action == "lt_remain_archive_all":
        pending_del(uid)
        remaining = await get_list("🛒 Покупки", BOT_NAME, p_user_id, status="Not started")
        ids = [it["id"] for it in remaining]
        names = [it["name"] for it in remaining]
        if ids:
            await archive_items(ids)
            await query.message.edit_text(f"🗑️ Архивировано: {', '.join(names)}\n🗒️ Список чист!", parse_mode="HTML")
        return

    if action == "lt_remain_archive":
        if not pending:
            await query.answer("⏰ Сессия истекла.")
            return
        selected = pending.get("selected", [])
        pending_del(uid)
        if not selected:
            await query.answer("Сначала выбери айтемы.")
            return
        all_items = await _fetch_all_display_items(None, BOT_NAME, p_user_id)
        items_map = {it["id"]: it for it in all_items}
        names = [items_map[p]["name"] for p in selected if p in items_map]
        await archive_items(selected)
        remaining = await get_list("🛒 Покупки", BOT_NAME, p_user_id, status="Not started")
        if remaining:
            text = f"🗑️ Архивировано: {', '.join(names)}\n🗒️ В списке: {', '.join(it['name'] for it in remaining)}"
        else:
            text = f"🗑️ Архивировано: {', '.join(names)}\n🗒️ Список чист!"
        await query.message.edit_text(text, parse_mode="HTML")


# ── Callback: checkout ────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "lt_checkout")
async def on_checkout(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("action") != "list_select":
        await query.answer("⏰ Сессия истекла.")
        return

    selected: list[str] = pending.get("selected", [])
    if not selected:
        await query.answer("Сначала выбери айтемы!")
        return

    p_user_id = pending.get("user_notion_id", user_notion_id)
    all_items = await _fetch_all_display_items(pending.get("list_type"), BOT_NAME, p_user_id)
    items_map = {it["id"]: it for it in all_items}

    buy_items: dict[str, dict] = {}
    check_ids: list[str] = []
    for pid in selected:
        it = items_map.get(pid)
        if not it:
            continue
        if it.get("type") == "📋 Чеклист":
            check_ids.append(pid)
        else:
            buy_items[pid] = it

    if check_ids:
        await mark_items_done(check_ids)
        names = [items_map[p]["name"] for p in check_ids if p in items_map]
        await query.message.answer(f"✅ Чеклист: {', '.join(names)}", parse_mode="HTML")

    if buy_items:
        categories: dict[str, list[str]] = {}
        selected_data: dict[str, dict] = {}
        for pid, it in buy_items.items():
            cat = it.get("category", "💳 Прочее")
            categories.setdefault(cat, []).append(it["name"])
            selected_data[pid] = {"name": it["name"], "category": cat}

        pending_del(uid)
        pending_set(uid, {
            "action": "list_checkout",
            "selected": selected_data,
            "categories": categories,
            "user_notion_id": p_user_id,
            "bot": "arcana",
        })

        lines = ["🛒 <b>Выбрано:</b>"]
        for cat, names in categories.items():
            cat_emoji = cat.split(" ")[0] if " " in cat else ""
            lines.append(f"  {cat_emoji} {cat}: {', '.join(names)}")
        lines.append("\nСколько потратила? 💳 карта / 💵 наличные?")
        await query.message.answer("\n".join(lines), parse_mode="HTML")

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer("✅")


# ── Text handlers (buy/done/check/inv — same as before) ──────────────────────

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
        result = await check_items_bulk(parsed.get("total") or 0, parsed.get("breakdown", []), BOT_NAME, user_notion_id)
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
            price = ch.get("price") or 0
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
        pending_set(msg.from_user.id, {"action": "checklist_items", "group": name, "user_notion_id": user_notion_id, "bot": "arcana"})
        await msg.answer(f"📋 <b>{name}</b>\n\nОтправь пункты чеклиста.", parse_mode="HTML")
        return
    items = [{"name": it, "group": name} for it in items_raw if it]
    created = await add_items(items, "📋 Чеклист", BOT_NAME, user_notion_id)
    lines = [f"📋 <b>{name}</b> ({len(created)} пунктов)"]
    for c in created:
        lines.append(f"  ◻️ {c['name']}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


async def handle_list_inv_add(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_INV_SYSTEM)
    except Exception:
        await msg.answer("⚠️ Не смог разобрать.")
        return
    items = [{"name": parsed.get("item", ""), "quantity": parsed.get("quantity") or 1, "note": parsed.get("note", ""), "category": parsed.get("category", "🕯️ Расходники")}]
    created = await add_items(items, "📦 Инвентарь", BOT_NAME, user_notion_id)
    if created:
        await msg.answer(f"📦 <b>Инвентарь:</b> {created[0]['name']} добавлен · {created[0].get('category', '')}", parse_mode="HTML")


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
    except Exception:
        await msg.answer("⚠️ Не смог разобрать.")
        return
    result = await inventory_update(parsed.get("item", ""), parsed.get("quantity") or 0, BOT_NAME, user_notion_id)
    if result.get("error") == "not_found":
        await msg.answer(f"❓ «{parsed.get('item', '')}» не найден в инвентаре.")
        return
    if result.get("suggest_buy"):
        await msg.answer(f"📦 {result['updated']} — закончился, архивирован.")
    else:
        await msg.answer(f"📦 {result['updated']}: осталось {result.get('quantity', 0)}")


# ── Pending handler ───────────────────────────────────────────────────────────

async def handle_list_pending(msg: Message, user_notion_id: str = "") -> bool:
    uid = msg.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("bot") != "arcana":
        return False

    action = pending.get("action")
    text = (msg.text or "").strip()

    if action in ("list_select", "list_remain_select"):
        return False

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
        created = await add_items(items, "📋 Чеклист", BOT_NAME, pending.get("user_notion_id", user_notion_id))
        lines = [f"📋 <b>{group}</b> ({len(created)} пунктов)"]
        for c in created:
            lines.append(f"  ◻️ {c['name']}")
        await msg.answer("\n".join(lines), parse_mode="HTML")
        return True

    if action == "list_checkout":
        categories = pending.get("categories", {})
        selected_data = pending.get("selected", {})
        p_user_id = pending.get("user_notion_id", user_notion_id)

        try:
            parsed = await _haiku_parse(text, _checkout_parse_system(categories))
        except Exception:
            await msg.answer("⚠️ Не смог разобрать. Попробуй: «2500 картой»")
            return True

        total = parsed.get("total") or 0
        source = parsed.get("source", "💳 Карта")
        breakdown = parsed.get("breakdown", [])
        named_sum = sum(b.get("amount") or 0 for b in breakdown)
        if not total:
            total = named_sum

        named_cats: dict[str, float] = {}
        for b in breakdown:
            raw_cat = b.get("category", "")
            amount = b.get("amount") or 0
            matched = None
            for full_cat in categories:
                clean = full_cat.split(" ", 1)[-1].lower() if " " in full_cat else full_cat.lower()
                if clean in raw_cat.lower() or raw_cat.lower() in clean:
                    matched = full_cat
                    break
            named_cats[matched or raw_cat] = amount

        remaining_cats = [c for c in categories if c not in named_cats]
        named_total = sum(named_cats.values())
        remainder = total - named_total

        if len(remaining_cats) == 1 and remainder > 0:
            named_cats[remaining_cats[0]] = remainder
        elif len(remaining_cats) > 1 and remainder > 0:
            first_cat = remaining_cats[0]
            rest = remaining_cats[1:]
            pending_del(uid)
            pending_set(uid, {
                "action": "list_checkout_split",
                "named_cats": named_cats, "remaining_cats": rest,
                "remainder": remainder, "total": total, "source": source,
                "selected": selected_data, "categories": categories,
                "user_notion_id": p_user_id, "asking_cat": first_cat, "bot": "arcana",
            })
            await msg.answer(f"Осталось {int(remainder)}₽ на {', '.join(remaining_cats)}.\nСколько на {first_cat}?")
            return True
        elif not remaining_cats and not named_cats and total > 0 and len(categories) == 1:
            named_cats[list(categories.keys())[0]] = total

        pending_del(uid)
        await _finalize_checkout(msg, named_cats, source, selected_data, categories, p_user_id)
        return True

    if action == "list_checkout_split":
        price_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[кk]?", text)
        if not price_match:
            await msg.answer("⚠️ Напиши число, например: 800")
            return True
        amount = float(price_match.group(1).replace(",", "."))
        if "к" in text.lower() or "k" in text.lower():
            amount *= 1000

        asking_cat = pending.get("asking_cat", "")
        named_cats = pending.get("named_cats", {})
        named_cats[asking_cat] = amount
        remaining_cats = pending.get("remaining_cats", [])
        remainder = pending.get("remainder") or 0 - amount

        if len(remaining_cats) == 1 and remainder > 0:
            named_cats[remaining_cats[0]] = remainder
            pending_del(uid)
            await _finalize_checkout(msg, named_cats, pending.get("source", "💳 Карта"),
                                     pending.get("selected", {}), pending.get("categories", {}),
                                     pending.get("user_notion_id", user_notion_id))
        elif remaining_cats:
            next_cat = remaining_cats[0]
            pending["named_cats"] = named_cats
            pending["remaining_cats"] = remaining_cats[1:]
            pending["remainder"] = remainder
            pending["asking_cat"] = next_cat
            pending_set(uid, pending)
            await msg.answer(f"Сколько на {next_cat}? (осталось {int(remainder)}₽)")
        else:
            pending_del(uid)
            await _finalize_checkout(msg, named_cats, pending.get("source", "💳 Карта"),
                                     pending.get("selected", {}), pending.get("categories", {}),
                                     pending.get("user_notion_id", user_notion_id))
        return True

    return False


async def _finalize_checkout(msg, named_cats, source, selected_data, categories, user_page_id):
    from core.list_manager import _today_iso

    lines = ["✅ <b>Чек записан:</b>"]
    for cat, amount in named_cats.items():
        if amount <= 0:
            continue
        item_names = categories.get(cat, [])
        title = ", ".join(item_names) if item_names else cat
        finance_cat = CATEGORY_TO_FINANCE.get(cat, "💳 Прочее")
        await finance_add(date=_today_iso(), amount=float(amount), category=finance_cat,
                          type_="💸 Расход", source=source, description=title,
                          bot_label=BOT_NAME, user_notion_id=user_page_id)
        cat_emoji = cat.split(" ")[0] if " " in cat else ""
        lines.append(f"  {cat_emoji} {cat}: {int(amount)}₽ ({title})")

    page_ids = list(selected_data.keys())
    if page_ids:
        await mark_items_done(page_ids)

    total = sum(named_cats.values())
    lines.append(f"\n💰 Итого: {int(total)}₽ · {source}")
    await msg.answer("\n".join(lines), parse_mode="HTML")
