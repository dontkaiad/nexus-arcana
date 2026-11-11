"""tests/test_sort_cat_blocks.py — разовая раскладка существующих фактов по блокам: план, защита, запись одной транзакцией, откат.

Реальная тестовая БД для записи; модель подменена (ask). Имена вымышленные.
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from sqlalchemy import delete, insert, text

from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.memories_table import memories
from scripts import sort_cat_blocks as sb

MARK = "test-sort-cat-blocks"
OTHER = "test-sort-cat-blocks-other"
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        c.execute(text("DELETE FROM cats WHERE user_id IN (:a, :b)"), {"a": MARK, "b": OTHER})


def row(i, t, cat_id=None, person_id=None, key="", courses=0):
    return {"id": i, "fact_text": t, "key_name": key, "cat_id": cat_id, "person_id": person_id, "courses": courses}


def mk(t, user=MARK, **kw):
    vals = dict(fact_text=t, key_name="", category=CATEGORY, related_to="", user_id=user, review_status="confirmed")
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


def blk(i):
    with get_engine().connect() as c:
        return c.execute(text("SELECT cat_block FROM memories WHERE id = :i"), {"i": i}).scalar_one()


# ── разбор ответа модели ─────────────────────────────────────────────────────

def test_parse_answer_accepts_exact_values_and_closes_everything_doubtful():
    raw = json.dumps([{"id": 1, "блок": "care", "уверен": True}, {"id": 2, "блок": "Care", "уверен": True}, {"id": 3, "блок": "уход", "уверен": True},
                      {"id": 4, "блок": "profile", "уверен": "true"}, {"id": 5, "блок": "owner", "уверен": False},
                      {"id": 6, "блок": "care", "уверен": True}, {"id": 6, "блок": "profile", "уверен": True}, {"id": "7", "блок": "care", "уверен": True},
                      "мусор", {"блок": "care"}, {"id": True, "блок": "care", "уверен": True}])
    got = sb.parse_answer(raw, [1, 2, 3, 4, 5, 6, 7, 8])
    assert got[1] == ("care", True) and got[2] == ("care", True)
    assert got[3] == ("unsorted", True)                 # блок вне списка
    assert got[4] == ("profile", False)                 # «true» строкой — не уверен
    assert got[5] == ("owner", False)
    assert got[6] == ("unsorted", False)                # id дважды — не угадываем
    assert got[7] == ("unsorted", False) and got[8] == ("unsorted", False)    # нет ответа
    assert list(got) == [1, 2, 3, 4, 5, 6, 7, 8]


@pytest.mark.parametrize("raw", ["", "не json", "{}", "null", "[1, 2]", "```json\n[]\n```"])
def test_parse_answer_garbage_closes_all(raw):
    assert sb.parse_answer(raw, [1, 2]) == {1: ("unsorted", False), 2: ("unsorted", False)}


def test_prompt_lists_every_fact_and_every_block_definition():
    system, user = sb.build_prompt([row(1, "рыжая  и\nтрёхлетняя"), row(2, "не ест сухой")])
    assert user == "1: рыжая и трёхлетняя\n2: не ест сухой"
    for b in ("profile", "care", "health", "relations", "owner", "unsorted"):
        assert b in system


# ── решение по одному факту ──────────────────────────────────────────────────

def test_person_card_fact_is_owner_without_asking_the_model():
    d = sb.decide(row(1, "обожает обезболивающие", person_id=5), ("care", True))
    assert (d.block, d.source, d.audience) == ("owner", "правило", "owner")


def test_sure_sitter_block_is_accepted_and_unsure_is_closed():
    assert sb.decide(row(1, "не ест сухой корм"), ("care", True)).audience == "sitter"
    d = sb.decide(row(1, "не ест сухой корм"), ("care", False))
    assert (d.block, d.source, d.audience) == ("unsorted", "сомнение", "owner")
    assert sb.decide(row(1, "x"), None).block == "unsorted"


def test_guard_turns_money_plans_and_feelings_into_owner_even_if_the_model_opened_them():
    for t in ("ветеринар обошёлся в 12 тысяч", "хочу завести ещё одну", "планирую второго кота", "я переживаю за него", "лечение стоит 5000 ₽"):
        d = sb.decide(row(1, t), ("care", True))
        assert (d.block, d.source, d.audience) == ("owner", "защита", "owner"), t


def test_guard_closes_secrets_codes_links_and_long_numbers():
    for t in ("пароль от поилки 1234", "код домофона", "см. https://example.invalid/x", "телефон 89990001122"):
        d = sb.decide(row(1, t), ("care", True))
        assert (d.block, d.audience) == ("unsorted", "owner"), t


def test_medicine_facts_become_health_and_stay_closed_unless_critical():
    d = sb.decide(row(1, "нужен препарат от тревоги", cat_id=1), ("care", True))
    assert (d.block, d.source, d.audience) == ("health", "защита", "owner")
    d = sb.decide(row(1, "нельзя таблетки с парацетамолом", cat_id=1, key="cat_1_important_aaaaaaaa"), ("health", True))
    assert (d.block, d.kind, d.audience) == ("health", "important", "sitter")        # «критично» + health: ситтер видит
    assert sb.decide(row(1, "даём таблетки от давления", cat_id=1), ("owner", True)).block == "owner"   # владелец-блок модели не открываем


def test_a_model_owner_block_on_a_money_fact_stays_the_models_own_answer():
    d = sb.decide(row(1, "ветеринар обошёлся в 12 тысяч"), ("owner", True))
    assert (d.block, d.source, d.note) == ("owner", "модель", "")


def test_owner_override_wins_over_everything():
    d = sb.decide(row(1, "хочу завести ещё одну", person_id=3), ("care", True), override="care")
    assert (d.block, d.source) == ("care", "владелец")
    assert sb.decide(row(1, "x"), ("care", True), override="sitter").block == "unsorted"       # неизвестное значение не открывает


def test_kind_is_taken_from_the_key_then_from_the_text():
    assert sb.decide(row(1, "что угодно", key="cat_1_important_aaaaaaaa"), ("health", True)).audience == "sitter"
    assert sb.decide(row(1, "аллергия на курицу"), ("health", True)).audience == "sitter"      # старый факт без ключа: «аллерги» → important
    assert sb.decide(row(1, "спит на окне"), ("health", True)).audience == "owner"


# ── таблица и отчёт о лекарствах ─────────────────────────────────────────────

def test_table_shows_id_block_audience_and_totals():
    plan = sb.make_plan([row(1, "рыжая | трёхлетняя", cat_id=7), row(2, "личное", person_id=1), row(3, "странный", cat_id=7)],
                        {1: ("profile", True), 3: ("unsorted", False)})
    out = sb.render_table(plan)
    assert "| 1 | 7 | other | profile | sitter | модель | рыжая / трёхлетняя |" in out
    assert "| 2 | общее | other | owner | owner | правило" in out
    assert "Итого 3: ситтер увидит 1, закрыто 2." in out and "profile 1" in out and "unsorted 1" in out


def test_medicine_report_says_what_happens_with_the_need_a_drug_fact():
    p = sb.make_plan([row(1, "нужен препарат от тревоги", cat_id=4, courses=0), row(2, "дать таблетку утром", cat_id=5, courses=1),
                      row(3, "рыжая", cat_id=4)], {1: ("health", True), 2: ("health", True), 3: ("profile", True)})
    rep = sb.med_report(p)
    assert "#1 → блок health, аудитория owner; курса в cat_medications нет; нужен курс" in rep
    assert "#2 → блок health, аудитория owner; есть действующих курсов у кота: 1" in rep and "нужен курс" not in rep.split("#2")[1]
    assert "#3" not in rep
    p = sb.make_plan([row(1, "нужен габапентин", cat_id=4), row(2, "кот принимает таблетки", person_id=9)], {1: ("health", True)})   # у первого слов про лекарство нет
    rep = sb.med_report(p)
    assert "#1 → блок health, аудитория owner; курса в cat_medications нет; нужен курс" in rep
    assert "#2 → блок owner, аудитория owner; без привязки к коту: курс не определить" in rep
    assert sb.med_report(sb.make_plan([row(1, "рыжая")], {1: ("profile", True)})) == "фактов про лекарства у котов не найдено"


# ── dry-run целиком (без БД) ─────────────────────────────────────────────────

def test_dry_run_from_json_asks_only_for_rows_without_person_or_override_prints_and_saves_a_private_plan(tmp_path, capsys):
    src = tmp_path / "rows.json"
    src.write_text(json.dumps({"rows": [row(1, "рыжая", cat_id=1), row(2, "личное", person_id=3), row(3, "странный", cat_id=1), row(4, "не ест сухой", cat_id=1)]}))
    seen = []

    async def ask(rows):
        seen.extend(r["id"] for r in rows)
        return {1: ("profile", True), 4: ("care", True)}

    plan = tmp_path / "plan.json"
    rc = sb.main(["--user-id", MARK, "--from-json", str(src), "--override", "3=unsorted", "--save-plan", str(plan)], ask=ask)
    out = capsys.readouterr().out
    assert rc == 0 and seen == [1, 4]
    assert "DRY-RUN: в базу ничего не записано" in out and "| 2 | общее | other | owner | owner | правило" in out
    assert stat.S_IMODE(os.stat(plan).st_mode) == 0o600
    saved = json.loads(plan.read_text())
    assert saved["user_id"] == MARK and [(i["id"], i["block"]) for i in saved["items"]] == [(1, "profile"), (2, "owner"), (3, "unsorted"), (4, "care")]


def test_dry_run_does_not_write_to_the_database(tmp_path, capsys):
    a = mk("кот боится пылесоса")

    async def ask(rows):
        return {a: ("care", True)}

    assert sb.main(["--user-id", MARK], ask=ask) == 0
    assert blk(a) is None and "| %d |" % a in capsys.readouterr().out


def test_apply_refuses_without_plan_rollback_or_with_from_json(tmp_path):
    for argv in (["--user-id", MARK, "--apply"], ["--user-id", MARK, "--apply", "--plan", "p.json"],
                 ["--user-id", MARK, "--apply", "--plan", "p.json", "--rollback", "r.json", "--from-json", "x.json"]):
        with pytest.raises(SystemExit):
            sb.main(argv)
    with pytest.raises(SystemExit):
        sb.main(["--user-id", MARK, "--from-json", "x.json", "--override", "1=sitter"])


# ── выборка ──────────────────────────────────────────────────────────────────

def test_load_rows_takes_only_current_unblocked_cat_facts_of_this_owner_and_counts_courses():
    cid = crepo.create(MARK, "Тучка")
    keep = mk("рыжая", cat_id=cid)
    card = mk("про кота у человека", person_id=None)
    skipped = [mk("уже с блоком", cat_block="care"), mk("архив", is_archived=True), mk("не актуально", is_current=False), mk("отклонён", review_status="rejected"),
               mk("", ), mk("чужой", user=OTHER), mk("не про котов", category="🏠 Быт")]
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO cat_medications (cat_id, user_id, name, starts_on, status) VALUES (:c, :u, 'капли', current_date, 'active')"), {"c": cid, "u": MARK})
        for name, starts, ends, status in (("мазь", "current_date", "NULL", "done"), ("старые", "current_date - 30", "current_date - 1", "active"),
                                           ("будущие", "current_date + 5", "NULL", "active"), ("отменены", "current_date", "NULL", "cancelled")):
            c.execute(text("INSERT INTO cat_medications (cat_id, user_id, name, starts_on, ends_on, status) VALUES (:c, :u, :n, %s, %s, :s)" % (starts, ends)),
                      {"c": cid, "u": MARK, "n": name, "s": status})
    got = {r["id"]: r for r in sb.load_rows(get_engine(), MARK)}
    assert set(got) == {keep, card} and got[keep]["courses"] == 1 and got[card]["courses"] == 0
    assert not set(skipped) & set(got)


# ── запись и откат ───────────────────────────────────────────────────────────

def _plan(items, user=MARK):
    return {"user_id": user, "items": items}


def _item(i, text_, block):
    return {"id": i, "sha": sb.Decision(i, text_, "other", None, block, "модель").sha, "block": block}


def test_apply_writes_in_one_transaction_with_a_private_rollback_file_written_first(tmp_path):
    a, b = mk("рыжая"), mk("не ест сухой")
    rb = tmp_path / "rb.json"
    n = sb.apply_plan(get_engine(), MARK, _plan([_item(a, "рыжая", "profile"), _item(b, "не ест сухой", "care")]), str(rb))
    assert n == 2 and (blk(a), blk(b)) == ("profile", "care")
    assert stat.S_IMODE(os.stat(rb).st_mode) == 0o600
    assert json.loads(rb.read_text())["updated"] == {str(a): {"old": None, "new": "profile"}, str(b): {"old": None, "new": "care"}}


def test_apply_is_all_or_nothing_when_a_row_changed_or_already_has_a_block(tmp_path):
    a, b, c = mk("рыжая"), mk("изменили после dry-run"), mk("уже размечен", cat_block="owner")
    for bad in (_item(b, "было другое", "care"), _item(c, "уже размечен", "care"), _item(999999999, "нет такой", "care")):
        with pytest.raises(RuntimeError):
            sb.apply_plan(get_engine(), MARK, _plan([_item(a, "рыжая", "profile"), bad]), str(tmp_path / ("rb%s.json" % bad["id"])))
        assert blk(a) is None                                        # первая строка тоже не записана
    assert blk(c) == "owner"


def test_apply_refuses_foreign_owner_unknown_block_and_existing_rollback_file(tmp_path):
    a = mk("рыжая")
    with pytest.raises(RuntimeError):
        sb.apply_plan(get_engine(), OTHER, _plan([_item(a, "рыжая", "profile")], user=MARK), str(tmp_path / "x.json"))
    rb = tmp_path / "bad.json"
    with pytest.raises(RuntimeError):
        sb.apply_plan(get_engine(), MARK, _plan([_item(a, "рыжая", "sitter")]), str(rb))
    assert not rb.exists() and blk(a) is None                        # до записи rollback-файла проверяем план
    existing = tmp_path / "e.json"
    existing.write_text("{}")
    with pytest.raises(FileExistsError):
        sb.apply_plan(get_engine(), MARK, _plan([_item(a, "рыжая", "profile")]), str(existing))
    assert blk(a) is None


def test_a_plan_made_for_another_owner_is_refused_even_if_the_ids_exist_there(tmp_path):
    x = mk("чужой", user=OTHER)
    with pytest.raises(RuntimeError, match="другого владельца"):
        sb.apply_plan(get_engine(), OTHER, _plan([_item(x, "чужой", "care")], user=MARK), str(tmp_path / "rb.json"))
    assert blk(x) is None


def test_other_owners_row_with_the_same_id_is_never_touched(tmp_path):
    x = mk("чужой", user=OTHER)
    with pytest.raises(RuntimeError):
        sb.apply_plan(get_engine(), MARK, _plan([_item(x, "чужой", "care")]), str(tmp_path / "rb.json"))
    assert blk(x) is None


def test_undo_restores_only_rows_that_still_hold_the_applied_value(tmp_path):
    a, b = mk("рыжая"), mk("не ест сухой")
    rb = tmp_path / "rb.json"
    sb.apply_plan(get_engine(), MARK, _plan([_item(a, "рыжая", "profile"), _item(b, "не ест сухой", "care")]), str(rb))
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET cat_block = 'owner' WHERE id = :i"), {"i": b})          # владелец потом поправила руками
    data = json.loads(rb.read_text())
    assert sb.undo(get_engine(), MARK, data, apply=False) == 2 and blk(a) == "profile"            # dry-run не пишет
    assert sb.undo(get_engine(), MARK, data, apply=True) == 1
    assert blk(a) is None and blk(b) == "owner"                    # правка владельца осталась
