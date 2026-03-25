"""nexus/handlers/lists.py — хэндлеры 🗒️ Списки для ☀️ Nexus.

Мультиселект → группировка по категориям → свободный текст с ценами → бот считает остаток.
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
from core.notion_client import finance_add, update_page, _status
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


def _checkout_parse_system(categories: dict[str, list[str]]) -> str:
    """Haiku prompt для парсинга текста чека с контекстом категорий."""
    cats_desc = ", ".join(f"{c} ({', '.join(items)})" for c, items in categories.items())
    return (
        f"Разбери чек покупок. Контекст категорий: {cats_desc}.\n"
        "Верни ТОЛЬКО JSON без markdown:\n"
        '{"total": 2500, "source": "💳 Карта", "breakdown": [{"category": "🚬 Привычки", "amount": 600}]}\n'
        "\nПравила:\n"
        '- "карта/картой" → "💳 Карта", "наличка/нал/наличные" → "💵 Наличные"\n'
        '- Если не указан источник → "💳 Карта"\n'
        "- НЕ считай остаток — только то что явно указано\n"
        '- "из них X 600" → ищи X среди категорий\n'
        "- Число без категории = total\n"
        "- к/тыс = ×1000: 4к=4000\n"
    )


async def _haiku_parse(text: str, system: str) -> dict | list:
    raw = await ask_claude(text, system=system, max_tokens=500, model="claude-haiku-4-5-20251001")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ── Build list view with multiselect ─────────────────────────────────────────

def _build_list_text_and_buttons(
    all_items: list[dict],
    selected: set[str],
    header: str,
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Построить текст и кнопки для /list. selected = set page_id для ✅."""
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

    # Кнопка "✅ Чек" если есть выбранные
    if selected:
        buttons.append([InlineKeyboardButton(text="✅ Чек", callback_data="lt_checkout")])

    return "\n".join(lines), buttons


async def _fetch_all_display_items(
    list_type: str | None,
    bot_name: str,
    user_page_id: str,
) -> list[dict]:
    """Получить все айтемы для отображения (active + done checklists)."""
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
    # Очистить старый select state
    pending_del(uid)

    text, buttons = _build_list_text_and_buttons(all_items, set(), HEADER)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    sent = await msg.answer(text, parse_mode="HTML", reply_markup=kb)

    # Сохранить state мультиселекта
    pending_set(uid, {
        "action": "list_select",
        "selected": [],
        "msg_id": sent.message_id,
        "user_notion_id": user_notion_id,
        "list_type": list_type,
    })


