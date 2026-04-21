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
    search_memory_categories, find_task_by_name,
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
    "- ЭНЕРГЕТИКИ → 🚬 Привычки: монстр/monster/ред булл/редбулл/burn/энергос/энерги\n"
    "- СИГАРЕТЫ → 🚬 Привычки: сигареты/сиги/табак/снюс/вейп/одноразки\n"
    "- КОТЫ/ЖИВОТНЫЕ → 🐾 Коты: корм/наполнитель/лоток/когтеточка/ветеринар\n"
    "- БЫТОВАЯ ХИМИЯ И ДОМ → 🏠 Жилье: туалетная бумага/салфетки/мыло/порошок/средство для мытья/губки/мусорные пакеты/лампочки/батарейки\n"
    "- ЕДА И НАПИТКИ → 🍜 Продукты: молоко/хлеб/яйца/мясо/кола/вода/сок/кофе/чай/лапша/булдак/доширак/рамён\n"
    "- ЗДОРОВЬЕ → 🏥 Здоровье: лекарства/таблетки/пластырь/витамины\n"
    "- КРАСОТА → 💅 Бьюти: шампунь/крем/маска/зубная паста\n"
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
        '- "остальное/прочее/остаток 4000" → category="остальное" (спец.слово, НЕ категория 💳 Прочее!)\n'
        "- В breakdown записывай ТОЛЬКО явно названные категории из контекста + остальное\n"
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


# ── Navigation render functions ───────────────────────────────────────────────

_BACK_BTN = InlineKeyboardButton(text="🔙 Назад", callback_data="list_nav_back")


def _items_by_type(all_items: list[dict]) -> dict[str, list[dict]]:
    by_type: dict[str, list[dict]] = {}
    for it in all_items:
        t = it.get("type", "🛒 Покупки")
        by_type.setdefault(t, []).append(it)
    return by_type


def render_overview(
    all_items: list[dict], header: str = HEADER,
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Обзорный экран: превью каждой категории + 3 кнопки навигации."""
    by_type = _items_by_type(all_items)

    lines = [f"<b>{header}</b>\n"]

    # Покупки
    buy = [it for it in by_type.get("🛒 Покупки", []) if it.get("status") != "Done"]
    if buy:
        preview = ", ".join(it["name"] for it in buy[:3])
        if len(buy) > 3:
            preview += " …"
        lines.append(f"🛒 Покупки ({len(buy)}) · {preview}")
    else:
        lines.append("🛒 Покупки (0)")

    # Чеклисты
    checks = by_type.get("📋 Чеклист", [])
    if checks:
        by_group: dict[str, list] = {}
        for it in checks:
            g = it.get("group", "") or "Без группы"
            by_group.setdefault(g, []).append(it)
        parts = []
        for gname, gitems in by_group.items():
            done = sum(1 for i in gitems if i.get("status") == "Done")
            parts.append(f"{gname} ({done}/{len(gitems)})")
        lines.append(f"📋 Чеклисты ({len(by_group)}) · {', '.join(parts)}")
    else:
        lines.append("📋 Чеклисты (0)")

    # Инвентарь
    inv = by_type.get("📦 Инвентарь", [])
    lines.append(f"📦 Инвентарь ({len(inv)})")

    buttons = [[
        InlineKeyboardButton(text="🛒 Покупки", callback_data="list_nav_buy"),
        InlineKeyboardButton(text="📋 Чеклисты", callback_data="list_nav_check"),
        InlineKeyboardButton(text="📦 Инвентарь", callback_data="list_nav_inv"),
    ]]
    return "\n".join(lines), buttons


def render_buy_screen(
    all_items: list[dict], selected: set[str],
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Экран покупок с мультиселектом."""
    by_type = _items_by_type(all_items)
    buy = [it for it in by_type.get("🛒 Покупки", []) if it.get("status") != "Done"]

    buttons: list[list[InlineKeyboardButton]] = []

    if not buy:
        text = "<b>🛒 Покупки</b>\n\nПусто. Напиши «купить ...» чтобы добавить."
        buttons.append([_BACK_BTN])
        return text, buttons

    lines = [f"<b>🛒 Покупки ({len(buy)})</b>\n"]
    for it in buy:
        cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
        is_sel = it["id"] in selected
        icon = "✅" if is_sel else "⬜"
        lines.append(f"  {icon} {it['name']} · {cat_emoji}")
        btn_text = f"✅ {it['name']}" if is_sel else it["name"]
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"lt_{it['id'][:28]}",
        )])

    bottom = []
    if selected:
        bottom.append(InlineKeyboardButton(text="✅ Чек", callback_data="lt_checkout"))
    bottom.append(_BACK_BTN)
    buttons.append(bottom)

    return "\n".join(lines), buttons


