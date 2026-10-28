"""tests/test_people_fix.py — странные карточки: пустое имя, ник владельца, удалённые аккаунты.

Классификация чата (человек / «Я» / удалённый аккаунт), загрузчики, план и применение на тестовой БД.
Фикстуры обезличены.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, text

from core.db import get_engine
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tools.people_import import fix_people as fx
from tools.people_import.chats import (
    Chat, Msg, classify_chat, is_deleted_like, load_tg, parse_vk_page, usable_name,
)
from tools.people_import.selfblock import SelfBlock

MARK = "test-people-fix"
NICK = "Старый Ник"


def sb(tg=(), vk=()):
    s = SelfBlock()
    s.add_names(["Кай", "Kai", "Kai Lark", "Вы", NICK])
    s.tg_ids |= set(tg)
    s.vk_ids |= set(vk)
    return s


def chat(source="tg", cid=7, title="", names=None):
    return Chat(source, cid, title, [Msg("partner", "2021-01-01", "x")], Counter(names or {}))


# ── классификация ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,ok", [("Имя1 Фамилия1", True), ("", False), ("   ", False), ("🌸", False), ("…", False),
                                      ("Deleted Account", False), ("удалённый аккаунт", False), ("Имя1", True),
                                      ("Удалённый аккаунт · VK", True)])
def test_usable_name(name, ok):
    assert usable_name(name) is ok
    assert is_deleted_like("Deleted Account") and not is_deleted_like("Делия")


def test_my_alias_is_not_usable_when_self_name_check_given():
    assert not usable_name(NICK, sb().is_self_name) and not usable_name("старый   ник!", sb().is_self_name)


def test_classify_title_ok_wins():
    assert classify_chat(chat(title="Имя1 Фамилия1", names={"Другое Имя": 5}), sb()) == ("person", "Имя1 Фамилия1")


def test_classify_empty_or_emoji_title_takes_name_from_partner_messages():
    assert classify_chat(chat("vk", 5, "", {"Имя2 Фамилия2": 9, "Ошибка": 1}), sb()) == ("person", "Имя2 Фамилия2")
    assert classify_chat(chat(title="🌸", names={"Имя3": 2}), sb()) == ("person", "Имя3")


def test_classify_alias_title_with_other_real_name_is_a_person():
    assert classify_chat(chat(title=NICK, names={"Реальный Человек": 4}), sb()) == ("person", "Реальный Человек")


def test_classify_alias_title_without_other_name_is_self_chat():
    assert classify_chat(chat(title=NICK, names={NICK: 3}), sb()) == ("self", "")
    assert classify_chat(chat(title=NICK), sb()) == ("self", "")


def test_classify_my_own_id_is_self_even_with_normal_title():
    assert classify_chat(chat(cid=111, title="Нормальное"), sb(tg=[111])) == ("self", "")
    assert classify_chat(chat("vk", 888, "Нормальное"), sb(vk=[888])) == ("self", "")


def test_classify_deleted_account_fallback_names():
    assert classify_chat(chat(title="", names={}), sb()) == ("person", "Удалённый аккаунт · TG")
    assert classify_chat(chat("vk", 5, "DELETED", {"Deleted Account": 3}), sb()) == ("person", "Удалённый аккаунт · VK")


# ── загрузчики ──────────────────────────────────────────────────────────────

def test_load_tg_keeps_nameless_personal_chat_and_skips_saved_messages(tmp_path):
    data = {"personal_information": {"user_id": 5}, "chats": {"list": [
        {"type": "personal_chat", "id": 7, "name": "", "messages": [
            {"type": "message", "from_id": "user7", "from": "Имя Партнёра", "date": "2022-01-01T10:00:00", "text": "привет"},
            {"type": "message", "from_id": "user5", "from": "Я", "date": "2022-01-01T10:01:00", "text": "ответ"}]},
        {"type": "saved_messages", "id": 5, "name": "Saved", "messages": [{"type": "message", "from_id": "user5", "text": "заметка"}]},
        {"type": "bot_chat", "id": 9, "name": "Бот", "messages": []}]}}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    chats = load_tg(p)
    assert list(chats) == [7] and chats[7].title == ""
    assert dict(chats[7].partner_names) == {"Имя Партнёра": 1}
    assert classify_chat(chats[7], sb()) == ("person", "Имя Партнёра")


VK_NO_CRUMB = """
<div class="message" data-id="1"><div class="message__header"><a href="https://vk.ru/id100">Имя4 Фамилия4</a>, 12 мар 2021 в 17:53:19</div>
<div>текст<div class="kludges"></div></div></div>
<div class="message" data-id="2"><div class="message__header">Вы, 12 мар 2021 в 17:54:02</div><div>ответ<div class="kludges"></div></div></div>
<div class="message" data-id="3"><div class="message__header"><a href="https://vk.ru/id999">Чужой</a>, 1 янв 2020 в 10:00:00</div><div>x<div class="kludges"></div></div></div>
"""


def test_vk_page_without_title_still_yields_partner_name_from_header_link():
    names = Counter()
    parse_vk_page(VK_NO_CRUMB, 100, names)
    assert dict(names) == {"Имя4 Фамилия4": 1}                               # чужая ссылка и «Вы» не в счёт


# ── план ────────────────────────────────────────────────────────────────────

def person(id_, name, tg=None, vk=None, facts=1, nonpending=0, bday=False):
    return {"id": id_, "name": name, "tg_id": tg, "vk_id": vk, "facts": facts, "nonpending_facts": nonpending, "has_birthday": bday}


def test_plan_covers_every_case_and_counts():
    chats = {"tg": {7: chat("tg", 7, NICK, {NICK: 2}), 8: chat("tg", 8, "🌸", {"Имя3": 3}), 9: chat("tg", 9, "", {})},
             "vk": {5: chat("vk", 5, "", {"Имя2 Фамилия2": 4}), 6: chat("vk", 6, "", {"Имя6": 1})}}
    people_ = [person(1, NICK, tg=7), person(2, "🌸", tg=8), person(3, "", tg=9), person(4, "", vk=5),
               person(5, "", vk=6, facts=0), person(6, "", vk=6, facts=0, bday=True),
               person(7, "", vk=5, nonpending=2), person(8, "Нормальное Имя", tg=8), person(9, "", vk=404)]
    plan = fx.plan_fixes(chats, people_, sb())
    by = {a["id"]: a for a in plan["actions"]}
    assert by[1]["action"] == "to_self"                                       # ник владельца
    assert (by[2]["action"], by[2]["name"]) == ("rename", "Имя3")             # значок → имя из реплик
    assert (by[3]["action"], by[3]["name"], by[3]["deleted_account"]) == ("rename", "Удалённый аккаунт · TG", True)
    assert (by[4]["action"], by[4]["name"]) == ("rename", "Имя2 Фамилия2")
    assert by[5]["action"] == "delete_empty"                                  # ни фактов, ни ДР → удалить
    assert (by[6]["action"], by[6]["name"]) == ("rename", "Имя6")             # без фактов, но с ДР → переименовать
    assert 8 not in by                                                        # нормальное имя не трогаем
    assert by[7]["action"] == "skip_protected" and by[9]["action"] == "skip_no_chat"


# ── применение на БД ────────────────────────────────────────────────────────

@pytest.fixture
def db():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def mk_person(name, **kw):
    with get_engine().begin() as c:
        return c.execute(insert(people).values(user_id=MARK, name=name, **kw).returning(people.c.id)).scalar_one()


def mk_fact(pid, h8="aaaaaaaa", type_="likes", status="pending", source="auto", text_="факт", **kw):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text_, key_name="person_%d_%s_%s" % (pid, type_, h8), category="👥 Люди", person_id=pid,
            review_status=status, source=source, user_id=MARK, **kw).returning(memories.c.id)).scalar_one()


def state(pid):
    with get_engine().connect() as c:
        p = c.execute(select(people).where(people.c.id == pid)).first()
        m = c.execute(select(memories).where(memories.c.person_id == pid)).all()
    return (dict(p._mapping) if p else None), m


def test_rename_to_self_delete_and_dry_run(db):
    me = mk_person("Кай", is_self=True, status="confirmed", tg_id=1)
    ren = mk_person("", vk_id=11); mk_fact(ren, "11111111")
    own = mk_person(NICK, tg_id=22); mk_fact(own, "22222222"); mk_fact(own, "33333333", type_="important")
    empty = mk_person("", vk_id=33)
    plan = {"actions": [{"id": ren, "action": "rename", "name": "Имя2 Фамилия2"}, {"id": own, "action": "to_self"},
                        {"id": empty, "action": "delete_empty"}]}
    dry = fx.apply_plan(MARK, plan, dry=True)
    assert dry["rename"] == 1 and dry["to_self"] == 1 and dry["delete_empty"] == 1
    assert state(ren)[0]["name"] == "" and state(own)[0] and state(empty)[0]            # dry-run ничего не записал
    res = fx.apply_plan(MARK, plan, dry=False)
    assert res["rename"] == 1 and res["to_self"] == 1 and res["delete_empty"] == 1 and res["facts_moved_to_self"] == 2
    assert state(ren)[0]["name"] == "Имя2 Фамилия2"
    assert state(own)[0] is None and state(empty)[0] is None
    keys = sorted(m.key_name for m in state(me)[1])
    assert keys == ["person_%d_important_33333333" % me, "person_%d_likes_22222222" % me]   # перевешены и перекеены на «Я»
    with get_engine().connect() as c:                                                    # related_to ренейма обновлён
        assert c.execute(text("SELECT related_to FROM memories WHERE person_id=:p"), {"p": ren}).scalar_one() == "Имя2 Фамилия2"


def test_confirmed_and_manual_are_never_touched(db):
    mk_person("Кай", is_self=True, status="confirmed")
    p = mk_person(NICK, tg_id=22); mk_fact(p, "22222222"); keep = mk_fact(p, "44444444", status="confirmed")
    with pytest.raises(ValueError):
        prepo.absorb_into_self(MARK, p)                                                  # защита на уровне репозитория
    res = fx.apply_plan(MARK, {"actions": [{"id": p, "action": "to_self"}]}, dry=False)
    assert res.get("skip_protected") == 1 and state(p)[0] and len(state(p)[1]) == 2 and keep
    confirmed_person = mk_person("", vk_id=5, status="confirmed")
    res = fx.apply_plan(MARK, {"actions": [{"id": confirmed_person, "action": "rename", "name": "Х"}]}, dry=False)
    assert res["skip_changed"] == 1 and state(confirmed_person)[0]["name"] == ""         # подтверждённого человека не переименовываем


def test_absorb_without_self_card_is_a_noop(db):
    p = mk_person(NICK, tg_id=22); mk_fact(p)
    assert prepo.absorb_into_self(MARK, p) is None and state(p)[0]


def test_delete_empty_refuses_when_facts_appeared_since_plan(db):
    mk_person("Кай", is_self=True, status="confirmed")
    p = mk_person("", vk_id=33); mk_fact(p)
    res = fx.apply_plan(MARK, {"actions": [{"id": p, "action": "delete_empty"}]}, dry=False)
    assert res["skip_changed"] == 1 and state(p)[0]


def test_dedupe_self_in_db_uses_clean_text_embeddings_and_keeps_totals(db):
    me = mk_person("Кай", is_self=True, status="confirmed")
    a = mk_fact(me, "aaaa0001", text_="любит чай", mention_count=2, first_mention_date=date(2019, 1, 1),
                last_mention_date=date(2020, 1, 1))
    b = mk_fact(me, "aaaa0002", text_="обожает чай", mention_count=3, first_mention_date=date(2021, 1, 1),
                last_mention_date=date(2023, 6, 1))
    c_ = mk_fact(me, "aaaa0003", text_="любит футбол", mention_count=1, last_mention_date=date(2022, 1, 1))
    emb = {"любит чай": [1, 0.01], "обожает чай": [1, 0.02], "любит футбол": [0, 1]}
    fake = lambda texts: [emb[t] for t in texts]
    assert fx.dedupe_self_in_db(MARK, 0.9, True, fake) == {"merged_groups": 1, "removed": 1}
    assert len(state(me)[1]) == 3
    assert fx.dedupe_self_in_db(MARK, 0.9, False, fake) == {"merged_groups": 1, "removed": 1}
    rows = {m.id: m for m in state(me)[1]}
    assert set(rows) == {b, c_} and a not in rows                                        # представитель — самое свежее упоминание
    assert (rows[b].mention_count, rows[b].first_mention_date, rows[b].last_mention_date) == (5, date(2019, 1, 1), date(2023, 6, 1))
    assert fx.dedupe_self_in_db(MARK, 0.9, False, fake)["removed"] == 0                 # идемпотентно
    assert fx.dedupe_self_in_db(MARK, 0.9, False, lambda t: [])["skipped_no_embeddings"] == 1   # нет эмбеддингов — ничего не трогаем


def test_apply_without_to_self_never_touches_self_facts(db):
    """Регрессия: dry-run на проде показал, что дедуп «Я» запускался всегда и склеил бы 782 факта."""
    me = mk_person("Кай", is_self=True, status="confirmed")
    for i in range(4):
        mk_fact(me, "bbbb000%d" % i, text_="факт %d" % i)
    ren = mk_person("", vk_id=11); mk_fact(ren, "11111111")
    boom = lambda texts: (_ for _ in ()).throw(AssertionError("эмбеддинги не должны запрашиваться"))
    for dry in (True, False):
        res = fx.apply_plan(MARK, {"actions": [{"id": ren, "action": "rename", "name": "Имя"}]}, dry, embed=boom)
        assert res["rename"] == 1 and not any(k.startswith("dedupe_") for k in res)
    assert len(state(me)[1]) == 4
