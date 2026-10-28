"""Self-blocklist: владелец (Кай) никогда не становится «человеком» и не получает фактов.

Что считается «собой»:
  • имена: «Кай», «Kai», «Kai Lark», «Вы» (зашиты в код — это публичный псевдоним) +
    всё из окружения/экспорта: PEOPLE_IMPORT_SELF_NAMES (через запятую; ЗДЕСЬ реальное имя,
    в публичный репо его класть нельзя), имя/фамилия/«имя фамилия» из personal_information
    TG-экспорта;
  • id: оба (все) tg_id из ALLOWED_TELEGRAM_IDS, PEOPLE_IMPORT_SELF_TG_IDS, user_id из
    TG-экспорта, PEOPLE_IMPORT_SELF_VK_IDS (VK-id владельца в архиве автоматически не
    определить — реплики Кай подписаны «Вы» без ссылки);
  • username'ы: PEOPLE_IMPORT_SELF_USERNAMES, username из TG-экспорта.
Сравнение имён — по нормализованной строке целиком (регистр, ё/е, знаки не важны) и
по набору слов для двусловных имён («Иванова Анна» == «Анна Иванова»).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from tools.people_import.chats import nz

SAFE_NAMES = ("Кай", "Kai", "Kai Lark", "Вы")


def _csv(name: str) -> list:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def _ids(name: str) -> Set[int]:
    return {int(x) for x in _csv(name) if x.lstrip("-").isdigit()}


@dataclass
class SelfBlock:
    names: Set[str] = field(default_factory=set)          # нормализованные
    display: List[str] = field(default_factory=list)      # как написаны (для алиасов карточки «Я»)
    phrases: List[tuple] = field(default_factory=list)    # [(основа, макс.длина), …] многословных имён: вхождение в любой форме
    tg_ids: Set[int] = field(default_factory=set)
    vk_ids: Set[int] = field(default_factory=set)
    usernames: Set[str] = field(default_factory=set)      # lower, без @

    def add_names(self, names: Iterable[str]) -> None:
        for n in names:
            k = nz(n)
            if k:
                self.names.add(k)
                if n.strip() not in self.display:
                    self.display.append(n.strip())
                words = [w for w in k.split() if len(w) >= 3]
                if len(k.split()) >= 2 and len(words) == len(k.split()):
                    # «Асоциальный контракт»: ловим и «Контрактом Асоциальным», и «… 🖤 Имя Асоциальный контракт»;
                    # однословные имена («Кай», «Карина») так не ловим — слишком много ложных совпадений
                    # (основа из ≤4 букв, макс. длина формы): «ника» — форма «ник», «никита» — нет
                    stems = tuple(sorted({(w[:4] if len(w) >= 5 else w, len(w) + 2) for w in words}))
                    if stems not in self.phrases:
                        self.phrases.append(stems)

    # Карточка «Я»: алиасы и «основные» идентификаторы. Реальные имена приходят из окружения/экспорта
    # и попадают только в БД — в репозитории лежат лишь «Кай/Kai/Kai Lark».
    def display_names(self) -> List[str]:
        return [n for n in self.display if nz(n) != "вы"]      # «Вы» — подпись в VK, не имя

    def primary_tg_id(self) -> Optional[int]:
        return min(self.tg_ids) if self.tg_ids else None

    def primary_vk_id(self) -> Optional[int]:
        return min(self.vk_ids) if self.vk_ids else None

    def primary_username(self) -> Optional[str]:
        return sorted(self.usernames)[0] if self.usernames else None

    def with_tg_owner(self, owner: Optional[dict]) -> "SelfBlock":
        """Дополняет блок-лист данными владельца из personal_information TG-экспорта."""
        if owner:
            if owner.get("user_id"):
                self.tg_ids.add(int(owner["user_id"]))
            if owner.get("username"):
                self.usernames.add(str(owner["username"]).lstrip("@").lower())
            first, last = owner.get("first_name") or "", owner.get("last_name") or ""
            self.add_names([first, last, ("%s %s" % (first, last)).strip()])
        return self

    def is_self_name(self, name: str) -> bool:
        k = nz(name)
        if not k:
            return False
        if k in self.names:
            return True
        words = k.split()
        if len(words) == 2 and " ".join(reversed(words)) in self.names:
            return True
        return any(all(any(w.startswith(s) and len(w) <= mx for w in words) for s, mx in stems)
                   for stems in self.phrases)

    def is_self(self, name: str = "", tg_id: Optional[int] = None, vk_id: Optional[int] = None,
                username: str = "") -> bool:
        return bool(
            (tg_id and int(tg_id) in self.tg_ids)
            or (vk_id and int(vk_id) in self.vk_ids)
            or (username and username.lstrip("@").lower() in self.usernames)
            or (name and self.is_self_name(name))
        )


def load_selfblock(tg_owner: Optional[dict] = None) -> SelfBlock:
    sb = SelfBlock()
    sb.add_names(SAFE_NAMES)
    sb.add_names(_csv("PEOPLE_IMPORT_SELF_NAMES"))
    sb.tg_ids |= _ids("ALLOWED_TELEGRAM_IDS") | _ids("PEOPLE_IMPORT_SELF_TG_IDS")
    sb.vk_ids |= _ids("PEOPLE_IMPORT_SELF_VK_IDS")
    sb.usernames |= {u.lstrip("@").lower() for u in _csv("PEOPLE_IMPORT_SELF_USERNAMES")}
    return sb.with_tg_owner(tg_owner)