def render_check_screen(
    all_items: list[dict], selected: set[str],
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Экран чеклистов с мультиселектом."""
    by_type = _items_by_type(all_items)
    checks = by_type.get("📋 Чеклист", [])

    buttons: list[list[InlineKeyboardButton]] = []

    if not checks:
        text = "<b>📋 Чеклисты</b>\n\nПусто. Напиши «список: ...» чтобы создать."
        buttons.append([_BACK_BTN])
        return text, buttons

    by_group: dict[str, list] = {}
    for it in checks:
        g = it.get("group", "") or "Без группы"
        by_group.setdefault(g, []).append(it)

    lines = [f"<b>📋 Чеклисты</b>\n"]
    for gname, gitems in by_group.items():
        done = sum(1 for i in gitems if i.get("status") == "Done")
        lines.append(f"<b>📋 {gname}</b> ({done}/{len(gitems)})")
        for it in gitems:
            is_done = it.get("status") == "Done"
            is_sel = it["id"] in selected
            icon = "✅" if (is_done or is_sel) else "⬜"
            lines.append(f"  {icon} {it['name']}")
            if not is_done:
                btn_text = f"✅ {it['name']}" if is_sel else it["name"]
                buttons.append([InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"lt_{it['id'][:28]}",
                )])
        lines.append("")

    bottom = []
    if selected:
        bottom.append(InlineKeyboardButton(text="✅ Готово", callback_data="lt_checkout"))
    bottom.append(_BACK_BTN)
    buttons.append(bottom)

    return "\n".join(lines), buttons


def render_inv_screen(
    all_items: list[dict],
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Экран инвентаря (только просмотр)."""
    by_type = _items_by_type(all_items)
    inv = by_type.get("📦 Инвентарь", [])

    buttons: list[list[InlineKeyboardButton]] = []

    if not inv:
        text = "<b>📦 Инвентарь</b>\n\nПусто. Напиши «дома есть: ...» чтобы добавить."
        buttons.append([_BACK_BTN])
        return text, buttons

    lines = [f"<b>📦 Инвентарь ({len(inv)})</b>\n"]
    for it in inv:
        cat_emoji = (it.get("category", "").split(" ")[0]) if it.get("category") else ""
        qty = it.get("quantity") or 0
        parts = [it["name"]]
        if qty:
            parts.append(f"{int(qty)}шт")
        if it.get("note"):
            parts.append(it["note"])
        if it.get("expiry"):
            parts.append(f"до {it['expiry'][:10]}")
        lines.append(f"  {cat_emoji} {' · '.join(parts)}")

    buttons.append([_BACK_BTN])
    return "\n".join(lines), buttons


# ── /list command ─────────────────────────────────────────────────────────────

async def handle_list_command(msg: Message, user_notion_id: str = "") -> None:
    args = (msg.text or "").split(maxsplit=1)
    sub = args[1].strip().lower() if len(args) > 1 else ""
    screen_map = {"buy": "buy", "check": "check", "inv": "inv"}
    screen = screen_map.get(sub, "overview")

    all_items = await _fetch_all_display_items(None, BOT_NAME, user_notion_id)

    uid = msg.from_user.id
    pending_del(uid)

    sel: set[str] = set()
    if screen == "buy":
        text, buttons = render_buy_screen(all_items, sel)
    elif screen == "check":
        text, buttons = render_check_screen(all_items, sel)
    elif screen == "inv":
        text, buttons = render_inv_screen(all_items)
    else:
        if not all_items:
            await msg.answer("📭 Списки пусты.")
            return
        text, buttons = render_overview(all_items)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    sent = await msg.answer(text, parse_mode="HTML", reply_markup=kb)

    pending_set(uid, {
        "action": "list_select",
        "selected": [],
        "msg_id": sent.message_id,
        "user_notion_id": user_notion_id,
        "list_type": None,
        "screen": screen,
    })


# ── Callback: navigation (buy/check/inv/back) ─────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_nav_"))
async def on_list_nav(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("action") not in ("list_select",):
        await query.answer("⏰ Сессия истекла. Отправь /list заново.")
        return

    p_user_id = pending.get("user_notion_id", user_notion_id)
    all_items = await _fetch_all_display_items(None, BOT_NAME, p_user_id)

    nav = query.data.replace("list_nav_", "")  # buy / check / inv / back
    screen = nav if nav != "back" else "overview"

    # При навигации сбрасываем выбранное
    if nav == "back":
        pending["selected"] = []

    sel = set(pending.get("selected", []))

    if screen == "buy":
        text, buttons = render_buy_screen(all_items, sel)
    elif screen == "check":
        text, buttons = render_check_screen(all_items, sel)
    elif screen == "inv":
        text, buttons = render_inv_screen(all_items)
    else:
        text, buttons = render_overview(all_items)

    pending["screen"] = screen
    pending_set(uid, pending)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await query.answer()


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
    sel_set = set(selected)

    # Rebuild по экрану
    if is_remain:
        text, buttons = _build_list_text_and_buttons(all_items, sel_set, HEADER)
        buttons = [b for b in buttons if b[0].callback_data != "lt_checkout"]
        bottom = []
        if selected:
            bottom.append([InlineKeyboardButton(text="🗑️ Архивировать выбранное", callback_data="lt_remain_archive")])
        bottom.append([
            InlineKeyboardButton(text="✅ Оставить всё", callback_data="lt_remain_keep"),
            InlineKeyboardButton(text="🗑️ Архивировать всё", callback_data="lt_remain_archive_all"),
        ])
        buttons.extend(bottom)
    else:
        screen = pending.get("screen", "overview")
        if screen == "buy":
            text, buttons = render_buy_screen(all_items, sel_set)
        elif screen == "check":
            text, buttons = render_check_screen(all_items, sel_set)
        else:
            text, buttons = _build_list_text_and_buttons(all_items, sel_set, HEADER)

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
            lines.append(f"🎉 Все подзадачи «{g}» готовы!")
        await query.message.answer("\n".join(lines), parse_mode="HTML")

        # Если завершённая группа привязана к задаче → предложить завершить
        for g in groups_done:
            task_rel = ""
            for pid in check_ids:
                it = items_map.get(pid)
                if it and it.get("group") == g and it.get("task_rel"):
                    task_rel = it["task_rel"]
                    break
            if task_rel:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Завершить задачу", callback_data=f"list_complete_task_{task_rel[:28]}"),
                    InlineKeyboardButton(text="Оставить открытой", callback_data="list_keep_task"),
                ]])
                await query.message.answer(
                    f"🎯 Завершить задачу «{g}»?",
                    reply_markup=kb,
                )

    # Покупки — нужна цена
    if buy_items:
        # Группируем по категории
        categories: dict[str, list[str]] = {}
        selected_data: dict[str, dict] = {}
        for pid, it in buy_items.items():
            cat = it.get("category", "💳 Прочее")
            categories.setdefault(cat, []).append(it["name"])
            selected_data[pid] = {"name": it["name"], "category": cat, "recurring": it.get("recurring", False)}

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
            lines.append(f"  {cat}: {', '.join(names)}")
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
    await react(msg, "🫡")
    text = data.get("text", msg.text or "")

    # Ищем предпочтения в Памяти для определения категорий
    # Извлекаем потенциальные названия из текста (убираем "купить/купи/добавь")
    clean = re.sub(r"^\s*(купить|купи|добавь в (?:покупки|список)|надо купить|нужно купить)\s*", "", text, flags=re.IGNORECASE).strip()
    potential_items = [w.strip() for w in re.split(r"[,\s]+и\s+|,\s*", clean) if w.strip()]

    memory_cats: dict[str, str] = {}
    if potential_items:
        try:
            memory_cats = await search_memory_categories(potential_items)
        except Exception as e:
            logger.debug("memory category search: %s", e)

    # Дополняем промпт маппингами из Памяти
    system = _PARSE_BUY_SYSTEM
    if memory_cats:
        mem_lines = "\n".join(f"- {name} → {cat}" for name, cat in memory_cats.items())
        system = system + f"\nИзвестные предпочтения из памяти (ПРИОРИТЕТ):\n{mem_lines}\n"

    try:
        parsed = await _haiku_parse(text, system)
        if not isinstance(parsed, list):
            parsed = [parsed]
    except Exception as e:
        logger.error("handle_list_buy parse error: %s", e)
        await msg.answer("⚠️ Не смог разобрать список. Попробуй: «купить молоко, яйца»")
        return

    _ARCANA_CATS = {"🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды"}
    all_parsed = [{"name": p.get("name", ""), "category": p.get("category", "💳 Прочее")} for p in parsed if p.get("name")]
    if not all_parsed:
        await msg.answer("⚠️ Не нашёл айтемов.")
        return

    # Разделить: Nexus vs Arcana
    nexus_items = [it for it in all_parsed if it["category"] not in _ARCANA_CATS]
    arcana_items = [it for it in all_parsed if it["category"] in _ARCANA_CATS]

    lines: list[str] = []
    if nexus_items:
        # Проверить дубли: уже есть в покупках (Not started)?
        existing = await get_list(list_type="🛒 Покупки", bot_name=BOT_NAME,
                                  user_page_id=user_notion_id, status="Not started")
        existing_names = {it["name"].lower() for it in existing}
        new_items = [it for it in nexus_items if it["name"].lower() not in existing_names]
        dupes = [it for it in nexus_items if it["name"].lower() in existing_names]

        if new_items:
            created = await add_items(new_items, "🛒 Покупки", BOT_NAME, user_notion_id)
            lines.append("🛒 <b>Добавлено в покупки:</b>")
            for c in created:
                cat_emoji = c.get("category", "").split(" ")[0] if c.get("category") else ""
                lines.append(f"  ⬜ {c['name']} · {cat_emoji}")
        if dupes:
            dupe_names = ", ".join(it["name"] for it in dupes)
            lines.append(f"ℹ️ Уже в списке: {dupe_names}")
        if not new_items and not dupes:
            lines.append("⚠️ Нечего добавлять.")
    if arcana_items:
        names = ", ".join(it["name"] for it in arcana_items)
        lines.append(f"\n🌒 {names} — это к Аркане! Напиши @arcana_kailark_bot")
    if not nexus_items and arcana_items:
        lines = [f"🌒 Это к Аркане! Напиши @arcana_kailark_bot"]
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ── list_done handler (text "купила X 89р") ───────────────────────────────────

