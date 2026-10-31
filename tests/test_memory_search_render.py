"""tests/test_memory_search_render.py — бот-ответ search_memory для структурных маршрутов (этап 2).

answer() замокан: проверяем только рендер и поведение «не знаю» / «не нашла в Людях» / тёзки.
Privacy: вымышленные имена.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core import memory as mem
from core.memory_search import Group, Hit, Result
from core.repos.pg_memory_repo import Memory


def _msg():
    m = MagicMock()
    m.from_user.id = 7
    m.answer = AsyncMock()
    return m


def _hit(i, fact, typ="dislikes", pid=3):
    m = Memory(id=str(i), fact=fact, key=f"person_{pid}_{typ}_0000000{i}", category="👥 Люди", person_id=pid)
    return Hit(memory_id=i, score=0.0, memory=m)


def _run(res, del_prefix="mem_del"):
    msg = _msg()
    with patch.object(mem._memory_search, "answer", AsyncMock(return_value=res)), \
         patch.object(mem, "_search_finance", AsyncMock(return_value=[])):
        asyncio.run(mem.search_memory(msg, "что не любит оля", "u1", del_prefix=del_prefix))
    return msg.answer.await_args


def test_direct_facts_render_with_group_label_and_type_marks():
    res = Result(route="direct", kind="facts", subject="person", types=("important", "dislikes"),
                 groups=[Group(label="Ольга (подруга)", person_id=3,
                               hits=[_hit(1, "аллергия на орехи", "important"), _hit(2, "не любит шум")])])
    call = _run(res)
    text = call.args[0]
    assert "Ольга (подруга)" in text and "⚠️ аллергия на орехи" in text and "🚫 не любит шум" in text
    assert call.kwargs.get("reply_markup") is not None


def test_two_groups_for_homonyms():
    res = Result(route="direct", kind="facts", subject="ambiguous", types=("likes",), groups=[
        Group(label="Михаил Тестов", person_id=1, hits=[_hit(1, "любит джаз", "likes", 1)]),
        Group(label="Миша Образцов", person_id=2, hits=[_hit(2, "любит рок", "likes", 2)])])
    text = _run(res).args[0]
    assert "Михаил Тестов" in text and "Миша Образцов" in text and text.index("джаз") < text.index("Миша Образцов")


def test_direct_empty_is_honest_dont_know():
    res = Result(route="direct", kind="none", subject="person", types=("important", "dislikes"),
                 groups=[Group(label="Ольга", person_id=3)])
    text = _run(res).args[0]
    assert "Не знаю" in text and "Ольга" in text and "нет подтверждённых фактов" in text


def test_unknown_person_gendered_by_bot():
    res = Result(route="unknown_person", kind="unknown_person", subject="unknown_person", unknown_name="ваня")
    assert "Не нашла в Людях «ваня»" in _run(res, "arcmem_del").args[0]
    assert "Не нашёл в Людях «ваня»" in _run(res, "mem_del").args[0]


def test_birthday_render_and_missing():
    ok = Result(route="birthday", kind="birthday", subject="person",
                groups=[Group(label="Ольга", person_id=3, birthday="ДР: 05.03")])
    assert "ДР: 05.03" in _run(ok).args[0]
    no = Result(route="birthday", kind="none", subject="person", groups=[Group(label="Ольга", person_id=3)])
    assert "ДР не указан" in _run(no).args[0]


def test_html_in_labels_and_facts_is_escaped():
    res = Result(route="direct", kind="facts", subject="person", types=("likes",),
                 groups=[Group(label="<b>x</b>", person_id=3, hits=[_hit(1, "a < b & c", "likes")])])
    text = _run(res).args[0]
    assert "&lt;b&gt;x&lt;/b&gt;" in text and "a &lt; b &amp; c" in text
