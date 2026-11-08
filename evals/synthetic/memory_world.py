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

from core.cats import cat_fact_key
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos import pg_people_repo as prepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from core.repos.people_tables import people

SYN_USER = "eval-synthetic"

# (символьное имя, человек|None, тип|None, текст факта, статус)
FACTS = [
    ("m1_dis1", "tyoma1", "dislikes", "не любит опоздания", "confirmed"),
    ("m1_dis2", "tyoma1", "dislikes", "ненавидит шумные места", "confirmed"),
    ("m1_like", "tyoma1", "likes", "любит джаз", "confirmed"),
    ("m2_dis", "tyoma2", "dislikes", "не любит жару", "confirmed"),
    ("m2_like", "tyoma2", "likes", "любит рок", "confirmed"),
    ("m1_wrongtype", "tyoma1", "likes", "не любит ходить один", "confirmed"),      # тип Haiku неверный: по тексту — dislikes
    ("m1_pending", "tyoma1", "dislikes", "не любит ожидание", "pending"),          # pending не должен попадать в выдачу
    ("o_imp", "olya", "important", "аллергия на орехи", "confirmed"),
    ("o_imp2", "olya", "important", "нельзя кофеин вечером", "confirmed"),
    ("o_like", "olya", "likes", "любит зелёный чай", "confirmed"),
    ("own_food", None, None, "не ем сладкое по вечерам", "confirmed"),
    ("own_food2", None, None, "не ем рыбу", "confirmed"),
    ("own_flat", None, None, "номер квартиры 42, этаж 5", "confirmed"),
    ("own_cat", None, None, "кота зовут Барсик", "confirmed"),
    # подсказки по объекту покупки: свои положительные, свои отрицательные/аллергия (ловушки), чужие
    ("own_milk", None, None, "предпочитает овсяное молоко марки Тест", "confirmed"),
    ("own_milk_neg", None, None, "не пью коровье молоко", "confirmed"),
    ("own_nuts", None, None, "аллергия на орехи", "confirmed"),
    ("own_tea", None, None, "пью чёрный чай без сахара", "confirmed"),
    # человек с тем же именем, что у кота (ловушка «неоднозначно»)
    ("tp_dis", "tp", "dislikes", "не ест мясо", "confirmed"),
    ("tp_like", "tp", "likes", "любит борщ", "confirmed"),
]
PEOPLE = [("tyoma1", "Алекс Тестов", ["Алекс"], None), ("tyoma2", "Алекс Образцов", ["Алекс"], None),
          ("olya", "Вера Тестова", ["Верочка"], date(1990, 3, 5)),
          ("tp", "Тучка Тестова", ["Тучка"], None)]

# Коты (вымышленные): (символьное имя, имя, клички, статус карточки). «Рыжик» — карточка убрана (rejected).
CATS = [("tuchka", "Тучка", ["Туча"], "confirmed"), ("ponchik", "Пончик", ["Пончо"], "confirmed"),
        ("ryzhik", "Рыжик", [], "rejected")]
# (символьное имя, кот|None, тип|None, текст факта); кот=None — «общее про котов», без cat_id
CAT_FACTS = [
    ("t_dis", "tuchka", "dislikes", "Тучка не ест курицу"),
    ("t_like", "tuchka", "likes", "Тучка любит окно"),
    ("t_imp", "tuchka", "important", "у Тучки аллергия на курицу"),
    ("t_med", "tuchka", "other", "Тучка принимает лекарства по утрам"),
    ("p_dis", "ponchik", "dislikes", "Пончик не ест сыр"),
    ("p_like", "ponchik", "likes", "Пончик любит коробки"),
    ("p_med", "ponchik", "other", "Пончик принимает лекарства вечером"),
    ("r_dis", "ryzhik", "dislikes", "Рыжик не ест рыбу"),                  # кот убран: факт не должен всплывать
    ("cat_general", None, None, "кот боится пылесоса"),                    # без привязки к коту
]