async def handle_list_done(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "🏆")
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
    await react(msg, "🫡")
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


# ── Subtask: разбивка задачи на чеклист с привязкой ─────────────────────────

_PARSE_SUBTASK_SYSTEM = (
    "Пользователь хочет разбить задачу на подзадачи. "
    "Извлеки название задачи. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"task_name": "ремонт в ванной"}\n'
    "- Убери служебные слова (разбей задачу, на подзадачи, и т.д.)\n"
    "- Оставь только суть задачи"
)


async def handle_list_subtask(msg: Message, data: dict, user_notion_id: str = "") -> None:
    """Разбить задачу на подзадачи (чеклист с Relation к задаче)."""
    await react(msg, "🫡")
    text = data.get("text", msg.text or "")

    # Извлечь название задачи
    try:
        parsed = await _haiku_parse(text, _PARSE_SUBTASK_SYSTEM)
        task_query = parsed.get("task_name", "").strip()
    except Exception:
        task_query = re.sub(
            r"разб(?:ей|ить)\s+(?:задачу|работу)\s*|\s*на\s+подзадачи\s*",
            "", text, flags=re.IGNORECASE
        ).strip()

    if not task_query:
        await msg.answer("⚠️ Не понял какую задачу разбить. Напиши: «разбей задачу X на подзадачи»")
        return

    # Поиск задачи
    tasks = await find_task_by_name(task_query, user_notion_id)
    uid = msg.from_user.id

    if not tasks:
        # Не нашли → предложить чеклист без привязки
        pending_set(uid, {
            "action": "subtask_items",
            "task_id": "",
            "task_name": task_query,
            "user_notion_id": user_notion_id,
        })
        await msg.answer(
            f"❓ Задача «{task_query}» не найдена.\n"
            f"Создам чеклист без привязки к задаче.\n\n"
            f"📋 <b>{task_query}</b>\nНапиши пункты (каждый с новой строки или через запятую):",
            parse_mode="HTML",
        )
        return

    if len(tasks) == 1:
        # Единственный результат → pending
        t = tasks[0]
        pending_set(uid, {
            "action": "subtask_items",
            "task_id": t["id"],
            "task_name": t["name"],
            "user_notion_id": user_notion_id,
        })
        await msg.answer(
            f"📋 Разбиваю «{t['name']}» на подзадачи\n"
            f"Напиши пункты (каждый с новой строки или через запятую):",
            parse_mode="HTML",
        )
        return

    # Несколько результатов → кнопки выбора
    buttons = []
    for t in tasks[:5]:
        short = t["name"][:30] + ("…" if len(t["name"]) > 30 else "")
        buttons.append([InlineKeyboardButton(
            text=f"📋 {short}",
            callback_data=f"subtask_pick_{t['id'][:28]}",
        )])
    from core.utils import cancel_button
    buttons.append([cancel_button("❌ Отмена", "subtask_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Сохраняем все задачи для lookup по id-prefix
    pending_set(uid, {
        "action": "subtask_pick",
        "tasks": [{"id": t["id"], "name": t["name"]} for t in tasks[:5]],
        "user_notion_id": user_notion_id,
    })
    await msg.answer("Нашёл несколько задач. Какую разбить?", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("subtask_pick_"))
async def on_subtask_pick(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if not pending or pending.get("action") != "subtask_pick":
        await query.answer("⏰ Сессия истекла.")
        return

    id_prefix = query.data.replace("subtask_pick_", "")
    tasks = pending.get("tasks", [])
    matched = None
    for t in tasks:
        if t["id"].startswith(id_prefix):
            matched = t
            break
    if not matched:
        await query.answer("❓ Задача не найдена.")
        return

    p_user_id = pending.get("user_notion_id", user_notion_id)
    pending_del(uid)
    pending_set(uid, {
        "action": "subtask_items",
        "task_id": matched["id"],
        "task_name": matched["name"],
        "user_notion_id": p_user_id,
    })
    await query.message.edit_text(
        f"📋 Разбиваю «{matched['name']}» на подзадачи\n"
        f"Напиши пункты (каждый с новой строки или через запятую):",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(lambda c: c.data == "subtask_cancel")
async def on_subtask_cancel(query: CallbackQuery, user_notion_id: str = "") -> None:
    pending_del(query.from_user.id)
    try:
        await query.message.edit_text("❌ Отменено.")
    except Exception:
        pass
    await query.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("list_complete_task_"))
async def on_complete_task(query: CallbackQuery, user_notion_id: str = "") -> None:
    """Завершить задачу после автозавершения всех подзадач."""
    id_prefix = query.data.replace("list_complete_task_", "")
    from core.notion_client import update_task_status
    # Найти полный ID
    tasks = await find_task_by_name("", user_notion_id)
    full_id = ""
    for t in tasks:
        if t["id"].startswith(id_prefix):
            full_id = t["id"]
            break
    if not full_id:
        # fallback: use prefix as-is (may work for short UUIDs)
        full_id = id_prefix
    result = await update_task_status(full_id, "Done")
    if result:
        try:
            await query.message.edit_reply_markup()
        except Exception:
            pass
        await query.answer("✅ Задача завершена!")
        await query.message.reply("✅ Задача отмечена как выполненная!")
    else:
        await query.answer("⚠️ Не удалось обновить.", show_alert=True)


@router.callback_query(lambda c: c.data == "list_keep_task")
async def on_keep_task(query: CallbackQuery, user_notion_id: str = "") -> None:
    try:
        await query.message.edit_reply_markup()
    except Exception:
        pass
    await query.answer("👌 Задача остаётся открытой")


# ── Unchanged text handlers ──────────────────────────────────────────────────

async def handle_list_checklist_toggle(msg: Message, data: dict, user_notion_id: str = "") -> None:
    await react(msg, "⚡")
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
    await react(msg, "🫡")
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
        # Спросить срок годности
        uid = msg.from_user.id
        pending_set(uid, {
            "action": "inv_expiry",
            "item_id": c["id"],
            "item_name": c["name"],
            "user_notion_id": user_notion_id,
        })
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭️ Пропустить", callback_data="list_skip_expiry"),
        ]])
        await msg.answer(
            "📅 Срок годности? (напиши дату, например <code>2026-06-15</code>)",
            parse_mode="HTML", reply_markup=kb,
        )
    else:
        await msg.answer("⚠️ Не удалось добавить.")


async def handle_list_inv_search(msg: Message, data: dict, user_notion_id: str = "") -> None:
    text = data.get("text", msg.text or "")
    query = re.sub(r"(?:дома\s+)?есть\s*(?:ли)?\s*(?:у меня)?\s*(?:дома)?\s*", "", text, flags=re.IGNORECASE).strip().rstrip("?")
    results = await inventory_search(query, BOT_NAME, user_notion_id)
    if not results:
        # Проверить: уже в покупках?
        existing_buy = await get_list(list_type="🛒 Покупки", bot_name=BOT_NAME,
                                      user_page_id=user_notion_id, status="Not started")
        already = any(query.lower() in it["name"].lower() for it in existing_buy)
        if already:
            await msg.answer(f"📦 «{query}» нет в инвентаре. Уже в списке покупок ✅")
        else:
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
    await react(msg, "🫡")
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
        await react(msg, "🫡")
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

    if action == "subtask_items":
        await react(msg, "🫡")
        pending_del(uid)
        raw_items = []
        for line in text.split("\n"):
            for part in line.split(","):
                part = part.strip().lstrip("•·-–— ").strip()
                if part:
                    raw_items.append(part)
        task_id = pending.get("task_id", "")
        task_name = pending.get("task_name", "Подзадачи")
        rel_type = pending.get("rel_type", "task")
        p_user_id = pending.get("user_notion_id", user_notion_id)
        if task_id:
            rel_key = "work_rel" if rel_type == "work" else "task_rel"
            items = [{"name": it, "group": task_name, rel_key: task_id} for it in raw_items]
        else:
            items = [{"name": it, "group": task_name} for it in raw_items]
        created = await add_items(items, "📋 Чеклист", BOT_NAME, p_user_id)
        lines = [f"📋 <b>{task_name}</b> — {len(created)} подзадач:"]
        for c in created:
            lines.append(f"  ⬜ {c['name']}")
        await msg.answer("\n".join(lines), parse_mode="HTML")
        return True

    if action == "list_checkout":
        # Если в тексте нет цифр — это не ответ на чек, сбросить pending
        if not re.search(r"\d", text):
            pending_del(uid)
            return False
        await react(msg, "🏆")
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
        # "остальное/прочее/остаток" — спец.слова, не реальная категория
        _REST_WORDS = {"остальное", "прочее", "остаток", "rest", "другое"}
        named_cats: dict[str, float] = {}
        rest_amount: float = 0
        for b in breakdown:
            raw_cat = b.get("category", "")
            amount = b.get("amount") or 0
            # Проверяем: это слово "остальное"?
            if raw_cat.lower().strip() in _REST_WORDS:
                rest_amount += amount
                continue
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

        # Считаем остаток (явный total минус названные + "остальное" как нераспределённое)
        remaining_cats = [c for c in categories if c not in named_cats]
        named_total = sum(named_cats.values())
        # rest_amount = то что Haiku вернул как "остальное/прочее"
        # remainder = total - named_total - уже включает rest_amount если total указан
        if total:
            remainder = total - named_total
        else:
            remainder = rest_amount

        if len(remaining_cats) == 1 and remainder > 0:
            # Одна оставшаяся → получает остаток
            named_cats[remaining_cats[0]] = remainder
        elif len(remaining_cats) == 0 and rest_amount > 0:
            # Все категории уже названы, но есть "остальное" — не сходится, спросить
            pending_del(uid)
            await msg.answer(f"⚠️ Все категории уже указаны, но осталось {int(rest_amount)}₽. Куда записать?")
            return True
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
        if not re.search(r"\d", text):
            pending_del(uid)
            return False
        await react(msg, "🏆")
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
        skip_words = {"пропустить", "пропусти", "нет", "—", "-", "skip", "не надо", "без"}
        if text.lower().strip() in skip_words:
            pending_del(uid)
            await msg.answer("📦 Ок, без срока годности.")
            return True
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if date_match:
            pending_del(uid)
            from core.notion_client import update_page as up, _date
            item_id = pending.get("item_id", "")
            if item_id:
                await up(item_id, {"Срок годности": _date(date_match.group())})
                item_name = pending.get("item_name", "")
                await msg.answer(f"📦 {item_name} — срок годности: {date_match.group()}")
            return True
        pending_del(uid)
        await msg.answer("⚠️ Не распознал дату. Формат: <code>YYYY-MM-DD</code>. Пропускаю.", parse_mode="HTML")
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
        lines.append(f"  {cat}: {int(amount)}₽ ({title})")
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

    # 4. Предложить задачу-напоминание / инвентарь (макс 1+1, не для крупных чеков)
    _REMIND_CATS = {"🐾 Коты", "🏥 Здоровье", "🍜 Продукты"}
    _INV_CATS = {"🏥 Здоровье"}
    all_items_flat = list(selected_data.values())
    if len(all_items_flat) <= 4:
        # --- Задача-напоминание ---
        remind_candidate = None
        for it in all_items_flat:
            if it.get("recurring"):
                continue  # клонируется cron-ом
            if it.get("category") in _REMIND_CATS:
                remind_candidate = it
                break
        if remind_candidate:
            import hashlib
            name_hash = hashlib.md5(remind_candidate["name"].encode()).hexdigest()[:8]
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="7 дн", callback_data=f"list_remind_7_{name_hash}"),
                InlineKeyboardButton(text="14 дн", callback_data=f"list_remind_14_{name_hash}"),
                InlineKeyboardButton(text="30 дн", callback_data=f"list_remind_30_{name_hash}"),
                InlineKeyboardButton(text="Нет", callback_data="list_remind_no"),
            ]])
            # Сохраняем name для callback
            pending_set(msg.from_user.id, {
                "action": "list_remind_meta",
                "item_name": remind_candidate["name"],
                "category": remind_candidate["category"],
                "user_notion_id": user_page_id,
                "name_hash": name_hash,
            })
            await msg.answer(
                f"⏰ Напомнить купить «{remind_candidate['name']}» снова?",
                reply_markup=kb,
            )

        # --- Инвентарь ---
        inv_candidate = None
        for it in all_items_flat:
            if it.get("category") in _INV_CATS:
                inv_candidate = it
                break
        if inv_candidate:
            # Проверить: был ли в инвентаре (Archived)?
            try:
                inv_results = await inventory_search(inv_candidate["name"], BOT_NAME, user_page_id)
            except Exception:
                inv_results = []
            if not inv_results:
                import hashlib
                nh = hashlib.md5(inv_candidate["name"].encode()).hexdigest()[:8]
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=f"📦 {inv_candidate['name']}", callback_data=f"list_to_inv_{nh}"),
                    InlineKeyboardButton(text="Нет", callback_data="list_to_inv_no"),
                ]])
                # Сохраняем мета для callback
                uid = msg.from_user.id
                old_pending = pending_get(uid)
                if not old_pending or old_pending.get("action") != "list_remind_meta":
                    pending_set(uid, {
                        "action": "list_inv_meta",
                        "item_name": inv_candidate["name"],
                        "category": inv_candidate["category"],
                        "user_notion_id": user_page_id,
                        "name_hash": nh,
                    })
                else:
                    # Добавляем к existing pending
                    old_pending["inv_name"] = inv_candidate["name"]
                    old_pending["inv_category"] = inv_candidate["category"]
                    old_pending["inv_hash"] = nh
                    pending_set(uid, old_pending)
                await msg.answer(
                    f"📦 Добавить в инвентарь?",
                    reply_markup=kb,
                )

    # 5. Оставшиеся покупки
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


