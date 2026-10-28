"""Загрузчики личных переписок (TG JSON-экспорт, VK HTML-архив) с ОДНОЗНАЧНЫМ автором.

Автор каждой реплики:
  kai      — владелец (TG: from_id == personal_information.user_id; VK: подпись «Вы»)
  partner  — собеседник по личному чату
  other    — кто-то третий (в личных чатах почти не бывает)

Один и тот же код читает чаты и для извлечения (extract_facts), и для проверки цитат
(attribute_quotes) — расхождение в парсинге уже стоило нам 45% реплик без автора в VK.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "июн": 6,
           "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


def nz(s: str) -> str:
    """Нормализация для сопоставления цитат: lower, ё→е, только буквы/цифры, схлопнутые пробелы."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower().replace("ё", "е"))).strip()


@dataclass
class Msg:
    author: str          # kai | partner | other
    date: str            # YYYY-MM-DD или ""
    text: str            # как в исходнике (для LLM)
    _n: str = field(default="", repr=False)

    @property
    def n(self) -> str:  # нормализованный текст, считается лениво
        if not self._n and self.text:
            self._n = nz(self.text)
        return self._n


@dataclass
class Chat:
    source: str          # tg | vk
    id: int              # TG: id собеседника; VK: peer_id
    title: str           # название диалога как в экспорте (может быть пустым — у VK часто нет ui_crumb)
    msgs: List[Msg]
    partner_names: Counter = field(default_factory=Counter)   # как подписаны реплики собеседника


PLATFORM_LABEL = {"tg": "TG", "vk": "VK"}
_DELETED = re.compile(r"^\s*(deleted(\s+(account|user))?|удал[её]нн\w*\s+(аккаунт|пользователь))\s*$", re.I)


def is_deleted_like(name: str) -> bool:
    return bool(_DELETED.match(name or ""))


def usable_name(name: str, is_self_name=None) -> bool:
    """Имя годится для карточки: есть буквы/цифры, не «Deleted Account», не мой алиас."""
    n = (name or "").strip()
    return bool(re.search(r"[^\W_]", n)) and not is_deleted_like(n) and not (is_self_name and is_self_name(n))


def classify_chat(chat: "Chat", sb) -> Tuple[str, str]:
    """('self', '') — чат с самой собой; ('person', имя) — обычный собеседник.

    1) id собеседника ∈ моих id → «Я» (saved_messages сюда не попадают — они не личные чаты);
    2) название нормальное (не пусто, не мой алиас, не «Deleted Account») → оно;
    3) иначе имя собеседника из подписи его реплик (TG: from, VK: имя в ссылке заголовка);
    4) название — мой алиас и другого имени нет → это мой же второй аккаунт → «Я»;
    5) иначе «Удалённый аккаунт · <платформа>» (карточка создаётся только если у неё есть факты)."""
    is_self_name = sb.is_self_name if sb else None
    if sb and sb.is_self(tg_id=chat.id if chat.source == "tg" else None,
                         vk_id=chat.id if chat.source == "vk" else None):
        return "self", ""
    if usable_name(chat.title, is_self_name):
        return "person", chat.title.strip()
    for n, _cnt in chat.partner_names.most_common():
        if usable_name(n, is_self_name):
            return "person", n.strip()
    if is_self_name and is_self_name(chat.title or ""):
        return "self", ""
    return "person", "Удалённый аккаунт · %s" % PLATFORM_LABEL.get(chat.source, chat.source.upper())


def _tg_text(t) -> str:
    return t if isinstance(t, str) else "".join(p if isinstance(p, str) else p.get("text", "") for p in t)


class ChatMap(dict):
    """{chat_id: Chat} + данные владельца экспорта (`owner`) для self-blocklist."""
    owner: Optional[dict] = None