# query, ожидаемые символьные id (порядок не важен), ожидаемый route
CASES = [
    ("что не любит алекс", ["m1_dis1", "m1_dis2", "m1_wrongtype", "m2_dis"], "direct"),
    ("что любит алекс", ["m1_like", "m2_like"], "direct"),
    ("что важно про веру", ["o_imp", "o_imp2"], "direct"),
    ("у веры аллергия", ["o_imp", "o_imp2"], "direct"),
    ("что любит вера", ["o_like"], "direct"),
    ("когда у веры др", [], "birthday"),
    ("что я не ем", ["own_food", "own_food2"], "hybrid_self"),
    ("номер квартиры", ["own_flat"], "hybrid"),
    ("как зовут кота", ["own_cat"], "hybrid"),
]

# Подсказки по объекту покупки («как обычно» / предпочтения списков): объект, что ДОЛЖНО подсказаться, чего НЕ должно
# (отрицание «не пью», аллергия, факт другого человека, «не ем рыбу»).
HINT_CASES = [
    ("молоко", ["own_milk"], ["own_milk_neg"]),
    ("орехи", [], ["own_nuts", "o_imp"]),
    ("рыбу", [], ["own_food2"]),
    ("чай", ["own_tea"], ["o_like"]),
    ("гранат", [], []),                                            # такого в памяти нет → ничего не выдумываем
]

# Вопросы про котов: query, ожидаемые символьные id, route, extra. extra: {"ambiguous": N} — вопрос неоднозначен,
# ожидается subject=ambiguous и N блоков (показать всех, не выбирать).
CASES_CATS = [
    ("что не ест пончик", ["p_dis"], "direct", {}),                         # прямой запрос по cat_id + тип
    ("что важно про туча", ["t_imp"], "direct", {}),                        # по кличке
    ("что любит пончику", ["p_like"], "direct", {}),                        # падеж
    ("какие лекарства у пончика", ["p_med"], "hybrid_cat", {}),             # тип не распознан → гибрид внутри фактов кота
    ("что не ест тучка", ["t_dis", "tp_dis"], "direct", {"ambiguous": 2}),  # кот = человек: показать обоих
    ("что любит тучка", ["t_like", "tp_like"], "direct", {"ambiguous": 2}),
    ("что не ест мурка", [], "unknown_person", {}),                         # такого кота нет
    ("что не ест рыжик", [], "unknown_person", {}),                         # карточка убрана: факт не всплывает
    ("что важно про пончика", [], "direct", {}),                            # важных фактов нет → честное «не знаю»
    ("чего боится кот", ["cat_general"], "hybrid", {}),                     # имени нет, факт без привязки
]


@contextmanager
def world():
    ids: Dict[str, int] = {}
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == SYN_USER))
        c.execute(delete(people).where(people.c.user_id == SYN_USER))
        c.execute(delete(cats).where(cats.c.user_id == SYN_USER))
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
        cat_names = {}
        for key, name, aliases, status in CATS:
            cid = c.execute(insert(cats).values(user_id=SYN_USER, name=name, aliases=aliases, status=status)
                            .returning(cats.c.id)).scalar_one()
            crepo.sync_norm(c, cid)
            ids[key], cat_names[key] = cid, name
        for key, cat, typ, text in CAT_FACTS:
            cid = ids.get(cat) if cat else None
            ids[key] = c.execute(insert(memories).values(
                fact_text=text, key_name=cat_fact_key(cid, typ, text) if cid else "", category="🐾 Коты", cat_id=cid,
                related_to=cat_names.get(cat, ""), user_id=SYN_USER, review_status="confirmed")
                .returning(memories.c.id)).scalar_one()
    try:
        yield ids
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == SYN_USER))
            c.execute(delete(people).where(people.c.user_id == SYN_USER))
            c.execute(delete(cats).where(cats.c.user_id == SYN_USER))