# ── Callback: напомнить купить снова ─────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_remind_") and c.data != "list_remind_no")
async def on_list_remind(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if not pending:
        await query.answer("⏰ Сессия истекла.")
        return
    # Извлечь дни из callback: list_remind_7_abc12345
    parts = query.data.split("_")  # ["list", "remind", "7", "abc12345"]
    days = int(parts[2]) if len(parts) >= 4 else 14
    item_name = pending.get("item_name", "")
    category = pending.get("category", "")
    p_user_id = pending.get("user_notion_id", user_notion_id)

    # Создать задачу-напоминание
    from datetime import date, timedelta
    from core.notion_client import page_create, _title, _select, _status, _date
    from core.config import config as app_config
    from nexus.handlers.tasks import _date_with_tz
    deadline = (date.today() + timedelta(days=days)).isoformat()
    reminder = (date.today() + timedelta(days=max(days - 1, 1))).isoformat()
    try:
        db_tasks = app_config.nexus.db_tasks
        props = {
            "Задача": _title(f"Купить {item_name}"),
            "Статус": _status("Not started"),
            "Дедлайн": _date(deadline),
            "Напоминание": _date_with_tz(reminder + "T10:00", 3),
            "Приоритет": _select("Важно"),
        }
        if category:
            props["Категория"] = _select(category)
        if p_user_id:
            from core.notion_client import _relation
            props["🪪 Пользователи"] = _relation(p_user_id)
        await page_create(db_tasks, props)
    except Exception as e:
        logger.error("on_list_remind create task: %s", e)
        await query.answer("⚠️ Не удалось создать напоминание.", show_alert=True)
        return

    # Очистить только remind-мету из pending (оставить inv-мету если есть)
    if pending.get("action") == "list_remind_meta":
        if pending.get("inv_name"):
            pending["action"] = "list_inv_meta"
            pending_set(uid, pending)
        else:
            pending_del(uid)

    try:
        await query.message.edit_text(f"⏰ Напоминание: купить «{item_name}» через {days} дн.")
    except Exception:
        pass
    await query.answer("✅ Напоминание создано!")


@router.callback_query(lambda c: c.data == "list_remind_no")
async def on_list_remind_no(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if pending and pending.get("action") == "list_remind_meta":
        if pending.get("inv_name"):
            pending["action"] = "list_inv_meta"
            pending_set(uid, pending)
        else:
            pending_del(uid)
    try:
        await query.message.edit_reply_markup()
    except Exception:
        pass
    await query.answer()


# ── Callback: добавить в инвентарь после чека ────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_to_inv_") and c.data != "list_to_inv_no")
async def on_list_to_inv(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    item_name = ""
    category = ""
    p_user_id = user_notion_id
    if pending:
        item_name = pending.get("inv_name") or pending.get("item_name", "")
        category = pending.get("inv_category") or pending.get("category", "")
        p_user_id = pending.get("user_notion_id", user_notion_id)
        pending_del(uid)
    if not item_name:
        await query.answer("⏰ Сессия истекла.")
        return

    created = await add_items(
        [{"name": item_name, "category": category, "quantity": 1}],
        "📦 Инвентарь", BOT_NAME, p_user_id,
    )
    if created:
        c = created[0]
        pending_set(uid, {
            "action": "inv_expiry",
            "item_id": c["id"],
            "item_name": c["name"],
            "user_notion_id": p_user_id,
        })
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭️ Пропустить", callback_data="list_skip_expiry"),
        ]])
        try:
            await query.message.edit_text(f"📦 {item_name} добавлен в инвентарь!")
        except Exception:
            pass
        await query.message.answer(
            "📅 Срок годности?",
            reply_markup=kb,
        )
    else:
        await query.answer("⚠️ Не удалось добавить.")
    await query.answer()