# ── Callback: toggle (multiselect) ───────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("lt_") and c.data != "lt_checkout" and not c.data.startswith("lt_remain_"))
async def on_list_toggle(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    page_id_short = query.data.replace("lt_", "")

    pending = pending_get(uid)
    if not pending or pending.get("action") not in ("list_select", "list_remain_select"):
        await query.answer("⏰ Сессия истекла. Отправь /list заново.")
        return

    selected: list[str] = pending.get("selected", [])

    # Найти полный page_id по префиксу
    full_id = None
    all_items = await _fetch_all_display_items(
        pending.get("list_type"), BOT_NAME, pending.get("user_notion_id", user_notion_id)
    )
    for it in all_items:
        if it["id"].startswith(page_id_short):
            full_id = it["id"]
            break

    if not full_id:
        await query.answer("❓ Айтем не найден.")
        return

    # Toggle
    if full_id in selected:
        selected.remove(full_id)
    else:
        selected.append(full_id)

    pending["selected"] = selected
    pending_set(uid, pending)

    # Определить header по action
    is_remain = pending.get("action") == "list_remain_select"
    header = HEADER

    # Rebuild
    sel_set = set(selected)
    text, buttons = _build_list_text_and_buttons(all_items, sel_set, header)

    if is_remain:
        # Для remain — свои кнопки внизу
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


# ── Callback: ✅ Чек (checkout) ───────────────────────────────────────────────

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

    # Загружаем данные выбранных айтемов
    all_items = await _fetch_all_display_items(
        pending.get("list_type"), BOT_NAME, p_user_id
    )
    items_map: dict[str, dict] = {it["id"]: it for it in all_items}

    # Разделяем по типу
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

    # Чеклист — сразу Done, без цены
    if check_ids:
        await mark_items_done(check_ids)
        checked_names = [items_map[p]["name"] for p in check_ids if p in items_map]
        # Проверяем автозавершение групп
        groups_done = set()
        for pid in check_ids:
            it = items_map.get(pid)
            if it and it.get("group"):
                remaining = [i for i in all_items
                             if i.get("type") == "📋 Чеклист"
                             and i.get("group") == it["group"]
                             and i.get("status") != "Done"
                             and i["id"] not in check_ids]
                if not remaining:
                    groups_done.add(it["group"])

        lines = [f"✅ Чеклист: {', '.join(checked_names)}"]
        for g in groups_done:
            lines.append(f"🎉 Чеклист «{g}» завершён!")
        await query.message.answer("\n".join(lines), parse_mode="HTML")

    # Покупки — нужна цена
    if buy_items:
        # Группируем по категории
        categories: dict[str, list[str]] = {}
        selected_data: dict[str, dict] = {}
        for pid, it in buy_items.items():
            cat = it.get("category", "💳 Прочее")
            categories.setdefault(cat, []).append(it["name"])
            selected_data[pid] = {"name": it["name"], "category": cat}

        # Обновить pending → checkout
        pending_del(uid)
        pending_set(uid, {
            "action": "list_checkout",
            "selected": selected_data,
            "categories": categories,
            "user_notion_id": p_user_id,
        })

        lines = ["🛒 <b>Выбрано:</b>"]
        for cat, names in categories.items():
            cat_emoji = cat.split(" ")[0] if " " in cat else ""
            lines.append(f"  {cat_emoji} {cat}: {', '.join(names)}")
        lines.append("\nСколько потратила? 💳 карта / 💵 наличные?")

        await query.message.answer("\n".join(lines), parse_mode="HTML")

    if not buy_items and not check_ids:
        await query.answer("Сначала выбери айтемы!")
        return

    # Убрать кнопки с исходного сообщения
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer("✅")


# ── Callback: remain (оставшиеся айтемы) ─────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("lt_remain_"))
async def on_remain_action(query: CallbackQuery, user_notion_id: str = "") -> None:
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

    if action == "lt_remain_archive_all":
        pending_del(uid)
        # Архивировать все Not started покупки
        remaining = await get_list("🛒 Покупки", BOT_NAME, pending.get("user_notion_id", user_notion_id), status="Not started")
        ids = [it["id"] for it in remaining]
        names = [it["name"] for it in remaining]
        if ids:
            await archive_items(ids)
            await query.message.edit_text(
                f"🗑️ Архивировано: {', '.join(names)}\n🗒️ Список чист!",
                parse_mode="HTML",
            )
        else:
            await query.answer("Список уже пуст.")
        return

    if action == "lt_remain_archive":
        if not pending or pending.get("action") != "list_remain_select":
            await query.answer("⏰ Сессия истекла.")
            return
        selected = pending.get("selected", [])
        pending_del(uid)
        if not selected:
            await query.answer("Сначала выбери айтемы для архивации.")
            return

        all_items = await _fetch_all_display_items(None, BOT_NAME, pending.get("user_notion_id", user_notion_id))
        items_map = {it["id"]: it for it in all_items}
        names = [items_map[p]["name"] for p in selected if p in items_map]
        await archive_items(selected)

        # Проверяем что осталось
        remaining = await get_list("🛒 Покупки", BOT_NAME, pending.get("user_notion_id", user_notion_id), status="Not started")
        if remaining:
            remain_names = [it["name"] for it in remaining]
            text = f"🗑️ Архивировано: {', '.join(names)}\n🗒️ В списке осталось: {', '.join(remain_names)}"
        else:
            text = f"🗑️ Архивировано: {', '.join(names)}\n🗒️ Список чист!"
        await query.message.edit_text(text, parse_mode="HTML")
        return

    await query.answer()


# ── list_buy handler (text "купить X") ────────────────────────────────────────

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
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_done handler (text "купила X 89р") ───────────────────────────────────

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
        result = await check_items_bulk(parsed.get("total") or 0, parsed.get("breakdown", []), BOT_NAME, user_notion_id)
        lines = [f"🧾 <b>Чек: {parsed.get('total', 0)}₽</b>"]
        for fr in result.get("finance_results", []):
            lines.append(f"  💸 {fr['category']}: {int(fr['amount'])}₽")
        await msg.answer("\n".join(lines), parse_mode="HTML")
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
            price = ch.get("price") or 0
            total += price
            nf = " (не в списке)" if ch.get("not_found") else ""
            lines.append(f"  ✓ {ch['name']}: {int(price)}₽{nf}")
        if total:
            lines.append(f"\n💰 Итого: {int(total)}₽")
        await msg.answer("\n".join(lines), parse_mode="HTML")
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
        pending_set(msg.from_user.id, {
            "action": "checklist_items",
            "group": name,
            "user_notion_id": user_notion_id,
        })
        await msg.answer(f"📋 <b>{name}</b>\n\nОтправь пункты чеклиста — каждый на новой строке или через запятую.", parse_mode="HTML")
        return

    items = [{"name": it, "group": name} for it in items_raw if it]
    created = await add_items(items, "📋 Чеклист", BOT_NAME, user_notion_id)
    lines = [f"📋 <b>{name}</b> ({len(created)} пунктов)"]
    for c in created:
        lines.append(f"  ◻️ {c['name']}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── Unchanged text handlers ──────────────────────────────────────────────────

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
        "quantity": parsed.get("quantity") or 1,
        "note": parsed.get("note", ""),
        "category": parsed.get("category", "💳 Прочее"),
    }]
    created = await add_items(items, "📦 Инвентарь", BOT_NAME, user_notion_id)
    if created:
        c = created[0]
        await msg.answer(f"📦 <b>Инвентарь:</b> {c['name']} добавлен · {c.get('category', '')}", parse_mode="HTML")
    else:
        await msg.answer("⚠️ Не удалось добавить.")


