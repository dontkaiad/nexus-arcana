"""tests/test_arcana_memory_list_callbacks.py — каждая кнопка списка найденных записей (и «Все факты») имеет обработчик
в ОБОИХ ботах. Регрессия: в Arcana чекбоксы не работали — обработчики были только в Nexus.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.memory as mem
from core.repos.pg_memory_repo import Memory


def _callbacks():
    pages = [Memory(id="1", fact="a", is_current=True), Memory(id="2", fact="b", is_current=False)]
    uid = 777
    mem._mem_selected[uid] = {"1", "2"}
    kb = mem._build_delete_keyboard(uid, pages, reactivate_cb="mem_reactivate_selected")
    mem._mem_selected.pop(uid, None)
    return [b.callback_data for row in kb.inline_keyboard for b in row] + ["memall:abcd1234"]


def _handled(router, data: str) -> bool:
    call = SimpleNamespace(data=data)
    # свой loop, НЕ asyncio.run(): тот сбрасывает текущий loop процесса, и соседние тесты с get_event_loop() падают
    loop = asyncio.new_event_loop()
    try:
        for h in router.callback_query.handlers:
            ok, _ = loop.run_until_complete(h.check(call))
            if ok:
                return True
        return False
    finally:
        loop.close()


@pytest.mark.parametrize("bot", ["nexus", "arcana"])
def test_every_list_button_has_a_handler(bot):
    if bot == "nexus":
        from nexus.handlers.memory import router
    else:
        from arcana.handlers.memory import router
    datas = _callbacks()
    assert len(datas) >= 6
    missing = [d for d in datas if not _handled(router, d)]
    assert missing == [], f"{bot}: нет обработчика для {missing}"


def test_arcana_reuses_the_nexus_functions_not_copies():
    from arcana.handlers import memory as arc
    from nexus.handlers import memory as nx
    funcs = {h.callback for h in arc.router.callback_query.handlers}
    assert nx.cb_mem_toggle in funcs and nx.cb_mem_deactivate_selected in funcs and nx.cb_mem_cancel in funcs
