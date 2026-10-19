"""tests/test_people_extraction.py — извлечение с однозначным автором (ADR-0027, перезапуск).

Фикстуры обезличены (Имя1/Фамилия1), но структура заголовка VK — настоящая: имя собеседника
внутри ссылки <a href="https://vk.ru/id…">, свои реплики подписаны «Вы».
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from unittest.mock import AsyncMock, patch

import pytest

from tools.people_import import extract_facts as ef
from tools.people_import.attribute_quotes import (
    author_of, expected_about, first_person_by_kai, locate, segments, verify,
)
from tools.people_import.chats import Chat, Msg, load_tg, nz, parse_vk_page
from tools.people_import.selfblock import SelfBlock, load_selfblock

PEER = 100

VK_PAGE = """
<div class="item"><div class='item__main'>
<div class="message" data-id="1"><div class="message__header"><a href="https://vk.ru/id100">Имя1 Фамилия1</a>, 12 мар 2021 в 17:53:19</div>
<div>Я обожаю аниме, особенно Гибли<br><br><div class="kludges"></div></div></div></div></div>
<div class="item"><div class='item__main'>
<div class="message" data-id="2"><div class="message__header">Вы, 12 мар 2021 в 17:54:02</div>
<div>У тебя же аллергия на орехи<div class="kludges"></div></div></div></div></div>
<div class="item"><div class='item__main'>
<div class="message" data-id="3"><div class="message__header"><a href="https://vk.ru/id999">Имя9</a>, 1 янв 2020 в 10:00:00</div>
<div>Привет &amp; пока<div class="kludges"></div></div></div></div></div>
"""


# ── парсеры ──────────────────────────────────────────────────────────────────

def test_vk_header_author_comes_from_link_and_vy_is_kai():
    m = parse_vk_page(VK_PAGE, PEER)
    assert [x.author for x in m] == ["partner", "kai", "other"]             # раньше у партнёра был ПУСТОЙ автор
    assert [x.date for x in m] == ["2021-03-12", "2021-03-12", "2020-01-01"]
    assert m[0].text == "Я обожаю аниме, особенно Гибли" and m[2].text == "Привет & пока"


def test_tg_author_by_from_id_of_owner(tmp_path):
    data = {"personal_information": {"user_id": 5, "first_name": "Имя", "last_name": "Фам", "username": "own"},
            "chats": {"list": [
                {"type": "personal_chat", "id": 7, "name": "Имя1", "messages": [
                    {"type": "message", "from_id": "user5", "date": "2022-01-02T10:00:00", "text": "мой текст"},
                    {"type": "message", "from_id": "user7", "date": "2022-01-02T10:01:00", "text": [{"type": "plain", "text": "ответ "}, "собеседника"]},
                    {"type": "message", "from_id": "user8", "date": "2022-01-02T10:02:00", "text": "третий"},
                    {"type": "service", "from_id": "user5", "text": "x"}]},
                {"type": "private_group", "id": 9, "name": "Группа", "messages": []}]}}
    p = tmp_path / "result.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    chats = load_tg(p)
    assert list(chats) == [7] and chats.owner["user_id"] == 5
    assert [(m.author, m.text) for m in chats[7].msgs] == [("kai", "мой текст"), ("partner", "ответ собеседника"), ("other", "третий")]
    assert chats[7].msgs[0].date == "2022-01-02"


# ── окна и подписи ──────────────────────────────────────────────────────────

def _chat(msgs, source="tg", cid=7, title="Имя1 Фам"):
    return Chat(source, cid, title, [Msg(a, d, t) for a, d, t in msgs])


def test_every_line_is_signed_kai_or_sobesednik():
    c = _chat([("partner", "2021-01-01", "привет"), ("kai", "2021-01-01", "я люблю чай"),
               ("partner", "2021-01-02", "а я обожаю кофе"), ("other", "2021-01-02", "ещё"), ("kai", "2021-01-02", "")])
    text = ef.render_window(c.msgs, 0, len(c.msgs))
    assert text.split("\n") == ["Собеседник: привет", "Кай: я люблю чай", "Собеседник: а я обожаю кофе", "Другой: ещё"]
    assert ": привет" in text and not any(line.startswith(":") for line in text.split("\n"))


def test_window_ranges_merge_overlaps_and_use_prefilter():
    msgs = [Msg("partner", "", "x%d" % i) for i in range(20)]
    msgs[5] = Msg("partner", "", "я люблю чай")
    msgs[7] = Msg("kai", "", "у тебя аллергия на орехи")
    msgs[15] = Msg("partner", "", "обожаю кофе")
    assert ef.window_ranges(msgs) == [(3, 10), (13, 18)]


def test_system_prompt_rules():
    s = ef.SYSTEM
    assert "«Кай:»" in s and "«Собеседник:»" in s and "about" in s and "ДОСЛОВНАЯ" in s
    assert "у тебя же аллергия" in s                       # реплика Кай о собеседнике — факт
    assert "кого явно назвали" not in s                    # третьи лица не извлекаются
    assert "Факты о третьих лицах не извлекай" in s


# ── пост-проверка цитат ─────────────────────────────────────────────────────

def test_segments_and_locate_normalize_punctuation_and_prefix():
    assert segments("Собеседник: Я обожаю аниме!!!") == [nz("я обожаю аниме")]
    msgs = [Msg("partner", "2020-01-01", "Я обожаю аниме, особенно Гибли"), Msg("kai", "", "ок")]
    assert locate(msgs, "обожаю аниме особенно гибли") == [0]
    assert locate(msgs, "обожаю футбол и хоккей") == []


def test_verify_prefers_window_and_returns_author_date_chat():
    c = _chat([("partner", "2020-01-01", "люблю чай по утрам"), ("kai", "2021-05-05", "люблю чай по утрам"),
               ("partner", "2022-02-02", "ничего")])
    m = verify(c, "люблю чай по утрам", 1, 3)
    assert m["author"] == "kai" and m["date"] == "2021-05-05" and m["chat_id"] == 7 and m["platform"] == "tg"
    assert verify(c, "люблю чай по утрам")["author"] == "mixed"            # без окна совпали оба автора — неоднозначно
    assert verify(c, "придуманная цитата которой нет в чате") is None


def test_author_of_mixed():
    c = _chat([("partner", "", "общая фраза тут"), ("kai", "", "общая фраза тут")])
    assert author_of(c.msgs, [0, 1]) == "mixed" and author_of(c.msgs, []) == "unknown"


@pytest.mark.parametrize("author,quote,want", [
    ("kai", "я люблю чай", "kai"), ("kai", "у тебя же аллергия на орехи", "partner"),
    ("partner", "я люблю аниме", "partner"), ("partner", "ты же любишь чай", "kai"),
    ("kai", "любит чай", None), ("kai", "я думаю ты любишь чай", None),
])
def test_expected_about(author, quote, want):
    assert expected_about(author, quote) == want


def test_first_person_by_kai():
    assert first_person_by_kai("я люблю чай", "kai")
    assert not first_person_by_kai("я люблю чай", "partner")
    assert not first_person_by_kai("у тебя же аллергия", "kai")


# ── build_records ───────────────────────────────────────────────────────────

SB = SelfBlock()
SB.add_names(["Кай", "Kai", "Kai Lark", "Вы"])


def _items(*rows):
    return [dict(i=0, about=a, fact=f, type=t, quote=q, speaker="x") for a, f, t, q in rows]


CHAT = _chat([("partner", "2021-03-12", "Я обожаю аниме, особенно Гибли"),
              ("kai", "2021-03-12", "У тебя же аллергия на орехи"),
              ("kai", "2021-03-13", "Я люблю чай по утрам"),
              ("partner", "2021-03-13", "Ты же не ешь мясо")])


def test_build_records_keeps_valid_drops_hallucinations_and_mismatches():
    st = Counter()
    items = _items(("partner", "любит аниме", "likes", "Я обожаю аниме, особенно Гибли"),     # ок: партнёр о себе
                   ("partner", "аллергия на орехи", "important", "У тебя же аллергия на орехи"),  # ок: Кай о партнёре
                   ("partner", "любит чай", "likes", "Я люблю чай по утрам"),                  # Кай о СЕБЕ → mismatch
                   ("kai", "любит чай", "likes", "Я люблю чай по утрам"),                       # ок: факт о Кай
                   ("kai", "не ест мясо", "dislikes", "Ты же не ешь мясо"),                     # ок: партнёр о Кай
                   ("partner", "любит футбол", "likes", "Придуманная цитата про футбол"),       # нет в чате
                   ("partner", "тип битый", "weird", "Я обожаю аниме, особенно Гибли"))
    out = ef.build_records(CHAT, [(0, 4)], items, st, SB)
    assert st["quote_not_found"] == 1 and st["about_mismatch"] == 1 and len(out) == 5
    by = {r["fact"]: r for r in out}
    a = by["любит аниме"]
    assert (a["about"], a["name"], a["quote_author"], a["source_message_date"], a["source_chat_id"], a["source"]) == \
        ("partner", "Имя1 Фам", "partner", "2021-03-12", 7, "tg")
    assert a["tg_id"] == 7 and a["vk_id"] is None
    assert by["аллергия на орехи"]["quote_author"] == "kai" and by["аллергия на орехи"]["about"] == "partner"
    s = by["не ест мясо"]
    assert (s["about"], s["name"], s["quote_author"], s["tg_id"]) == ("kai", "Кай", "partner", None)
    assert by["тип битый"]["type"] == "other"


def test_build_records_raw_metric_counts_llm_mislabels_before_check():
    st = Counter()
    ef.build_records(CHAT, [(0, 4)], _items(("partner", "любит чай", "likes", "Я люблю чай по утрам")), st, SB)
    assert st["raw_partner"] == 1 and st["raw_partner_kai_first_person"] == 1 and st["about_mismatch"] == 1


def test_build_records_chat_with_own_other_account_goes_to_self():
    sb = SelfBlock()
    sb.add_names(["Кай"])
    sb.tg_ids.add(7)
    st = Counter()
    out = ef.build_records(CHAT, [(0, 4)], _items(("partner", "любит аниме", "likes", "Я обожаю аниме, особенно Гибли")), st, sb)
    assert out[0]["about"] == "kai" and out[0]["name"] == "Кай" and out[0]["tg_id"] is None


def test_build_records_malformed_items_are_counted_not_crashing():
    st = Counter()
    out = ef.build_records(CHAT, [(0, 4)], [None, {"fact": "x"}, {"quote": "y"}, "строка"], st, SB)
    assert out == [] and st["malformed"] == 4


def test_extract_windows_calls_llm_with_signed_windows_and_batches():
    payload = json.dumps(_items(("partner", "любит аниме", "likes", "Я обожаю аниме, особенно Гибли")))
    fake = AsyncMock(return_value="вот ответ: " + payload)
    st = Counter()
    with patch.object(ef, "ask_claude", fake):
        out = asyncio.run(ef.extract_windows(CHAT, [(0, 4)], st, SB))
    prompt = fake.await_args.args[0]
    assert "Собеседник: Я обожаю аниме" in prompt and "Кай: У тебя же аллергия" in prompt
    assert fake.await_args.kwargs["system"] == ef.SYSTEM and st["calls"] == 1 and len(out) == 1


# ── self-blocklist ──────────────────────────────────────────────────────────

def test_selfblock_names_ids_usernames_and_word_order(monkeypatch):
    monkeypatch.setenv("PEOPLE_IMPORT_SELF_NAMES", "Свойимя, Свойимя Фамилия")
    monkeypatch.setenv("PEOPLE_IMPORT_SELF_VK_IDS", "888")
    monkeypatch.setenv("PEOPLE_IMPORT_SELF_USERNAMES", "@MyNick")
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "111,222")
    sb = load_selfblock({"user_id": 333, "username": "other_nick", "first_name": "Первое", "last_name": "Второе"})
    for name in ("Кай", "kai", "KAI LARK", "Вы", "свойимя", "Фамилия Свойимя", "Первое Второе", "Второе Первое"):
        assert sb.is_self(name=name), name
    assert sb.is_self(tg_id=111) and sb.is_self(tg_id=222) and sb.is_self(tg_id=333)
    assert sb.is_self(vk_id=888) and sb.is_self(username="@mynick") and sb.is_self(username="other_nick")
    assert not sb.is_self(name="Иван Петров", tg_id=5, vk_id=6, username="x")
    assert not sb.is_self(name="Кайло Рен")                                  # целиком, а не подстрока
    assert sb.display_names()[:3] == ["Кай", "Kai", "Kai Lark"] and "Вы" not in sb.display_names()


# ── устойчивость к сбоям LLM (кончился баланс → 27% вызовов тихо пропало) ───

def _many_windows_chat(n=30):
    msgs = []
    for i in range(n):
        msgs += [Msg("partner", "2021-01-%02d" % (i % 28 + 1), "я обожаю тему%d и кофе" % i), Msg("kai", "", "пауза%d" % i),
                 Msg("kai", "", "пауза"), Msg("kai", "", "пауза"), Msg("kai", "", "пауза"), Msg("kai", "", "пауза")]
    return Chat("tg", 7, "Имя1 Фам", msgs)


def test_empty_llm_response_is_a_failure_not_a_silent_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(ef, "CHECKPOINT", tmp_path / "done.txt")
    chat = _many_windows_chat(80)
    ranges = ef.window_ranges(chat.msgs)
    assert len(ranges) > ef.BATCH * ef.MAX_FAILED
    st = Counter()
    with patch.object(ef, "ask_claude", AsyncMock(return_value="")):
        with pytest.raises(ef.LLMUnavailable):
            asyncio.run(ef.extract_windows(chat, ranges, st, SB, done=set()))
    assert st["llm_failed"] == ef.MAX_FAILED and st["calls"] == ef.MAX_FAILED          # остановились, а не сожгли всё
    assert not (tmp_path / "done.txt").exists()                                         # пачки НЕ отмечены выполненными


def test_resume_skips_completed_batches_and_saves_records_before_marking(tmp_path, monkeypatch):
    monkeypatch.setattr(ef, "CHECKPOINT", tmp_path / "done.txt")
    chat = _many_windows_chat(30)
    ranges = ef.window_ranges(chat.msgs)[:ef.BATCH * 2]                  # ровно две пачки
    good = json.dumps([dict(i=0, about="partner", fact="любит кофе", type="likes",
                            quote=chat.msgs[0].text, speaker="Собеседник")])
    saved = []
    fake = AsyncMock(return_value=good)
    done = set()
    with patch.object(ef, "ask_claude", fake):
        out = asyncio.run(ef.extract_windows(chat, ranges, Counter(), SB, done, saved.extend))
        assert fake.await_count == 2 and len(done) == 2 and len(saved) == 2 == len(out)
        assert (tmp_path / "done.txt").read_text().split() == sorted(done, key=lambda m: int(m.rsplit(":", 1)[1]))
        # возобновление: те же пачки уже отмечены → вызовов нет
        st = Counter()
        asyncio.run(ef.extract_windows(chat, ranges, st, SB, set(ef._done_batches()), saved.extend))
        assert fake.await_count == 2 and st["skipped_done"] == 2


def test_batch_is_not_marked_done_if_saving_its_records_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ef, "CHECKPOINT", tmp_path / "done.txt")
    chat = _many_windows_chat(5)
    ranges = ef.window_ranges(chat.msgs)
    good = json.dumps([dict(i=0, about="partner", fact="любит кофе", type="likes",
                            quote=chat.msgs[0].text, speaker="Собеседник")])

    def broken_sink(recs):
        raise OSError("диск")

    done = set()
    with patch.object(ef, "ask_claude", AsyncMock(return_value=good)):
        with pytest.raises(OSError):
            asyncio.run(ef.extract_windows(chat, ranges, Counter(), SB, done, broken_sink))
    assert done == set() and not (tmp_path / "done.txt").exists()