async def handle_list_inv_search(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
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


async def handle_list_inv_update(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🗒️")
    text = data.get("text", msg.text or "")
    try:
        parsed = await _haiku_parse(text, _PARSE_INV_UPDATE_SYSTEM)
    except Exception as e:
        logger.error("handle_list_inv_update parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать.")
        return
    result = await inventory_update(parsed.get("item", ""), parsed.get("quantity") or 0, BOT_NAME, user_notion_id)
    if result.get("error") == "not_found":
        await msg.answer(f"❓ «{parsed.get('item', '')}» не найден в инвентаре.")
        return
    if result.get("suggest_buy"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Добавить в покупки", callback_data=f"list_to_buy_{result['updated'][:30]}"),
        ]])
        await msg.answer(f"📦 {result['updated']} — закончился, архивирован.", reply_markup=kb)
    else:
        await msg.answer(f"📦 {result['updated']}: осталось {parsed.get('quantity', 0)}")


# ── Pending state handler ─────────────────────────────────────────────────────

async def handle_list_pending(msg: Message, user_notion_id: str = "") -> bool:
    """Обработать pending state. Вызывается из handle_text ПЕРЕД classify()."""
    uid = msg.from_user.id
    pending = pending_get(uid)
    if not pending:
        return False

    action = pending.get("action")
    text = (msg.text or "").strip()

    # Мультиселект — игнорируем текст (работает через callbacks)
    if action in ("list_select", "list_remain_select"):
        return False

    if action == "checklist_items":
        await react(msg, "🗒️")
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
            lines.append(f"  ◻️ {c['name']}")
        await msg.answer("\n".join(lines), parse_mode="HTML")
        return True

    if action == "list_checkout":
        await react(msg, "💸")
        # Парсим текст чека через Haiku
        categories = pending.get("categories", {})
        selected_data = pending.get("selected", {})
        p_user_id = pending.get("user_notion_id", user_notion_id)

        try:
            parsed = await _haiku_parse(text, _checkout_parse_system(categories))
        except Exception as e:
            logger.error("list_checkout parse error: %s", e)
            await msg.answer("⚠️ Не смог разобрать. Попробуй: «2500 картой»")
            return True

        total = parsed.get("total") or 0
        source = parsed.get("source", "💳 Карта")
        breakdown = parsed.get("breakdown", [])

        # Если total не указан — сумма named
        named_sum = sum(b.get("amount") or 0 for b in breakdown)
        if not total:
            total = named_sum

        # Маппинг named категорий к полным именам
        named_cats: dict[str, float] = {}
        for b in breakdown:
            raw_cat = b.get("category", "")
            amount = b.get("amount") or 0
            # Найти полное имя категории
            matched = None
            for full_cat in categories:
                clean = full_cat.split(" ", 1)[-1].lower() if " " in full_cat else full_cat.lower()
                if clean in raw_cat.lower() or raw_cat.lower() in clean:
                    matched = full_cat
                    break
            if matched:
                named_cats[matched] = amount
            else:
                named_cats[raw_cat] = amount

        # Считаем остаток
        remaining_cats = [c for c in categories if c not in named_cats]
        named_total = sum(named_cats.values())
        remainder = total - named_total

        if len(remaining_cats) == 1 and remainder > 0:
            # Одна оставшаяся → получает остаток
            named_cats[remaining_cats[0]] = remainder
        elif len(remaining_cats) == 0 and abs(named_total - total) > 1:
            # Не сходится
            diff = total - named_total
            pending_del(uid)
            await msg.answer(f"⚠️ Не сходится на {int(abs(diff))}₽. Проверь суммы.")
            return True
        elif len(remaining_cats) > 1 and remainder > 0:
            # Несколько неназванных — спрашиваем по первой
            pending_del(uid)
            first_cat = remaining_cats[0]
            rest_cats = remaining_cats[1:]
            pending_set(uid, {
                "action": "list_checkout_split",
                "named_cats": named_cats,
                "remaining_cats": rest_cats,
                "remainder": remainder,
                "total": total,
                "source": source,
                "selected": selected_data,
                "categories": categories,
                "user_notion_id": p_user_id,
                "asking_cat": first_cat,
            })
            await msg.answer(
                f"Осталось {int(remainder)}₽ на {', '.join(remaining_cats)}.\n"
                f"Сколько на {first_cat}?",
                parse_mode="HTML",
            )
            return True
        elif not remaining_cats and not named_cats and total > 0:
            # Только total, одна категория
            if len(categories) == 1:
                cat = list(categories.keys())[0]
                named_cats[cat] = total

        # Всё посчитано — записываем
        pending_del(uid)
        await _finalize_checkout(msg, named_cats, source, selected_data, categories, p_user_id)
        return True

    if action == "list_checkout_split":
        await react(msg, "💸")
        # Ответ на "сколько на X?"
        price_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[кk]?", text)
        if not price_match:
            await msg.answer("⚠️ Напиши число, например: 800")
            return True
        amount = float(price_match.group(1).replace(",", "."))
        if "к" in text.lower() or "k" in text.lower():
            amount *= 1000

        asking_cat = pending.get("asking_cat", "")
        named_cats: dict[str, float] = pending.get("named_cats", {})
        named_cats[asking_cat] = amount

        remaining_cats: list[str] = pending.get("remaining_cats", [])
        remainder = pending.get("remainder") or 0 - amount

        if len(remaining_cats) == 1 and remainder > 0:
            # Последняя — получает остаток
            named_cats[remaining_cats[0]] = remainder
            pending_del(uid)
            await _finalize_checkout(
                msg, named_cats,
                pending.get("source", "💳 Карта"),
                pending.get("selected", {}),
                pending.get("categories", {}),
                pending.get("user_notion_id", user_notion_id),
            )
        elif remaining_cats:
            # Ещё остались — спрашиваем следующую
            next_cat = remaining_cats[0]
            rest = remaining_cats[1:]
            pending["named_cats"] = named_cats
            pending["remaining_cats"] = rest
            pending["remainder"] = remainder
            pending["asking_cat"] = next_cat
            pending_set(uid, pending)
            await msg.answer(f"Сколько на {next_cat}? (осталось {int(remainder)}₽)")
        else:
            pending_del(uid)
            await _finalize_checkout(
                msg, named_cats,
                pending.get("source", "💳 Карта"),
                pending.get("selected", {}),
                pending.get("categories", {}),
                pending.get("user_notion_id", user_notion_id),
            )
        return True

    if action == "inv_expiry":
        pending_del(uid)
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if date_match:
            from core.notion_client import update_page as up, _date
            item_id = pending.get("item_id", "")
            if item_id:
                await up(item_id, {"Срок годности": _date(date_match.group())})
                await msg.answer(f"📦 Срок годности: {date_match.group()}")
            return True
        await msg.answer("⚠️ Формат даты: YYYY-MM-DD")
        return True

    return False