def load_tg(path: Path) -> Dict[int, Chat]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    kai = "user%s" % d["personal_information"]["user_id"]
    out = ChatMap()
    out.owner = d.get("personal_information")
    for c in d["chats"]["list"]:
        if c.get("type") != "personal_chat":      # saved_messages, боты, группы — не люди
            continue
        partner = "user%s" % c["id"]
        msgs = []
        names: Counter = Counter()
        for m in c.get("messages", []):
            if m.get("type") != "message":
                continue
            fid = m.get("from_id")
            who = "kai" if fid == kai else "partner" if fid == partner else "other"
            if who == "partner" and m.get("from"):
                names[m["from"]] += 1
            msgs.append(Msg(who, (m.get("date") or "")[:10], _tg_text(m.get("text", "")).strip()))
        out[c["id"]] = Chat("tg", c["id"], c.get("name") or "", msgs, names)
    return out


# Реальная структура заголовка VK (имя собеседника — внутри ссылки, свои реплики — «Вы»):
#   <div class="message__header"><a href="https://vk.ru/id100">Имя Фамилия</a>, 12 мар 2021 в 17:53:19</div>
#   <div class="message__header">Вы, 12 мар 2021 в 17:53:19</div>
_HDR = re.compile(r'class="message__header">(.*?)</div>', re.S)
_BODY = re.compile(r'\s*<div>(.*?)(?:<div class="kludges">|</div>\s*</div>)', re.S)  # после </div> заголовка
_HREF = re.compile(r'href="https?://vk\.(?:ru|com)/id(\d+)"')
_TAG = re.compile(r"<[^>]+>")


def _vk_date(s: str) -> str:
    m = re.search(r"(\d{1,2})\s+([а-я]{3,4})\s+(\d{4})", s)
    if not m or m.group(2)[:3] not in _MONTHS:
        return ""
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2)[:3]], int(m.group(1)))


def parse_vk_page(page: str, peer_id: int, names: Optional[Counter] = None) -> List[Msg]:
    msgs: List[Msg] = []
    for blk in page.split('<div class="message" data-id=')[1:]:
        h = _HDR.search(blk)
        if not h:
            continue
        head = html.unescape(_TAG.sub("", h.group(1))).strip()
        label, _, rest = head.partition(", ")
        href = _HREF.search(h.group(1))
        if href and int(href.group(1)) == peer_id and names is not None and label.strip():
            names[label.strip()] += 1                  # имя собеседника — текст его ссылки в заголовке
        if label.strip() == "Вы":
            who = "kai"
        elif href and int(href.group(1)) == peer_id:
            who = "partner"
        elif href:
            who = "other"
        else:
            who = "partner"
        b = _BODY.search(blk[h.end():])
        body = html.unescape(_TAG.sub(" ", (b.group(1) if b else "").replace("<br>", " ")))
        msgs.append(Msg(who, _vk_date(rest), " ".join(body.split())))
    return msgs


def load_vk(root: Path) -> Dict[int, Chat]:
    root = Path(root)
    msg_dir = next((p for p in root.rglob("*") if p.is_dir() and p.name.lower() == "messages"), None)
    out: Dict[int, Chat] = {}
    if not msg_dir:
        return out
    for d in sorted(p for p in msg_dir.iterdir() if p.is_dir() and p.name.isdigit()):
        peer = int(d.name)
        if peer >= 2_000_000_000:        # беседы
            continue
        msgs: List[Msg] = []
        title = ""
        names: Counter = Counter()
        # messages0.html — самые новые; в хронологию переворачиваем по возрастанию номера файла
        for f in sorted(d.glob("messages*.html"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
            page = f.read_bytes().decode("cp1251", errors="replace")
            if not title:
                m = re.search(r'ui_crumb">([^<]*)<', page)
                title = html.unescape(m.group(1)).strip() if m else ""
            msgs.extend(parse_vk_page(page, peer, names))
        if msgs:
            out[peer] = Chat("vk", peer, title, msgs, names)
    return out


def load_chats(tg: str = "", vk: str = "") -> Dict[str, Dict[int, Chat]]:
    return {"tg": load_tg(Path(tg)) if tg else {}, "vk": load_vk(Path(vk)) if vk else {}}