@router.callback_query(lambda c: c.data == "list_to_inv_no")
async def on_list_to_inv_no(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending = pending_get(uid)
    if pending and pending.get("action") in ("list_inv_meta", "list_remind_meta"):
        pending_del(uid)
    try:
        await query.message.edit_reply_markup()
    except Exception:
        pass
    await query.answer()


# ── Callback: добавить в покупки из инвентаря ─────────────────────────────────

@router.callback_query(lambda c: c.data == "list_skip_expiry")
async def on_skip_expiry(query: CallbackQuery, user_notion_id: str = "") -> None:
    uid = query.from_user.id
    pending_del(uid)
    try:
        await query.message.edit_text("📦 Готово, без срока годности.")
    except Exception:
        pass
    await query.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("list_to_buy_"))
async def on_list_to_buy(query: CallbackQuery, user_notion_id: str = "") -> None:
    item_name = query.data.replace("list_to_buy_", "")
    # Попробовать взять категорию из инвентаря
    category = "💳 Прочее"
    try:
        inv_results = await inventory_search(item_name, BOT_NAME, user_notion_id)
        if inv_results and inv_results[0].get("category"):
            category = inv_results[0]["category"]
    except Exception:
        pass
    created = await add_items([{"name": item_name, "category": category}], "🛒 Покупки", BOT_NAME, user_notion_id)
    if created:
        cat_emoji = category.split(" ")[0] if " " in category else ""
        await query.message.edit_text(f"🛒 «{item_name}» добавлен в покупки! {cat_emoji}")
    else:
        await query.answer("⚠️ Не удалось добавить.")


# ── Callback: вычеркнуть из списка после записи расхода ──────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("list_cross_") and c.data != "list_cross_no")
async def on_list_cross(query: CallbackQuery, user_notion_id: str = "") -> None:
    page_id_short = query.data.replace("list_cross_", "")
    # Найти полный page_id
    from core.list_manager import get_list
    items = await get_list("🛒 Покупки", BOT_NAME, user_notion_id, status="Not started")
    full_id = None
    item_name = ""
    for it in items:
        if it["id"].startswith(page_id_short):
            full_id = it["id"]
            item_name = it.get("name", "")
            break

    if not full_id:
        await query.answer("❓ Уже вычеркнуто или не найдено.")
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # Только статус → Done, БЕЗ записи в финансы (уже записан)
    await update_page(full_id, {"Статус": _status("Done")})
    await query.answer(f"✅ {item_name} вычеркнуто")
    try:
        await query.message.edit_text(f"🛒 ✅ {item_name} — вычеркнуто из списка", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(lambda c: c.data == "list_cross_no")
async def on_list_cross_no(query: CallbackQuery, user_notion_id: str = "") -> None:
    await query.answer("👌")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
