"""nexus/handlers/lists.py — хэндлеры 🗒️ Списки для ☀️ Nexus."""
from __future__ import annotations

import json
import logging
import re

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.list_manager import (
    add_items, get_list, check_items, check_items_bulk,
    checklist_toggle, checklist_toggle_by_id, buy_mark_done_by_id,
    inventory_search, inventory_update,
    pending_get, pending_set, pending_del, pending_pop,
    CATEGORY_TO_FINANCE, LIST_CATEGORIES,
)
from nexus.handlers.utils import react

logger = logging.getLogger("nexus.lists")
router = Router()

BOT_NAME = "☀️ Nexus"
HEADER = "🗒️ Списки · ☀️ Nexus"

# ── Haiku system prompts ─────────────────────────────────────────────────────

_PARSE_BUY_SYSTEM = (
    "Пользователь хочет добавить товары в список покупок. "
    "Извлеки список айтемов. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:\n"
    '[{"name":"молоко","category":"🍜 Продукты"},{"name":"корм коту","category":"🐾 Коты"}]\n'
    "\nКатегории: " + ", ".join(LIST_CATEGORIES) + "\n"
    "Правила:\n"
    "- Каждый айтем — отдельный объект\n"
    "- 'молоко, яйца, корм' → 3 объекта\n"
    '- category: подбирай из списка, с эмодзи\n'
    '- name: чистое название товара без категории\n'
)

_PARSE_DONE_SYSTEM = (
    "Пользователь сообщает о совершённой покупке. Извлеки айтемы с ценами. "
    "Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown.\n"
    "\nФормат единичной покупки:\n"
    '{"type":"list_done","items":[{"name":"молоко","price":89}],"category":"🍜 Продукты"}\n'
    "\nФормат пакетного чека (чек 4к привычки 1500 продукты 2500):\n"
    '{"type":"list_done_bulk","total":4000,"breakdown":[{"category":"привычки","amount":1500},{"category":"продукты","amount":2500}]}\n'
    "\nПравила:\n"
    "- 'купила молоко 89р' → list_done, items=[{name:'молоко', price:89}]\n"
    "- 'чек 4к привычки 1500 продукты 2500' → list_done_bulk\n"
    "- 'чек лента 2340 продукты' → list_done, items=[{name:'лента', price:2340}], category='🍜 Продукты'\n"
    "- к/тыс = ×1000: 4к=4000, 1.5к=1500\n"
)

_PARSE_INV_SYSTEM = (
    "Пользователь добавляет предмет в инвентарь (что есть дома). "
    "Извлеки данные. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"item":"парацетамол","quantity":2,"note":"верхний ящик ванной","category":"🏥 Здоровье"}\n'
    "\nКатегории: " + ", ".join(LIST_CATEGORIES) + "\n"
    "- quantity: число, по умолчанию 1\n"
    "- note: место хранения, бренд, детали (если есть)\n"
)

_PARSE_INV_UPDATE_SYSTEM = (
    "Пользователь сообщает об изменении количества предмета. "
    "Извлеки данные. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"item":"парацетамол","quantity":0}\n'
    "- 'закончился парацетамол' → quantity=0\n"
    "- 'осталась 1 пачка парацетамола' → quantity=1\n"
)

_PARSE_CHECK_SYSTEM = (
    "Пользователь создаёт чеклист. Извлеки данные. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name":"Собраться в поездку","items":["паспорт","зарядка","лекарства"]}\n'
    "- name: название чеклиста (группа)\n"
    "- items: список пунктов\n"
    "- Если пунктов нет — items пустой список\n"
)


# ── Haiku parse helper ────────────────────────────────────────────────────────

async def _haiku_parse(text: str, system: str) -> dict | list:
    raw = await ask_claude(text, system=system, max_tokens=500, model="claude-haiku-4-5-20251001")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ── Build list text + keyboard ────────────────────────────────────────────────

