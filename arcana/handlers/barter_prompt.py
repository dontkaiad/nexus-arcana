"""arcana/handlers/barter_prompt.py — интерактивный prompt бартера + reply-парсинг.

Флоу:
  • После сохранения ритуала/расклада с Источник=🔄 Бартер бот шлёт
    «🔄 Что в бартере?». Pending state: {kind, page_id, group_name, ttl=30мин}.
  • Кай отвечает строкой через запятую → создаём N пунктов чеклиста
    (тип=📋 Чеклист, категория=🔄 Бартер, статус=Not started, Группа=group_name,
    Бот=🌒 Arcana).
  • Reply на сообщение бота про ритуал/расклад (page_type ritual|session):
    — «отдала блок сигарет» → fuzzy-match по пункту → Done
    — «вместо блока сигарет колода таро» → переименовать + Done
    — «закинула 1500₽ за приворот» → finance_add(Доход) + Done денежный пункт
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import List, Optional

from aiogram import Router
from aiogram.types import Message

from core.cash_register import BOT_ARCANA
from core.config import config
from core.notion_client import (
    _select,
    _status,
    _text,
    _title,
    _with_user_filter,
    finance_add,
    get_page,
    page_create,
    query_pages,
    update_page,
)

logger = logging.getLogger("arcana.barter_prompt")

router = Router()

BARTER_CATEGORY = "🔄 Бартер"
LIST_TYPE_CHECKLIST = "📋 Чеклист"

# ── Pending state ────────────────────────────────────────────────────────────

_DB = os.path.join(os.path.dirname(__file__), "..", "..", "pending_barter.db")
_TTL = 1800  # 30 мин


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending_barter "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.commit()
    return con


def _save(uid: int, data: dict) -> None:
    with _con() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending_barter (uid, data, ts) VALUES (?,?,?)",
            (uid, json.dumps(data, ensure_ascii=False), time.time()),
        )


def _load(uid: int) -> Optional[dict]:
    with _con() as c:
        row = c.execute("SELECT data, ts FROM pending_barter WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    if time.time() - row[1] > _TTL:
        _drop(uid)
        return None
    return json.loads(row[0])


def _drop(uid: int) -> None:
    with _con() as c:
        c.execute("DELETE FROM pending_barter WHERE uid=?", (uid,))


# ── Public entry — вызывается из rituals.py / sessions.py ─────────────────────

async def propose_barter_prompt(
    message: Message,
    kind: str,
    page_id: str,
    group_name: str,
) -> None:
    """Сохранить pending и спросить «Что в бартере?»."""
    if not page_id:
        return
    uid = message.from_user.id
    _save(uid, {"kind": kind, "page_id": page_id, "group_name": group_name})
    await message.answer(
        "🔄 <b>Что в бартере?</b>\n"
        "Пришли списком через запятую, например:\n"
        "<code>2 блока сигарет, мерч улицы восток, поездка в беларусь</code>",
        parse_mode="HTML",
    )


# ── Parser ────────────────────────────────────────────────────────────────────

def _split_items(text: str) -> List[str]:
    if not text:
        return []
    raw = re.split(r"[,;\n]+", text)
    return [s.strip(" -—•") for s in raw if s.strip(" -—•")]


# ── Pending text → создать N чеклист-пунктов ──────────────────────────────────

async def handle_pending_text(message: Message, text: str, user_notion_id: str = "") -> bool:
    """Если есть pending barter — создать пункты и сбросить state.
    Возвращает True если перехватили."""
    uid = message.from_user.id
    pending = _load(uid)
    if not pending:
        return False
    items = _split_items(text)
    if not items:
        await message.answer("Не понял список — попробуй ещё раз через запятую.")
        return True

    db = config.db_lists
    if not db:
        await message.answer("⚠️ Lists DB не настроена.")
        _drop(uid)
        return True

    created = 0
    for name in items:
        props = {
            "Название": _title(name),
            "Тип": _select(LIST_TYPE_CHECKLIST),
            "Статус": _status("Not started"),
            "Бот": _select(BOT_ARCANA),
            "Категория": _select(BARTER_CATEGORY),
            "Группа": _text(pending["group_name"]),
        }
        if user_notion_id:
            from core.notion_client import _relation
            props["🪪 Пользователи"] = _relation(user_notion_id)
        try:
            pid = await page_create(db, props)
            if pid:
                created += 1
        except Exception as e:
            logger.warning("barter add_item failed for %r: %s", name, e)

    _drop(uid)
    await message.answer(f"✅ Создано {created} {_plural(created, 'пункт', 'пункта', 'пунктов')} бартера.")
    try:
        from arcana.handlers.reactions import react
        await react(message, "💅")
    except Exception:
        pass
    return True


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


# ── Reply на сообщение бота про ритуал/расклад ────────────────────────────────

_DONE_VERBS_RE = re.compile(
    r"\b(отдал\w*|закрыл\w*|сдел\w*|занесл\w*|закинул\w*|перевел\w*|перевела|"
    r"вернул\w*|подарил\w*|купил\w*)\b",
    re.IGNORECASE,
)
_REPLACE_RE = re.compile(
    r"\bвместо\s+(.+?)\s+([—\-:]\s+|на\s+|—\s*)?(.+)$",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(\d[\d\s.]*)\s*(?:₽|руб|рубл)", re.IGNORECASE)


async def _list_barter_for_group(group_name: str, user_notion_id: str) -> List[dict]:
    db = config.db_lists
    if not db or not group_name:
        return []
    base = {
        "and": [
            {"property": "Тип", "select": {"equals": LIST_TYPE_CHECKLIST}},
            {"property": "Категория", "select": {"equals": BARTER_CATEGORY}},
            {"property": "Группа", "rich_text": {"contains": group_name}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        ]
    }
    filters = _with_user_filter(base, user_notion_id)
    try:
        return await query_pages(db, filters=filters, page_size=100)
    except Exception as e:
        logger.warning("_list_barter_for_group failed: %s", e)
        return []


def _name(page: dict) -> str:
    arr = (page.get("properties", {}).get("Название", {}) or {}).get("title") or []
    return "".join(t.get("plain_text", "") for t in arr).strip()


def _fuzzy_pick(query: str, pages: List[dict]) -> Optional[dict]:
    """Берём первый пункт где имя содержит хотя бы одно общее слово (≥3 буквы)
    с query. Если ничего — None."""
    qwords = {w for w in re.findall(r"\w{3,}", query.lower())}
    best: Optional[tuple[int, dict]] = None
    for p in pages:
        nm = _name(p).lower()
        nwords = {w for w in re.findall(r"\w{3,}", nm)}
        score = len(qwords & nwords)
        if score and (not best or score > best[0]):
            best = (score, p)
    return best[1] if best else None


def _money_word(text: str) -> bool:
    """Есть ли в чеклист-имени денежный/откупный смысл."""
    low = text.lower()
    return any(w in low for w in ("откуп", "деньг", "₽", "руб"))


async def handle_reply_text(message: Message, text: str, user_notion_id: str = "") -> bool:
    """Reply на сообщение бота с page_type ritual|session.
    Возвращает True если запрос был обработан как бартер-апдейт.
    """
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return False
    if not message.reply_to_message.from_user.is_bot:
        return False
    from core.message_pages import get_message_page
    mp = await get_message_page(message.chat.id, message.reply_to_message.message_id)
    if not mp or mp.get("page_type") not in ("ritual", "session"):
        return False

    page_id = mp["page_id"]
    # Группу определяем по названию ритуала/расклада из самой Notion-страницы.
    page = await get_page(page_id)
    if not page:
        return False
    title_field = "Название" if mp["page_type"] == "ritual" else "Тема"
    arr = (page.get("properties", {}).get(title_field, {}) or {}).get("title") or []
    group_name = "".join(t.get("plain_text", "") for t in arr).strip()
    if not group_name:
        return False

    items = await _list_barter_for_group(group_name, user_notion_id)
    handled = False
    low = (text or "").lower().strip()

    # ── 1) Деньги «закинула 1500₽ за приворот» ─────────────────────────────
    money_m = _AMOUNT_RE.search(text or "")
    if money_m and _DONE_VERBS_RE.search(text or ""):
        amount = float(re.sub(r"[^\d.]", "", money_m.group(1)) or 0)
        if amount > 0:
            try:
                from datetime import date as _date
                await finance_add(
                    date=_date.today().strftime("%Y-%m-%d"),
                    amount=amount,
                    category="🔮 Практика",
                    type_="💰 Доход",
                    source="💳 Карта",
                    description=f"Бартер · {group_name}",
                    bot_label=BOT_ARCANA,
                    user_notion_id=user_notion_id,
                )
                # Закрываем money-пункт чеклиста
                money_item = next((p for p in items if _money_word(_name(p))), None)
                if money_item:
                    await update_page(money_item["id"], {"Статус": _status("Done")})
                handled = True
            except Exception as e:
                logger.warning("barter money handler failed: %s", e)

    # ── 2) «вместо X — Y» / «вместо X на Y» / «вместо X Y» ────────────────
    if not handled:
        old_part = new_part = ""
        m_word = re.search(r"\bвместо\s+(.+)$", low, re.IGNORECASE)
        if m_word:
            if True:
                tail_tokens = re.findall(r"\w+", m_word.group(1))
                # Для каждого пункта считаем consecutive prefix-match с началом tail.
                def _stem(w: str) -> str:
                    return w.lower()[:4]  # грубый стеммер: первые 4 буквы
                tail_stems = [_stem(t) for t in tail_tokens]
                best: Optional[tuple[int, int, dict]] = None
                for p in items:
                    nm_tokens = re.findall(r"\w+", _name(p))
                    nm_stems = {_stem(t) for t in nm_tokens}
                    cnt = 0
                    for s in tail_stems:
                        if s in nm_stems:
                            cnt += 1
                        else:
                            break
                    if cnt and (not best or cnt > best[0]):
                        best = (cnt, len(nm_tokens), p)
                if best:
                    target = best[2]
                    new_words = tail_tokens[best[0]:]
                    new_part = " ".join(new_words).strip(" -—:")
                    if new_part:
                        await update_page(target["id"], {
                            "Название": _title(new_part),
                            "Статус": _status("Done"),
                        })
                        handled = True
        if not handled and old_part and new_part:
            target = _fuzzy_pick(old_part, items)
            if target:
                await update_page(target["id"], {
                    "Название": _title(new_part),
                    "Статус": _status("Done"),
                })
                handled = True

    # ── 3) «отдала X / закрыла X» — fuzzy match ────────────────────────────
    if not handled and _DONE_VERBS_RE.search(text or ""):
        target = _fuzzy_pick(text, items)
        if target:
            await update_page(target["id"], {"Статус": _status("Done")})
            handled = True

    if handled:
        try:
            from arcana.handlers.reactions import react
            await react(message, "💅")
        except Exception:
            pass
    return handled
