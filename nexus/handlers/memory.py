"""nexus/handlers/memory.py — тонкий слой Nexus (вся логика в core/memory.py)."""
from __future__ import annotations

import logging
import os
from typing import Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

import core.memory as mem
from core.claude_client import ask_claude
from core.notion_client import page_create

logger = logging.getLogger("nexus.memory")
router = Router()

BOT_LABEL = "☀️ Nexus"

CATEGORIES_ORDER = [
    "🧠 СДВГ", "👥 Люди", "🏥 Здоровье", "🛒 Предпочтения",
    "💼 Работа", "🏠 Быт", "🔄 Паттерн", "💡 Инсайт",
    "🔮 Практика", "🐾 Коты", "💰 Лимит",
]

CAT_MAP = {
    "сдвг": "🧠 СДВГ",
    "люди": "👥 Люди",
    "здоровье": "🏥 Здоровье",
    "предпочтения": "🛒 Предпочтения",
    "работа": "💼 Работа",
    "быт": "🏠 Быт",
    "паттерн": "🔄 Паттерн",
    "инсайт": "💡 Инсайт",
    "практика": "🔮 Практика",
    "коты": "🐾 Коты",
    "лимит": "💰 Лимит",
}


async def handle_memory_list(
    message: Message,
    category_filter: str = "",
    user_notion_id: str = "",
) -> None:
    """/memory — все активные записи, сгруппированные по категориям."""
    from core.notion_client import db_query
    from core.pagination import PAGE_SIZE, register_pages, get_page_text, get_page_keyboard

    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    filters = [{"property": "Актуально", "checkbox": {"equals": True}}]

    matched_cat = ""
    if category_filter:
        key = category_filter.lower().strip()
        matched_cat = CAT_MAP.get(key, "")
        if not matched_cat:
            for k, v in CAT_MAP.items():
                if key in k or k in key:
                    matched_cat = v
                    break
        if matched_cat:
            filters.append({"property": "Категория", "select": {"equals": matched_cat}})

    filter_obj = {"and": filters} if len(filters) > 1 else filters[0]

    try:
        pages = await db_query(db_id, filter_obj=filter_obj, page_size=200)
    except Exception as e:
        logger.error("handle_memory_list: %s", e)
        await message.answer("⚠️ Ошибка загрузки памяти")
        return

    if not pages:
        msg_text = f"🧠 В категории {matched_cat} пусто." if matched_cat else "🧠 Память пуста."
        await message.answer(msg_text)
        return

    # Группируем по категории
    grouped: dict = {}
    all_items = []
    for p in pages:
        props = p["properties"]
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "❓ Без категории")
        text_parts = props.get("Текст", {}).get("title", [])
        text = text_parts[0]["plain_text"] if text_parts else "—"
        grouped.setdefault(cat, []).append(text)
        all_items.append({"cat": cat, "text": text})

    lines = [f"🧠 <b>Память</b> ({len(pages)} зап.)\n"]
    for cat in CATEGORIES_ORDER:
        if cat in grouped:
            lines.append(f"<b>{cat}</b>")
            for item in grouped[cat]:
                lines.append(f"  • {item}")
            lines.append("")
    for cat, items in grouped.items():
        if cat not in CATEGORIES_ORDER:
            lines.append(f"<b>{cat}</b>")
            for item in items:
                lines.append(f"  • {item}")
            lines.append("")

    uid = message.from_user.id
    if len(pages) > PAGE_SIZE:
        def _fmt(item: dict) -> str:
            return f"{item['cat']} · {item['text']}"
        title = f"🧠 Память{' · ' + matched_cat if matched_cat else ''}"
        register_pages(uid, all_items, title, _fmt)
        await message.answer("\n".join(lines), parse_mode="HTML")
        await message.answer(get_page_text(uid), reply_markup=get_page_keyboard(uid))
    else:
        await message.answer("\n".join(lines), parse_mode="HTML")

_ADHD_SUMMARY_SYSTEM = """Пишешь о человеке по имени Кай (женский род). Используй женский род во всех глаголах и прилагательных.
На основе списка фактов о человеке с СДВГ
напиши личный профиль — 3-4 предложения.
Конкретно, без банальщины, как будто описываешь именно этого человека.
Фокус: что это значит для его повседневной жизни.
Только текст, без заголовков."""