async def _build_list_view(
    list_type: str | None,
    bot_name: str,
    user_page_id: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать текст + inline keyboard для /list. Возвращает (text, keyboard|None)."""
    active = await get_list(list_type=list_type, bot_name=bot_name, user_page_id=user_page_id, status="Not started")

    # Для чеклистов — также Done айтемы (для отображения прогресса)
    done_checks: list[dict] = []
    if list_type is None or list_type == "📋 Чеклист":
        done_checks = await get_list(list_type="📋 Чеклист", bot_name=bot_name, user_page_id=user_page_id, status="Done")

    all_items = active + done_checks
    if not all_items:
        return "", None

    by_type: dict[str, list] = {}
    for it in all_items:
        t = it.get("type", "🛒 Покупки")
        by_type.setdefault(t, []).append(it)

    lines = [f"<b>{HEADER}</b>\n"]
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
                    icon = "✅" if is_done else "⬜"
                    lines.append(f"  {icon} {it['name']}")
                    if not is_done:
                        buttons.append([InlineKeyboardButton(
                            text=f"⬜ {it['name']}",
                            callback_data=f"list_chk_{it['id']}",
                        )])
        elif lt == "🛒 Покупки":
            not_done = [it for it in group if it.get("status") != "Done"]
            emoji_l = lt.split(" ")[0]
            label_l = lt.split(" ", 1)[1] if " " in lt else lt
            lines.append(f"\n<b>{emoji_l} {label_l.upper()}</b> ({len(not_done)})")
            for it in not_done:
                cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
                pri = ""
                if it.get("priority"):
                    pri_map = {"🔴 Срочно": "🔴", "🟡 Важно": "🟡", "⚪ Можно потом": ""}
                    pri = pri_map.get(it["priority"], "")
                    if pri:
                        pri = f" {pri}"
                lines.append(f"  ⬜ {it['name']} · {cat_emoji}{pri}")
                buttons.append([InlineKeyboardButton(
                    text=f"⬜ {it['name']}",
                    callback_data=f"list_buy_{it['id']}",
                )])
        else:
            # 📦 Инвентарь — не кликабельный
            emoji_l = lt.split(" ")[0]
            label_l = lt.split(" ", 1)[1] if " " in lt else lt
            lines.append(f"\n<b>{emoji_l} {label_l.upper()}</b> ({len(group)})")
            for it in group:
                cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
                qty = it.get("quantity", 0)
                extra = f" × {int(qty)}" if qty else ""
                if it.get("expiry"):
                    extra += f" · до {it['expiry'][:10]}"
                lines.append(f"  📦 {it['name']}{extra} · {cat_emoji}")

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    return "\n".join(lines), kb


# ── /list command (registered in nexus_bot.py on dp level) ────────────────────

async def handle_list_command(msg: Message, user_notion_id: str = "") -> None:
    args = (msg.text or "").split(maxsplit=1)
    sub = args[1].strip().lower() if len(args) > 1 else ""

    type_map = {"buy": "🛒 Покупки", "check": "📋 Чеклист", "inv": "📦 Инвентарь"}
    list_type = type_map.get(sub)

    text, kb = await _build_list_view(list_type, BOT_NAME, user_notion_id)
    if not text:
        label = list_type or "списков"
        await msg.answer(f"📭 Нет активных {label}.")
        return
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)


# ── list_buy handler ──────────────────────────────────────────────────────────

async def handle_list_buy(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🗒️")
    text = data.get("text", msg.text or "")

    try:
        parsed = await _haiku_parse(text, _PARSE_BUY_SYSTEM)
        if not isinstance(parsed, list):
            parsed = [parsed]
    except Exception as e:
        logger.error("handle_list_buy parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать список. Попробуй: «купить молоко, яйца»")
        return

    items = [{"name": p.get("name", ""), "category": p.get("category", "💳 Прочее")} for p in parsed if p.get("name")]
    if not items:
        await msg.answer("⚠️ Не нашёл айтемов.")
        return

    created = await add_items(items, "🛒 Покупки", BOT_NAME, user_notion_id)

    lines = ["🛒 <b>Добавлено в покупки:</b>"]
    for c in created:
        cat_emoji = c.get("category", "").split(" ")[0] if c.get("category") else ""
        lines.append(f"  ✓ {c['name']} · {cat_emoji}")

    # Подсказки из памяти (если note было добавлено)
    for c, orig in zip(created, items):
        note = orig.get("note", "")
        # note was enriched by add_items from memory
        # We need to re-check from created data, but page_create doesn't return note
        # So we rely on add_items having logged the memory search

    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_done handler ─────────────────────────────────────────────────────────

async def handle_list_done(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "💸")
    text = data.get("text", msg.text or "")

    try:
        parsed = await _haiku_parse(text, _PARSE_DONE_SYSTEM)
    except Exception as e:
        logger.error("handle_list_done parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать чек. Попробуй: «купила молоко 89р»")
        return

    done_type = parsed.get("type", "list_done") if isinstance(parsed, dict) else "list_done"

    if done_type == "list_done_bulk":
        total = parsed.get("total", 0)
        breakdown = parsed.get("breakdown", [])
        result = await check_items_bulk(total, breakdown, BOT_NAME, user_notion_id)

        lines = [f"🧾 <b>Чек: {total}₽</b>"]
        for fr in result.get("finance_results", []):
            lines.append(f"  💸 {fr['category']}: {int(fr['amount'])}₽")

        await msg.answer("\n".join(lines), parse_mode="HTML")

        # Проверка лимитов
        for fr in result.get("finance_results", []):
            try:
                from nexus.handlers.finance import _check_budget_limit
                await _check_budget_limit(fr["category"], msg, user_notion_id, amount=fr["amount"])
            except Exception as e:
                logger.error("list_done_bulk budget check: %s", e)
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
            nf = " (не в списке)" if ch.get("not_found") else ""
            lines.append(f"  ✓ {ch['name']}: {int(price)}₽{nf}")
        if total:
            lines.append(f"\n💰 Итого: {int(total)}₽")

        await msg.answer("\n".join(lines), parse_mode="HTML")

        # Проверка лимитов
        for fr in result.get("finance_results", []):
            try:
                from nexus.handlers.finance import _check_budget_limit
                await _check_budget_limit(fr["category"], msg, user_notion_id, amount=fr["amount"])
            except Exception as e:
                logger.error("list_done budget check: %s", e)


# ── list_check handler ────────────────────────────────────────────────────────

async def handle_list_check(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🗒️")
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
        # Нет пунктов — ждём их в pending
        pending_set(msg.from_user.id, {
            "action": "checklist_items",
            "group": name,
            "user_notion_id": user_notion_id,
        })
        await msg.answer(
            f"📋 <b>{name}</b>\n\nОтправь пункты чеклиста — каждый на новой строке или через запятую.",
            parse_mode="HTML",
        )
        return

    items = [{"name": it, "group": name} for it in items_raw if it]
    created = await add_items(items, "📋 Чеклист", BOT_NAME, user_notion_id)

    lines = [f"📋 <b>{name}</b> ({len(created)} пунктов)"]
    for c in created:
        lines.append(f"  ⬜ {c['name']}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_checklist_toggle ─────────────────────────────────────────────────────

async def handle_list_checklist_toggle(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "✅")
    item_name = data.get("item", data.get("text", msg.text or ""))

    result = await checklist_toggle(item_name, BOT_NAME, user_notion_id)
    if result.get("error") == "not_found":
        await msg.answer(f"❓ Не нашёл «{item_name}» в чеклистах.")
        return

    lines = [f"✅ {result['checked']}"]
    if result.get("group_complete"):
        lines.append(f"\n🎉 Чеклист «{result['group']}» завершён!")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_inventory_add ────────────────────────────────────────────────────────

async def handle_list_inv_add(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🗒️")
    text = data.get("text", msg.text or "")

    try:
        parsed = await _haiku_parse(text, _PARSE_INV_SYSTEM)
    except Exception as e:
        logger.error("handle_list_inv_add parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать. Попробуй: «дома есть: парацетамол»")
        return

    items = [{
        "name": parsed.get("item", ""),
        "quantity": parsed.get("quantity", 1),
        "note": parsed.get("note", ""),
        "category": parsed.get("category", "💳 Прочее"),
    }]

    created = await add_items(items, "📦 Инвентарь", BOT_NAME, user_notion_id)
    if created:
        c = created[0]
        await msg.answer(
            f"📦 <b>Инвентарь:</b> {c['name']} добавлен\n"
            f"Категория: {c.get('category', '')}",
            parse_mode="HTML",
        )
    else:
        await msg.answer("⚠️ Не удалось добавить.")


# ── list_inventory_search ─────────────────────────────────────────────────────

async def handle_list_inv_search(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    # Извлекаем запрос из текста
    query = re.sub(r"^есть\s+(?:ли\s+)?(?:у меня\s+)?", "", text, flags=re.IGNORECASE).strip().rstrip("?")

    results = await inventory_search(query, BOT_NAME, user_notion_id)
    if not results:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Добавить в покупки", callback_data=f"list_to_buy_{query[:30]}"),
        ]])
        await msg.answer(f"❌ «{query}» не найден в инвентаре.", reply_markup=kb)
        return

    lines = [f"📦 <b>Инвентарь: {query}</b>"]
    for r in results:
        qty = f" × {int(r['quantity'])}" if r.get("quantity") else ""
        note = f" — {r['note']}" if r.get("note") else ""
        expiry = f" · до {r['expiry'][:10]}" if r.get("expiry") else ""
        lines.append(f"  ✓ {r['name']}{qty}{note}{expiry}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_inventory_update ─────────────────────────────────────────────────────

async def handle_list_inv_update(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🗒️")
    text = data.get("text", msg.text or "")

    try:
        parsed = await _haiku_parse(text, _PARSE_INV_UPDATE_SYSTEM)
    except Exception as e:
        logger.error("handle_list_inv_update parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать.")
        return

    item_name = parsed.get("item", "")
    quantity = parsed.get("quantity", 0)

    result = await inventory_update(item_name, quantity, BOT_NAME, user_notion_id)
    if result.get("error") == "not_found":
        await msg.answer(f"❓ «{item_name}» не найден в инвентаре.")
        return

    if result.get("suggest_buy"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🛒 Добавить в покупки",
                callback_data=f"list_to_buy_{result['updated'][:30]}",
            ),
        ]])
        await msg.answer(
            f"📦 {result['updated']} — закончился, архивирован.",
            reply_markup=kb,
        )
    else:
        await msg.answer(f"📦 {result['updated']}: осталось {quantity}")


# ── Pending state handler ─────────────────────────────────────────────────────

async def handle_list_pending(msg: Message, user_notion_id: str = "") -> bool:
    """Обработать pending state для списков. Вызывается из handle_text ПЕРЕД classify().
    Возвращает True если обработал, False если нет pending."""
    uid = msg.from_user.id
    pending = pending_get(uid)
    if not pending:
        return False

    action = pending.get("action")
    text = (msg.text or "").strip()

    if action == "checklist_items":
        pending_del(uid)
        # Парсим пункты: каждая строка или через запятую
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

    if action == "buy_price":
        pending_del(uid)
        # Парсим цену
        price_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[кk]?", text)
        if not price_match:
            await msg.answer("⚠️ Не смог разобрать цену. Напиши число, например: 89")
            return True
        price_str = price_match.group(1).replace(",", ".")
        price = float(price_str)
        if "к" in text.lower() or "k" in text.lower():
            price *= 1000

        page_id = pending.get("page_id", "")
        p_user_id = pending.get("user_notion_id", user_notion_id)
        result = await buy_mark_done_by_id(page_id, price, BOT_NAME, p_user_id)

        if result.get("error"):
            await msg.answer("⚠️ Айтем не найден.")
            return True

        await msg.answer(
            f"✅ {result['name']}: {int(price)}₽ → 💰 Финансы",
            parse_mode="HTML",
        )

        # Проверка лимита
        if result.get("finance") and result["finance"].get("category"):
            try:
                from nexus.handlers.finance import _check_budget_limit
                await _check_budget_limit(result["finance"]["category"], msg, p_user_id, amount=price)
            except Exception as e:
                logger.error("buy_price budget check: %s", e)
        return True

    if action == "inv_expiry":
        pending_del(uid)
        # Парсим дату
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if date_match:
            from core.notion_client import update_page, _date
            item_id = pending.get("item_id", "")
            if item_id:
                await update_page(item_id, {"Срок годности": _date(date_match.group())})
                await msg.answer(f"📦 Срок годности: {date_match.group()}")
            return True
        await msg.answer("⚠️ Формат даты: YYYY-MM-DD")
        return True

    return False


# ── Callback: чеклист toggle ──────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_chk_"))
async def on_checklist_toggle(query: CallbackQuery, user_notion_id: str = "") -> None:
    page_id = query.data.replace("list_chk_", "")
    result = await checklist_toggle_by_id(page_id, BOT_NAME)

    if result.get("error"):
        await query.answer("⚠️ Не удалось обновить.")
        return

    # Уведомление
    note = f"🎉 Чеклист «{result['group']}» завершён!" if result.get("group_complete") else ""
    await query.answer(f"✅ {result['name']}" + (f" · {note}" if note else ""))

    # Обновить сообщение с новым списком
    try:
        text, kb = await _build_list_view(None, BOT_NAME, user_notion_id)
        if text:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await query.message.edit_text("🎉 Все списки выполнены!", parse_mode="HTML")
    except Exception as e:
        logger.warning("on_checklist_toggle edit: %s", e)


# ── Callback: покупка → спросить цену ─────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_buy_"))
async def on_buy_toggle(query: CallbackQuery, user_notion_id: str = "") -> None:
    page_id = query.data.replace("list_buy_", "")

    # Найти название айтема для вопроса
    from core.notion_client import get_notion
    try:
        raw = await get_notion().pages.retrieve(page_id)
        props = raw.get("properties", {})
        title_parts = props.get("Название", {}).get("title", [])
        item_name = title_parts[0]["plain_text"] if title_parts else "айтем"
    except Exception:
        item_name = "айтем"

    uid = query.from_user.id
    pending_set(uid, {
        "action": "buy_price",
        "page_id": page_id,
        "item_name": item_name,
        "user_notion_id": user_notion_id,
    })

    await query.answer(f"✅ {item_name}")
    await query.message.answer(
        f"✅ <b>{item_name}</b> — сколько потратила?",
        parse_mode="HTML",
    )


# ── Callback: добавить в покупки из инвентаря ─────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_to_buy_"))
async def on_list_to_buy(query: CallbackQuery, user_notion_id: str = "") -> None:
    item_name = query.data.replace("list_to_buy_", "")
    created = await add_items([{"name": item_name}], "🛒 Покупки", BOT_NAME, user_notion_id)
    if created:
        await query.message.edit_text(f"🛒 «{item_name}» добавлен в покупки!")
    else:
        await query.answer("⚠️ Не удалось добавить.")
