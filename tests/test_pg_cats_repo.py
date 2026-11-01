"""tests/test_pg_cats_repo.py — Cats subject, step 3: репо cats + resolve_cat.

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL). Имена вымышленные, строки помечены MARK.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from core.db import get_engine
from core.repos import pg_cats_repo as repo
from core.repos.cats_tables import cats

MARK = "test-cats-step3"
OTHER = "test-cats-step3-other"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


def _ids(found):
    return [c["id"] for c in found]


def test_create_fills_aliases_norm_and_get():
    cid = repo.create(MARK, "Тучка", ["Туча"])
    cat = repo.get_cat(MARK, cid)
    assert cat["name"] == "Тучка" and cat["aliases"] == ["Туча"]
    assert cat["aliases_norm"] and cat["status"] == "confirmed" and cat["photo_url"] is None


def test_create_rejects_empty_name_and_bad_status():
    with pytest.raises(ValueError):
        repo.create(MARK, "  ")
    with pytest.raises(ValueError):
        repo.create(MARK, "Тучка", status="weird")


def test_resolve_by_name_and_case_forms():
    cid = repo.create(MARK, "Тучка")
    for mention in ("Тучка", "тучка", "Тучке", "Тучку", "Тучки"):
        assert _ids(repo.resolve_cat(MARK, [mention])) == [cid], mention


def test_resolve_by_alias():
    cid = repo.create(MARK, "Пончик", ["Пончо"])
    assert _ids(repo.resolve_cat(MARK, ["Пончо"])) == [cid]


def test_resolve_short_name_declensions_without_false_matches():
    kex = repo.create(MARK, "Кекс")
    repo.create(MARK, "Кексон")                      # другое имя с общим началом — не тёзка Кекса
    assert _ids(repo.resolve_cat(MARK, ["Кексу"])) == [kex]


def test_resolve_unknown_and_generic_word_return_nothing():
    repo.create(MARK, "Тучка")
    assert repo.resolve_cat(MARK, ["Рыжик"]) == []
    assert repo.resolve_cat(MARK, ["кот"]) == []
    assert repo.resolve_cat(MARK, [""]) == []
    assert repo.resolve_cat(MARK, []) == []


def test_resolve_two_cats_with_same_name_returns_both_caller_decides():
    a = repo.create(MARK, "Тучка")
    b = repo.create(MARK, "Тучка")
    assert _ids(repo.resolve_cat(MARK, ["Тучка"])) == [a, b]


def test_resolve_is_scoped_by_user_and_skips_rejected():
    repo.create(OTHER, "Тучка")
    assert repo.resolve_cat(MARK, ["Тучка"]) == []
    cid = repo.create(MARK, "Пончик")
    repo.update_cat(MARK, cid, {"status": "rejected"})
    assert repo.resolve_cat(MARK, ["Пончик"]) == []


def test_update_name_and_aliases_resync_norm():
    cid = repo.create(MARK, "Тучка")
    assert repo.resolve_cat(MARK, ["Рыжик"]) == []
    repo.update_cat(MARK, cid, {"name": "Рыжик", "aliases": ["Рыжуля"]})
    assert _ids(repo.resolve_cat(MARK, ["Рыжику"])) == [cid]
    assert _ids(repo.resolve_cat(MARK, ["Рыжуля"])) == [cid]
    assert repo.resolve_cat(MARK, ["Тучка"]) == []             # старое имя больше не находится


def test_update_ignores_unknown_fields_and_other_users_cat():
    cid = repo.create(MARK, "Тучка")
    repo.update_cat(MARK, cid, {"user_id": OTHER, "id": 1, "photo_url": "https://x/y.jpg"})
    cat = repo.get_cat(MARK, cid)
    assert cat["user_id"] == MARK and cat["photo_url"] == "https://x/y.jpg"
    assert repo.update_cat(OTHER, cid, {"name": "Чужой"}) is None
    assert repo.get_cat(MARK, cid)["name"] == "Тучка"


def test_list_cats_hides_rejected_and_cat_names_collects_aliases():
    a = repo.create(MARK, "Тучка", ["Туча"])
    b = repo.create(MARK, "Пончик")
    c = repo.create(MARK, "Рыжик")
    repo.update_cat(MARK, c, {"status": "rejected"})
    assert _ids(repo.list_cats(MARK)) == [a, b]
    assert repo.cat_names(MARK) == ["Тучка", "Туча", "Пончик"]
    assert repo.cat_names(OTHER) == []