async def handle_adhd_command(message: Message, user_notion_id: str = "") -> None:
    """/adhd — личный СДВГ-профиль с группировкой и саммари от Sonnet."""
    from core.notion_client import db_query
    from core.pagination import PAGE_SIZE, register_pages

    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return
    try:
        pages = await db_query(db_id, filter_obj={"and": [
            {"property": "Категория", "select": {"equals": "🧠 СДВГ"}},
            {"property": "Актуально", "checkbox": {"equals": True}},
        ]}, page_size=100)
    except Exception as e:
        logger.error("handle_adhd_command: %s", e)
        await message.answer("⚠️ Ошибка загрузки")
        return

    if not pages:
        await message.answer("🧠 Пока нет фактов о СДВГ в памяти.")
        return

    PATTERN_KW = ("забыва", "теря", "откладыва", "прокрастин", "кладёт",
                  "громко", "быстро говор", "утро начинается", "сова",
                  "не существует", "неосознанно", "гиперфокус")
    STRATEGY_KW = ("помогают", "помогает", "стратеги", "витамин", "кольц",
                   "будильник", "список", "порядок", "структур", "Monster", "Chapman")
    TRIGGER_KW = ("мешает", "триггер", "хуже", "шум", "раздраж",
                  "плохой сон", "не может найти", "не на виду", "не могу")

    groups = {"🔄 Паттерны": [], "💡 Стратегии": [], "⚡ Триггеры": [], "📌 Особенности": []}
    all_facts = []
    for p in pages:
        parts = p["properties"].get("Текст", {}).get("title", [])
        fact = parts[0]["plain_text"] if parts else "—"
        all_facts.append(fact)
        fact_lower = fact.lower()
        if any(kw in fact_lower for kw in PATTERN_KW):
            groups["🔄 Паттерны"].append(fact)
        elif any(kw in fact_lower for kw in STRATEGY_KW):
            groups["💡 Стратегии"].append(fact)
        elif any(kw in fact_lower for kw in TRIGGER_KW):
            groups["⚡ Триггеры"].append(fact)
        else:
            groups["📌 Особенности"].append(fact)

    # Sonnet генерирует личный профиль
    facts_text = "\n".join(f"• {f}" for f in all_facts)
    try:
        summary = await ask_claude(
            facts_text,
            system=_ADHD_SUMMARY_SYSTEM,
            max_tokens=200,
            model="claude-sonnet-4-6",
        )
    except Exception:
        summary = ""

    lines = ["🧠 <b>СДВГ — твой профиль</b>"]
    if summary:
        lines.append("")
        lines.append(summary.strip())
    lines.append("")
    for group_name in ["🔄 Паттерны", "💡 Стратегии", "⚡ Триггеры", "📌 Особенности"]:
        items = groups.get(group_name, [])
        if items:
            lines.append(f"<b>{group_name} ({len(items)}):</b>")
            for item in items:
                lines.append(f"  • {item}")
            lines.append("")

    uid = message.from_user.id
    flat_items = []
    for group_name in ["🔄 Паттерны", "💡 Стратегии", "⚡ Триггеры", "📌 Особенности"]:
        for item in groups.get(group_name, []):
            flat_items.append({"group": group_name, "text": item})

    if len(flat_items) > PAGE_SIZE:
        from core.pagination import get_page_text, get_page_keyboard
        def _fmt(it: dict) -> str:
            return f"{it['group']} · {it['text']}"
        register_pages(uid, flat_items, "🧠 СДВГ — профиль", _fmt)
        await message.answer("\n".join(lines), parse_mode="HTML")
        await message.answer(get_page_text(uid), reply_markup=get_page_keyboard(uid))
    else:
        await message.answer("\n".join(lines), parse_mode="HTML")


async def send_adhd_digest(bot) -> None:
    """Еженедельно напоминает 2 случайных факта из категории 🧠 СДВГ."""
    import random
    from core.notion_client import db_query

    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        return
    try:
        pages = await db_query(db_id, filter_obj={"and": [
            {"property": "Категория", "select": {"equals": "🧠 СДВГ"}},
            {"property": "Актуально", "checkbox": {"equals": True}},
        ]}, page_size=100)
    except Exception as e:
        logger.error("send_adhd_digest: %s", e)
        return
    if not pages:
        return
    picked = random.sample(pages, min(2, len(pages)))
    facts = []
    for p in picked:
        parts = p["properties"].get("Текст", {}).get("title", [])
        facts.append(parts[0]["plain_text"] if parts else "—")
    text = "🧠 <b>Напоминание о себе:</b>\n\n" + "\n".join(f"• {f}" for f in facts)
    ids_str = os.environ.get("ALLOWED_TELEGRAM_IDS", "")
    for uid_str in ids_str.split(","):
        uid_str = uid_str.strip()
        if uid_str.isdigit():
            try:
                await bot.send_message(int(uid_str), text, parse_mode="HTML")
            except Exception as e:
                logger.warning("send_adhd_digest: uid=%s err=%s", uid_str, e)


# Pending auto-suggest: uid → {"text": ..., "user_notion_id": ...}
_pending_auto: Dict[int, dict] = {}


# ── Handlers (вызываются из process_item / nexus_bot) ─────────────────────────