# ── Finalize checkout ─────────────────────────────────────────────────────────

async def _finalize_checkout(
    msg: Message,
    named_cats: dict[str, float],
    source: str,
    selected_data: dict[str, dict],
    categories: dict[str, list[str]],
    user_page_id: str,
) -> None:
    """Записать в Финансы, отметить Done, показать лимиты, показать остатки."""
    from core.list_manager import _today_iso

    # 1. Записать в финансы по каждой категории
    lines = ["✅ <b>Чек записан:</b>"]
    finance_cats: list[dict] = []

    for cat, amount in named_cats.items():
        if amount <= 0:
            continue
        item_names = categories.get(cat, [])
        title = ", ".join(item_names) if item_names else cat
        finance_cat = CATEGORY_TO_FINANCE.get(cat, "💳 Прочее")

        fin_id = await finance_add(
            date=_today_iso(),
            amount=float(amount),
            category=finance_cat,
            type_="💸 Расход",
            source=source,
            description=title,
            bot_label=BOT_NAME,
            user_notion_id=user_page_id,
        )
        cat_emoji = cat.split(" ")[0] if " " in cat else ""
        lines.append(f"  {cat_emoji} {cat}: {int(amount)}₽ ({title})")
        finance_cats.append({"category": finance_cat, "amount": amount})

    # 2. Все выбранные → Done
    page_ids = list(selected_data.keys())
    if page_ids:
        await mark_items_done(page_ids)

    total = sum(named_cats.values())
    lines.append(f"\n💰 Итого: {int(total)}₽ · {source}")
    await msg.answer("\n".join(lines), parse_mode="HTML")

    # 3. Лимиты
    for fc in finance_cats:
        try:
            from nexus.handlers.finance import _check_budget_limit
            await _check_budget_limit(fc["category"], msg, user_page_id, amount=fc["amount"])
        except Exception as e:
            logger.error("checkout budget check: %s", e)

    # 4. Оставшиеся покупки
    remaining = await get_list("🛒 Покупки", BOT_NAME, user_page_id, status="Not started")
    if remaining:
        uid = msg.from_user.id
        r_lines = [f"\n🛒 <b>Осталось в списке ({len(remaining)}):</b>"]
        buttons: list[list[InlineKeyboardButton]] = []
        for it in remaining:
            cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
            r_lines.append(f"  ◻️ {it['name']} · {cat_emoji}")
            buttons.append([InlineKeyboardButton(
                text=it['name'],
                callback_data=f"lt_{it['id'][:28]}",
            )])
        buttons.append([
            InlineKeyboardButton(text="✅ Оставить всё", callback_data="lt_remain_keep"),
            InlineKeyboardButton(text="🗑️ Архивировать всё", callback_data="lt_remain_archive_all"),
        ])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        sent = await msg.answer("\n".join(r_lines), parse_mode="HTML", reply_markup=kb)
        pending_set(uid, {
            "action": "list_remain_select",
            "selected": [],
            "msg_id": sent.message_id,
            "user_notion_id": user_page_id,
            "list_type": "🛒 Покупки",
        })


# ── Callback: добавить в покупки из инвентаря ─────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_to_buy_"))
async def on_list_to_buy(query: CallbackQuery, user_notion_id: str = "") -> None:
    item_name = query.data.replace("list_to_buy_", "")
    created = await add_items([{"name": item_name}], "🛒 Покупки", BOT_NAME, user_notion_id)
    if created:
        await query.message.edit_text(f"🛒 «{item_name}» добавлен в покупки!")
    else:
        await query.answer("⚠️ Не удалось добавить.")
