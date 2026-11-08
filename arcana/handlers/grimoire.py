"""arcana/handlers/grimoire.py — Гримуар: view layer + CRUD."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core import llm_health
from core.claude_client import LLMUnavailable, ask_claude
from core.error_log import log_error
from arcana.repos.grimoire_repo import (
    GrimoireRepo, GrimoireEntry, RitualSummary,
)

logger = logging.getLogger("arcana.grimoire")
router = Router()
_repo = GrimoireRepo()

GRIMOIRE_CATEGORIES = {
    "заговор":    "📿 Заговор",
    "рецепт":     "🧴 Рецепт",
    "комбинация": "✨ Комбинация",
    "заметка":    "📝 Заметка",
}

GRIMOIRE_THEMES = {
    "финансы":     "💰 Финансы",
    "деньги":      "💰 Финансы",
    "любовь":      "💕 Любовь",
    "защита":      "🛡️ Защита",
    "деструктив":  "💀 Деструктив",
    "привлечение": "🧲 Привлечение",
    "очищение":    "🌊 Очищение",
    "другое":      "🔮 Другое",
}

PARSE_GRIMOIRE_SYSTEM = (
    "Извлеки данные для записи в гримуар. Ответь ТОЛЬКО JSON без markdown-заборов:\n"
    '{"title": "короткое название (1-5 слов, до 60 символов)", '
    '"category": "заговор|рецепт|комбинация|заметка", '
    '"themes": ["финансы","любовь","защита","деструктив","привлечение","очищение","другое"], '
    '"source": "откуда узнала или null"}\n\n'
    "Правила:\n"
    "— title никогда не пустой и всегда короткий; если явного названия нет — "
    "сформулируй по смыслу 1-5 слов. Никогда не копируй в title абзацы.\n"
    "— Полный текст записи НЕ нужен: он сохраняется отдельно. Тебе дан только "
    "начальный фрагмент текста.\n"
    "— category выбери одну из 4 опций по смыслу.\n"
    "— themes — массив подходящих тем (можно пустой).\n\n"
    "Пример:\n"
    'Текст: «тест — заговор на деньги, читать на убывающую луну»\n'
    '{"title":"тест","category":"заговор","themes":["финансы"],"source":null}'
)

_TITLE_MAX = 80       # title в БД/списках — короткое название, не абзац
_LLM_HEAD_CHARS = 400  # сколько символов ввода видит парсер (как в _parse_work_text)
_PREFIX_RE = None


def _strip_command_prefix(text: str) -> str:
    import re as _re
    return _re.sub(r"^\s*запиши\s+в\s+гримуар\s*[:\-—]?\s*", "", (text or ""),
                   flags=_re.IGNORECASE).strip()


def _clip_title(raw: str, limit: int = _TITLE_MAX) -> str:
    """Первая непустая строка, схлопнутые пробелы, обрезка по границе слова ≤ limit."""
    import re as _re
    line = next((ln.strip() for ln in (raw or "").splitlines() if ln.strip()), "")
    line = _re.sub(r"\s+", " ", line).strip(" —-:.,")
    if len(line) <= limit:
        return line
    cut = line.rfind(" ", 0, limit - 1)
    if cut < limit // 2:
        cut = limit - 1
    return line[:cut].rstrip(" ,;:—-") + "…"


# Заголовки разделов: markdown (#), нумерованные короткие строки, короткие строки с «:» на конце.
_HEADING_RE = None


def split_sections(text: str) -> list:
    """Разбить текст на разделы по заголовкам -> [(title, body)]. Пусто, если разделов < 3
    (одна запись; вопрос «разбить?» задаём только когда тем явно несколько)."""
    import re as _re
    global _HEADING_RE
    if _HEADING_RE is None:
        _HEADING_RE = _re.compile(
            r"^\s*(?:#{1,3}\s+\S.*|\d{1,2}[.)]\s+\S[^\n]{0,70}|[^\n]{3,60}:)\s*$"
        )
    lines = (text or "").splitlines()
    idx = [i for i, ln in enumerate(lines) if _HEADING_RE.match(ln)]
    if len(idx) < 3:
        return []
    sections = []
    if idx[0] > 0 and "\n".join(lines[:idx[0]]).strip():
        sections.append(("", "\n".join(lines[:idx[0]]).strip()))
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(lines)
        head = lines[i].strip().lstrip("#").strip().rstrip(":")
        sections.append((head, "\n".join(lines[i + 1:end]).strip()))
    return sections


def _heuristic_grimoire_parse(text: str) -> dict:
    """Fallback на случай если Haiku вернул пустой/битый JSON.

    Снимает префикс «запиши в гримуар:» и делит первое предложение по « — »
    или « : » на title/text. Категорию и темы определяет по ключевым словам.
    """
    import re as _re
    src = (text or "").strip()
    src = _re.sub(r"^\s*запиши\s+в\s+гримуар\s*[:\-—]?\s*", "", src, flags=_re.IGNORECASE)
    if not src:
        return {}
    sep = _re.search(r"\s+[—–\-:]\s+", src)
    if sep and sep.start() <= _TITLE_MAX:
        # «название — текст»: разделитель учитываем только в начале (иначе « - »
        # из списка далеко внутри текста делал title из всего вступления)
        title = src[:sep.start()].strip(" —-:.,")
        body = src[sep.end():].strip()
    else:
        # коротко → всё title, длинно → первая строка/слова → title, текст целиком в body
        words = src.split()
        if len(words) <= 4:
            title, body = src, ""
        else:
            title, body = " ".join(words[:4]), src
    title = _clip_title(title)
    low = (title + " " + body).lower()
    cat = "заметка"
    if "заговор" in low: cat = "заговор"
    elif "рецепт" in low or "масло" in low or "настой" in low: cat = "рецепт"
    elif "комбинация" in low: cat = "комбинация"
    themes: list = []
    for kw, theme in [
        (("деньги", "финансы", "долг", "доход"), "финансы"),
        (("любовь", "любви", "приворот", "отношения"), "любовь"),
        (("защита", "защит"), "защита"),
        (("деструктив", "порча", "сглаз"), "деструктив"),
        (("привлеч",), "привлечение"),
        (("очищ", "чист"), "очищение"),
    ]:
        if any(k in low for k in kw):
            themes.append(theme)
    return {
        "title": title or src[:40],
        "category": cat,
        "themes": themes,
        "text": body or title,
        "source": None,
    }


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕯️ Ритуалы",   callback_data="grim_rituals"),
            InlineKeyboardButton(text="📿 Заговоры",   callback_data="grim_spells"),
        ],
        [
            InlineKeyboardButton(text="🧴 Рецепты",    callback_data="grim_recipes"),
            InlineKeyboardButton(text="✨ Комбинации", callback_data="grim_combos"),
        ],
        [
            InlineKeyboardButton(text="📦 Инвентарь",  callback_data="grim_inventory"),
            InlineKeyboardButton(text="📝 Заметки",    callback_data="grim_notes"),
        ],
        [
            InlineKeyboardButton(text="🔍 Поиск",      callback_data="grim_search"),
        ],
    ])


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="grim_menu")],
    ])


def _parse_json_safe(raw: str) -> dict:
    """Принимает ответ Haiku (часто завёрнутый в ```json … ``` или с
    префиксом-комментарием). Возвращает {} если разобрать не удалось."""
    if not raw:
        return {}
    s = raw.strip()
    # 1) попытка как есть
    try:
        return json.loads(s)
    except Exception:
        pass
    # 2) вырезать первый JSON-объект из произвольного текста
    import re as _re
    fenced = _re.sub(r"```(?:json)?\s*|\s*```", "", s, flags=_re.IGNORECASE)
    m = _re.search(r"\{.*\}", fenced, flags=_re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def _format_grimoire_list(items: List[GrimoireEntry], title: str) -> str:
    if not items:
        return f"{title}\n\nПусто."
    lines = [f"<b>{title} ({len(items)})</b>\n"]
    for i, entry in enumerate(items[:10], 1):
        theme_str = " · ".join(t.split(" ")[0] for t in entry.themes) if entry.themes else ""
        check = " · ✅" if entry.verified else ""
        lines.append(f"{i}. {entry.title}" + (f" · {theme_str}" if theme_str else "") + check)
    if len(items) > 10:
        lines.append(f"\n… ещё {len(items) - 10}")
    return "\n".join(lines)


def _format_ritual_list(items: List[RitualSummary]) -> str:
    if not items:
        return "<b>🕯️ Ритуалы</b>\n\nПусто."
    lines = [f"<b>🕯️ Ритуалы ({len(items)})</b>\n"]
    for i, r in enumerate(items[:10], 1):
        lines.append(f"{i}. {r.name}" + (f" · {r.date}" if r.date else "") + f" {r.result_icon}")
    if len(items) > 10:
        lines.append(f"\n… ещё {len(items) - 10}")
    return "\n".join(lines)


# ── Menu ──────────────────────────────────────────────────────────────────────

async def handle_grimoire_menu(message: Message, user_id: str = "") -> None:
    await message.answer("📖 <b>Гримуар</b>", reply_markup=_menu_keyboard(), parse_mode="HTML")


# ── Callback handlers ─────────────────────────────────────────────────────────

async def _get_user_id(callback: CallbackQuery) -> str:
    """Извлечь user_id из middleware data (через bot_data fallback)."""
    # middleware прикрепляет user_id к data при каждом апдейте;
    # для callback_query тоже проходит через middleware, значение доступно напрямую
    return ""  # будет перекрыто при регистрации через wrapper


@router.callback_query(F.data == "grim_menu")
async def cb_grim_menu(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.message.edit_text("📖 <b>Гримуар</b>", reply_markup=_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "grim_rituals")
async def cb_grim_rituals(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        items = await _repo.rituals_list(user_id)
        text = _format_ritual_list(items)
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_rituals: %s", e)
        await callback.message.edit_text("Ошибка загрузки ритуалов.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_spells")
async def cb_grim_spells(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        items = await _repo.list_by_category("📿 Заговор", user_id)
        text = _format_grimoire_list(items, "📿 Заговоры")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_spells: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_recipes")
async def cb_grim_recipes(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        items = await _repo.list_by_category("🧴 Рецепт", user_id)
        text = _format_grimoire_list(items, "🧴 Рецепты")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_recipes: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_combos")
async def cb_grim_combos(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        items = await _repo.list_by_category("✨ Комбинация", user_id)
        text = _format_grimoire_list(items, "✨ Комбинации")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_combos: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_notes")
async def cb_grim_notes(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        items = await _repo.list_by_category("📝 Заметка", user_id)
        text = _format_grimoire_list(items, "📝 Заметки")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_notes: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_inventory")
async def cb_grim_inventory(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    try:
        from arcana.handlers.lists import _fetch_all_display_items, render_inv_screen, BOT_NAME
        all_items = await _fetch_all_display_items(None, BOT_NAME, user_id)
        text, buttons = render_inv_screen(all_items)
        # replace back button with grimoire back
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="grim_menu")],
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_inventory: %s", e)
        await callback.message.edit_text("Ошибка загрузки инвентаря.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_search")
async def cb_grim_search(callback: CallbackQuery, user_id: str = "") -> None:
    await callback.answer()
    uid = callback.from_user.id
    from arcana.pending_grimoire_search import save_pending_search
    await save_pending_search(uid, user_id)
    await callback.message.edit_text(
        "🔍 Введи поисковый запрос (слово, тема или категория):",
        reply_markup=_back_keyboard(),
    )


# ── Text handlers (add / search) ──────────────────────────────────────────────

_PK_SPLIT = "grimoire_split"
_SPLIT_TTL = 1800


def _normalise_themes(raw_themes) -> list:
    """Только валидные темы из справочника (неизвестные отбрасываем, без дублей)."""
    out: list = []
    for t in raw_themes or []:
        if not isinstance(t, str):
            continue
        val = GRIMOIRE_THEMES.get(t.strip().lower())
        if val and val not in out:
            out.append(val)
    return out


async def _save_entry(message: Message, *, title: str, category: str, themes: list,
                      text: str, source: str, user_id: str) -> bool:
    page_id = await _repo.add(
        title=title, category=category, themes=themes if themes else None,
        text=text or "", source=source or "", user_id=user_id,
    )
    if not page_id:
        return False
    import html as _html
    reply = f"📖 Записано в гримуар: {category} <b>{_html.escape(title)}</b>"
    if themes:
        reply += f" [{' · '.join(themes)}]"
    await message.answer(reply, parse_mode="HTML")
    return True


async def handle_grimoire_add(message: Message, text: str, user_id: str = "") -> None:
    """Записать новую запись в гримуар. «запиши в гримуар: ...»

    LLM видит только начало текста (название/категория/темы), полный текст
    сохраняется кодом без потерь (как _parse_work_text). title всегда ≤ 80 символов.
    Если в тексте явно несколько тем-разделов — не режем молча, а спрашиваем.
    """
    try:
        from arcana.handlers.work_preview import _split_head_tail
        src = _strip_command_prefix(text)
        head, _tail = _split_head_tail(src, _LLM_HEAD_CHARS)
        raw = await ask_claude(
            f"Текст: {head}",
            system=PARSE_GRIMOIRE_SYSTEM,
            max_tokens=200,
            role="parse",
            temperature=0,
            strict=True,
        )
        data = _parse_json_safe(raw)
        if not data.get("title"):
            # Fallback — простой эвристический парсер, чтобы не «терять» запись.
            data = _heuristic_grimoire_parse(text)
        title = _clip_title(data.get("title") or "")
        if not title:
            await message.answer("Не смогла распознать запись. Попробуй написать подробнее.")
            return

        category = GRIMOIRE_CATEGORIES.get(str(data.get("category") or "заметка").lower(), "📝 Заметка")
        themes = _normalise_themes(data.get("themes"))
        source = data.get("source") if isinstance(data.get("source"), str) else ""
        body = src or text

        sections = split_sections(body)
        if sections:
            from core import pending_kv as _pkv
            import html as _html
            uid = message.from_user.id
            _pkv.save(uid, _PK_SPLIT, {
                "title": title, "category": category, "themes": themes,
                "text": body, "source": source or "", "user_id": user_id or "",
                "sections": [list(x) for x in sections],
            }, ttl=_SPLIT_TTL)
            heads = [h or _clip_title(b, 40) for h, b in sections]
            lines = "\n".join(f"• {_html.escape(h)}" for h in heads[:10])
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📖 Одной записью", callback_data=f"grim_split_one:{uid}"),
                InlineKeyboardButton(text=f"✂️ Разбить на {len(sections)}", callback_data=f"grim_split_many:{uid}"),
            ]])
            await message.answer(
                f"📖 В тексте {len(sections)} раздела(ов):\n{lines}\n\n"
                f"Записать одной записью «<b>{_html.escape(title)}</b>» или разбить?",
                parse_mode="HTML", reply_markup=kb,
            )
            return

        if not await _save_entry(message, title=title, category=category, themes=themes,
                                 text=body, source=source or "", user_id=user_id):
            await message.answer("Не удалось записать в гримуар.")
    except LLMUnavailable:
        # Сбой API: не пишем запись из эвристического fallback и не логируем как «ошибку бота».
        await message.answer(llm_health.UNAVAILABLE_REPLY)
        llm_health.mark_notified()
    except Exception as e:
        logger.exception("handle_grimoire_add: %s", e)
        await log_error(str(e), context="handle_grimoire_add", bot_label="🌒 Arcana")
        await message.answer("Ошибка при записи в гримуар.")


@router.callback_query(F.data.startswith("grim_split_"))
async def cb_grimoire_split(query: CallbackQuery) -> None:
    """Ответ на «одной записью или разбить?» для многотемного текста."""
    from core import pending_kv as _pkv
    mode, _, uid_s = query.data.partition(":")
    uid = query.from_user.id
    st = _pkv.pop(uid, _PK_SPLIT, ttl=_SPLIT_TTL)
    if not st or uid_s != str(uid):
        await query.answer("⏰ Устарело — отправь текст ещё раз", show_alert=True)
        return
    common = dict(category=st["category"], themes=st["themes"],
                  source=st.get("source") or "", user_id=st.get("user_id") or "")
    try:
        if mode == "grim_split_many":
            n = 0
            for head, body in st["sections"]:
                title = _clip_title(head or body)
                if title and await _save_entry(query.message, title=title,
                                               text=(body or head), **common):
                    n += 1
            await query.answer(f"✂️ Записано: {n}")
        else:
            await _save_entry(query.message, title=st["title"], text=st["text"], **common)
            await query.answer("📖 Записано")
        try:
            await query.message.edit_reply_markup()
        except Exception:
            pass
    except Exception as e:
        logger.exception("cb_grimoire_split: %s", e)
        await query.answer("⚠️ Ошибка записи", show_alert=True)


async def handle_grimoire_search(message: Message, text: str, user_id: str = "") -> None:
    """Поиск в гримуаре по тексту или теме."""
    try:
        # Определить тему из текста
        theme: Optional[str] = None
        query = text.strip()
        for key, val in GRIMOIRE_THEMES.items():
            if key in query.lower():
                theme = val
                query = query.lower().replace(key, "").strip()
                break

        items = await _repo.search(
            query=query if len(query) >= 2 else "",
            theme=theme,
            user_id=user_id,
        )

        if not items:
            await message.answer("📖 Ничего не найдено в гримуаре.")
            return

        if len(items) == 1:
            entry = items[0]
            reply = f"{entry.category} <b>{entry.title}</b>\n"
            if entry.themes:
                reply += " · ".join(entry.themes) + "\n"
            if entry.verified:
                reply += "✅ Проверено\n"
            if entry.text:
                reply += f"\n📜 Текст:\n{entry.text}\n"
            if entry.source:
                reply += f"\n📚 Источник: {entry.source}"
            await message.answer(reply.strip(), parse_mode="HTML")
        else:
            text_out = _format_grimoire_list(items, "🔍 Результаты поиска")
            await message.answer(text_out, parse_mode="HTML")

    except Exception as e:
        logger.exception("handle_grimoire_search: %s", e)
        await log_error(str(e), context="handle_grimoire_search", bot_label="🌒 Arcana")
        await message.answer("Ошибка поиска в гримуаре.")


async def check_pending_search(message: Message, text: str) -> bool:
    """Если юзер ожидает ввода поискового запроса — обработать и вернуть True."""
    uid = message.from_user.id
    from arcana.pending_grimoire_search import pop_pending_search
    user_id = await pop_pending_search(uid)
    if user_id is None:
        return False
    await handle_grimoire_search(message, text, user_id)
    return True
