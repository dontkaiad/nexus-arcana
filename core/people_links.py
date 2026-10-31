"""core/people_links.py — связь клиентов Арканы с людьми (ADR-0027, PR-8a). Чистая логика, без БД.

Кандидаты — только подсказка, связывает владелец. Сигналы:
  account   — из `clients.contact` разобраны @username / t.me/<u> / «TG: <id>» / vk.com/id<id> и совпали
              с аккаунтами человека (username без регистра, либо числовой id)  w=0.90
  full_name — имя+фамилия в любом порядке                                        w=0.60
  name_birthday — общий токен имени + одинаковый ДР (год сравнивается, если известен у обоих) w=0.60
  alias     — имя клиента равно другому имени человека (или наоборот)             w=0.40
score = 1 − Π(1 − w). Клиент типа `self` ↔ карточка «Я» связывается детерминированно (см. pg_client_links).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from core.people import _same_bday, _tokens, full_name_key

LINK_WEIGHTS = {"account": 0.90, "full_name": 0.60, "name_birthday": 0.60, "alias": 0.40}

_USERNAME = re.compile(r"(?<![\w/])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
_TME = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?!joinchat|addstickers|share)([A-Za-z][A-Za-z0-9_]{3,31})", re.I)
_TG_ID = re.compile(r"(?:\bTG\s*:\s*|tg://user\?id=)(\d{5,})", re.I)
_VK_ID = re.compile(r"vk\.(?:com|ru)/id(\d{3,})", re.I)


def parse_contact(contact: Optional[str]) -> Dict[str, Set]:
    """Свободный текст `contact` → {usernames (lower), tg_ids, vk_ids}. Телефоны не разбираем:
    у людей телефонов нет, сопоставлять не с чем."""
    text = contact or ""
    return {
        "usernames": {m.lower() for m in _USERNAME.findall(text)} | {m.lower() for m in _TME.findall(text)},
        "tg_ids": {int(m) for m in _TG_ID.findall(text)},
        "vk_ids": {int(m) for m in _VK_ID.findall(text)},
    }


def _account_hits(parsed: Dict[str, Set], accounts: Iterable[Dict[str, Any]]) -> bool:
    for a in accounts or []:
        if a["platform"] == "tg" and (a["external_id"] in parsed["tg_ids"]
                                      or (a.get("username") and a["username"].lower() in parsed["usernames"])):
            return True
        if a["platform"] == "vk" and a["external_id"] in parsed["vk_ids"]:
            return True
    return False


def _bday_of(client: Dict[str, Any]):
    b = client.get("birthday")
    if not b:
        return None
    from datetime import date
    return date.fromisoformat(b) if isinstance(b, str) else b


def score_candidate(client: Dict[str, Any], person: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """client: {name, contact, birthday}; person: {id, name, aliases, birthday, birthday_year_known, accounts}."""
    reasons: List[str] = []
    if _account_hits(parse_contact(client.get("contact")), person.get("accounts") or []):
        reasons.append("account")
    cfull, pfull = full_name_key(client.get("name") or ""), full_name_key(person.get("name") or "")
    if cfull and cfull == pfull:
        reasons.append("full_name")
    cb, pb = _bday_of(client), person.get("birthday")
    if cb and pb and (set(_tokens(client.get("name") or "")) & set(_tokens(person.get("name") or ""))) \
            and _same_bday({"birthday": cb, "birthday_year_known": True},
                           {"birthday": pb, "birthday_year_known": person.get("birthday_year_known", True)}):
        reasons.append("name_birthday")
    cname = " ".join(_tokens(client.get("name") or ""))
    if cname and cname in {" ".join(_tokens(a)) for a in (person.get("aliases") or [])}:
        reasons.append("alias")                       # имя клиента = другое имя человека
    if not reasons:
        return None
    miss = 1.0
    for r in reasons:
        miss *= 1 - LINK_WEIGHTS[r]
    return {"person": person, "score": round(1 - miss, 3), "reasons": reasons}


def rank_candidates(client: Dict[str, Any], people: Iterable[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """Люди, подходящие клиенту, по убыванию балла. Отклонённые и «Я» не предлагаются
    (клиент `self` обрабатывается отдельно)."""
    out = []
    for p in people:
        if p.get("status") == "rejected" or p.get("is_self"):
            continue
        c = score_candidate(client, p)
        if c:
            out.append(c)
    out.sort(key=lambda c: (-c["score"], c["person"]["id"]))
    return out[:limit]