async def handle_memory_save(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    text = data.get("text", message.text or "")
    await mem.save_memory(message, text, user_notion_id, BOT_LABEL)


async def handle_memory_search(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    query = (data.get("query") or data.get("text") or "").strip()
    await mem.search_memory(message, query, user_notion_id, del_prefix="mem_del")


async def handle_memory_deactivate(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    await mem.deactivate_memory(message, hint, user_notion_id)


async def handle_memory_delete(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    await mem.delete_memory(
        message, hint, user_notion_id,
        del_prefix="mem_del", cancel_cb="mem_cancel",
    )


async def handle_memory_auto_suggest(
    message: Message,
    text: str,
    user_notion_id: str = "",
) -> None:
    await mem.auto_suggest_memory(
        message, text, user_notion_id, BOT_LABEL, _pending_auto,
        yes_prefix="mem_auto_yes", no_prefix="mem_auto_no",
    )


async def suggest_memory(message: Message, text: str, user_notion_id: str = "") -> None:
    """Удобная обёртка для вызова из других хендлеров (tasks.py и т.д.)."""
    await mem.auto_suggest_memory(
        message, text, user_notion_id, BOT_LABEL, _pending_auto,
        yes_prefix="mem_auto_yes", no_prefix="mem_auto_no",
    )


# ── Callbacks ────────────────────────────────────────────────────────────────

def _search_kb(uid: int) -> "InlineKeyboardMarkup":
    return mem._build_delete_keyboard(
        uid, mem._mem_delete_pages.get(uid, []),
        reactivate_cb="mem_reactivate_selected",
    )


def _delete_kb(uid: int) -> "InlineKeyboardMarkup":
    return mem._build_delete_keyboard(
        uid, mem._mem_delete_pages.get(uid, []),
        toggle_prefix="mem_del_toggle",
        selected_cb="mem_delete_selected",
        selected_label="🗑️ Удалить выбранные",
        all_cb="mem_delete_all",
        all_label="🗑️ Удалить все",
        cancel_label="❌ Отмена",
    )


@router.callback_query(F.data.startswith("mem_toggle:"))
async def cb_mem_toggle(call: CallbackQuery) -> None:
    """Переключить выбор записи — режим поиска (без действия в Notion)."""
    await call.answer()
    uid = call.from_user.id
    page_id = call.data.split(":", 1)[1]
    selected = mem._mem_selected.setdefault(uid, set())
    selected.discard(page_id) if page_id in selected else selected.add(page_id)
    pages = mem._mem_delete_pages.get(uid, [])
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    await call.message.edit_reply_markup(reply_markup=_search_kb(uid))


@router.callback_query(F.data.startswith("mem_del_toggle:"))
async def cb_mem_del_toggle(call: CallbackQuery) -> None:
    """Переключить выбор записи — режим удаления (без действия в Notion)."""
    await call.answer()
    uid = call.from_user.id
    page_id = call.data.split(":", 1)[1]
    selected = mem._mem_selected.setdefault(uid, set())
    selected.discard(page_id) if page_id in selected else selected.add(page_id)
    pages = mem._mem_delete_pages.get(uid, [])
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    await call.message.edit_reply_markup(reply_markup=_delete_kb(uid))


@router.callback_query(F.data.startswith("mem_deactivate_selected:"))
async def cb_mem_deactivate_selected(call: CallbackQuery) -> None:
    """Деактивировать выбранные записи (Актуально=false)."""
    await call.answer()
    uid = call.from_user.id
    selected = mem._mem_selected.pop(uid, set())
    mem._mem_delete_pages.pop(uid, None)
    if not selected:
        await call.message.edit_text("☐ Ничего не выбрано.")
        return
    from core.notion_client import update_page
    done = 0
    for pid in selected:
        try:
            await update_page(pid, {"Актуально": {"checkbox": False}})
            done += 1
        except Exception as e:
            logger.error("cb_mem_deactivate_selected: %s", e)
    noun = "запись" if done == 1 else "записи" if done < 5 else "записей"
    await call.message.edit_text(f"☑️ Помечено неактуальными: {done} {noun}.")


@router.callback_query(F.data.startswith("mem_deactivate_all:"))
async def cb_mem_deactivate_all(call: CallbackQuery) -> None:
    """Деактивировать все записи (Актуально=false)."""
    await call.answer()
    uid = call.from_user.id
    pages = mem._mem_delete_pages.pop(uid, [])
    mem._mem_selected.pop(uid, None)
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    from core.notion_client import update_page
    done = 0
    for page in pages:
        try:
            await update_page(page["id"], {"Актуально": {"checkbox": False}})
            done += 1
        except Exception as e:
            logger.error("cb_mem_deactivate_all: %s", e)
    noun = "запись" if done == 1 else "записи" if done < 5 else "записей"
    await call.message.edit_text(f"☑️ Помечено неактуальными: {done} {noun}.")


@router.callback_query(F.data.startswith("mem_reactivate_all:"))
async def cb_mem_reactivate_all(call: CallbackQuery) -> None:
    """Восстановить все записи (Актуально=True)."""
    await call.answer()
    uid = call.from_user.id
    pages = mem._mem_delete_pages.pop(uid, [])
    mem._mem_selected.pop(uid, None)
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    from core.notion_client import update_page
    done = 0
    for page in pages:
        if page["properties"].get("Актуально", {}).get("checkbox") is False:
            try:
                await update_page(page["id"], {"Актуально": {"checkbox": True}})
                done += 1
            except Exception as e:
                logger.error("cb_mem_reactivate_all: %s", e)
    noun = "запись" if done == 1 else "записи" if done < 5 else "записей"
    await call.message.edit_text(f"↩️ Восстановлено: {done} {noun}.")


@router.callback_query(F.data.startswith("mem_reactivate_selected:"))
async def cb_mem_reactivate_selected(call: CallbackQuery) -> None:
    """Восстановить выбранные записи (Актуально=True)."""
    await call.answer()
    uid = call.from_user.id
    selected = mem._mem_selected.pop(uid, set())
    pages = mem._mem_delete_pages.get(uid, [])
    if not selected:
        await call.message.edit_text("☐ Ничего не выбрано.")
        return
    from core.notion_client import update_page
    done = 0
    for pid in selected:
        try:
            await update_page(pid, {"Актуально": {"checkbox": True}})
            done += 1
            # обновить флаг в кэше
            for p in pages:
                if p["id"] == pid:
                    p.setdefault("properties", {}).setdefault("Актуально", {})["checkbox"] = True
        except Exception as e:
            logger.error("cb_mem_reactivate_selected: %s", e)
    noun = "запись" if done == 1 else "записи" if done < 5 else "записей"
    await call.message.edit_text(f"↩️ Восстановлено: {done} {noun}.")


@router.callback_query(F.data.startswith("mem_delete_selected:"))
async def cb_mem_delete_selected(call: CallbackQuery) -> None:
    """Архивировать выбранные записи."""
    await call.answer()
    uid = call.from_user.id
    selected = mem._mem_selected.pop(uid, set())
    pages = mem._mem_delete_pages.pop(uid, [])
    if not selected:
        await call.message.edit_text("☐ Ничего не выбрано.")
        return
    targets = [p for p in pages if p["id"] in selected]
    done = 0
    for page in targets:
        try:
            await mem._archive_page(page["id"])
            done += 1
        except Exception as e:
            logger.error("cb_mem_delete_selected: %s", e)
    verb = "Удалена" if done == 1 else "Удалено"
    noun = "запись" if done == 1 else "записи" if done < 5 else "записей"
    await call.message.edit_text(f"🗑 {verb} {done} {noun} из памяти.")


@router.callback_query(F.data.startswith("mem_delete_all:"))
async def cb_mem_delete_all(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    mem._mem_selected.pop(uid, None)
    pages = mem._mem_delete_pages.pop(uid, [])
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    deleted = 0
    for page in pages:
        try:
            await mem._archive_page(page["id"])
            deleted += 1
        except Exception as e:
            logger.error("cb_mem_delete_all: %s", e)
    n = deleted
    verb = "Удалена" if n == 1 else "Удалено"
    noun = "запись" if n == 1 else "записи" if n < 5 else "записей"
    await call.message.edit_text(f"🗑 {verb} {n} {noun} из памяти.")


@router.callback_query(F.data.startswith("mem_cancel:"))
async def cb_mem_cancel(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    mem._mem_selected.pop(uid, None)
    mem._mem_delete_pages.pop(uid, None)
    await call.message.edit_text("❌ Отмена.")


@router.callback_query(F.data.startswith("mem_auto_yes:"))
async def cb_mem_auto_yes(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    pending = _pending_auto.pop(uid, None)
    if not pending:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    fact, category, связь, ключ = await mem._parse_fact(pending["text"])
    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        await call.message.edit_text("⚠️ NOTION_DB_MEMORY не задан")
        return
    props = mem._build_props(fact, category, связь, ключ, BOT_LABEL, pending.get("user_notion_id", ""))
    result = await page_create(db_id, props)
    if result:
        cat_label = f" [{category}]" if category else ""
        await call.message.edit_text(f"🧠 Запомнила{cat_label}: {fact}")
    else:
        await call.message.edit_text("⚠️ Ошибка записи в Notion")


@router.callback_query(F.data.startswith("mem_auto_no:"))
async def cb_mem_auto_no(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
