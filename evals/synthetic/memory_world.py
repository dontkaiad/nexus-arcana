"""Синтетический «мир» для офлайн-эвала поиска по памяти: вымышленные люди и факты, без единого реального данного.

Строится во временных строках БД под user_id=SYN_USER и удаляется после прогона. Нужен Postgres (как в CI).
Символьные имена (m1_dis, o_imp, …) → реальные id при построении; кейсы ссылаются на символьные имена.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Dict, List

import sqlalchemy as sa
from sqlalchemy import delete, insert

from core.db import get_engine
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people

SYN_USER = "eval-synthetic"

# (символьное имя, человек|None, тип|None, текст факта, статус)
FACTS = [
    ("m1_dis1", "misha1", "dislikes", "не любит опоздания", "confirmed"),
    ("m1_dis2", "misha1", "dislikes", "ненавидит шумные места", "confirmed"),
    ("m1_like", "misha1", "likes", "любит джаз", "confirmed"),
    ("m2_dis", "misha2", "dislikes", "не любит жару", "confirmed"),
    ("m2_like", "misha2", "likes", "любит рок", "confirmed"),
    ("m1_wrongtype", "misha1", "likes", "не любит ходить один", "confirmed"),      # тип Haiku неверный: по тексту — dislikes
    ("m1_pending", "misha1", "dislikes", "не любит ожидание", "pending"),          # pending не должен попадать в выдачу
    ("o_imp", "olya", "important", "аллергия на орехи", "confirmed"),
    ("o_imp2", "olya", "important", "нельзя кофеин вечером", "confirmed"),
    ("o_like", "olya", "likes", "любит зелёный чай", "confirmed"),
    ("own_food", None, None, "не ем сладкое по вечерам", "confirmed"),
    ("own_food2", None, None, "не ем рыбу", "confirmed"),
    ("own_flat", None, None, "номер квартиры 42, этаж 5", "confirmed"),
    ("own_cat", None, None, "кота зовут Барсик", "confirmed"),
]
PEOPLE = [("misha1", "Михаил Тестов", ["Миша"], None), ("misha2", "Миша Образцов", ["Миша"], None),
          ("olya", "Ольга", ["Оля"], date(1990, 3, 5))]

# query, ожидаемые символьные id (порядок не важен), ожидаемый route
CASES = [
    ("что не любит миша", ["m1_dis1", "m1_dis2", "m1_wrongtype", "m2_dis"], "direct"),
    ("что любит миша", ["m1_like", "m2_like"], "direct"),
    ("что важно про олю", ["o_imp", "o_imp2"], "direct"),
    ("у оли аллергия", ["o_imp", "o_imp2"], "direct"),
    ("что любит оля", ["o_like"], "direct"),
    ("когда у оли др", [], "birthday"),
    ("что я не ем", ["own_food", "own_food2"], "hybrid_self"),
    ("номер квартиры", ["own_flat"], "hybrid"),
    ("как зовут кота", ["own_cat"], "hybrid"),
]


@contextmanager
def world():
    ids: Dict[str, int] = {}
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == SYN_USER))
        c.execute(delete(people).where(people.c.user_id == SYN_USER))
        for key, name, aliases, bday in PEOPLE:
            pid = c.execute(insert(people).values(user_id=SYN_USER, name=name, aliases=aliases, status="confirmed",
                                                  birthday=bday, birthday_year_known=True).returning(people.c.id)).scalar_one()
            ids[key] = pid
            prepo.sync_norm(c, pid)
        n = 0
        for key, who, typ, text, status in FACTS:
            n += 1
            pid = ids.get(who) if who else None
            k = f"person_{pid}_{typ}_{n:08x}" if pid else ""
            ids[key] = c.execute(insert(memories).values(
                fact_text=text, key_name=k, category="👥 Люди" if pid else "🛒 Предпочтения", person_id=pid,
                user_id=SYN_USER, review_status=status,
                related_to=(f"{dict((p[0], p[1]) for p in PEOPLE)[who]}" if who else "")).returning(memories.c.id)).scalar_one()
        for key, name, aliases, bday in PEOPLE:
            prepo.refresh_related(c, ids[key])
    try:
        yield ids
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == SYN_USER))
            c.execute(delete(people).where(people.c.user_id == SYN_USER))
