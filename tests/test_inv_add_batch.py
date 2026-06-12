"""tests/test_inv_add_batch.py — regression #75.

`handle_list_inv_add` падал на batch-вводе: Haiku возвращал list, а код звал
`.get(...)` на нём. Покрываем нормализацию + smoke на batch-флоу без
обращения к Notion (через моки).

Также: #76 (regex-fallback + category-хинты), #77 (эвристика «медицинский
список без префикса»), #78 (_parse_inv_line: qty + note из одной строки),
#79 (извлечение срока годности).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nexus.handlers import lists as lists_mod


# ── #75: _normalize_inv_items — нормализация ответа Haiku ───────────────────

@pytest.mark.parametrize("parsed,expected", [
    # {items:[...]} — основной batch-формат
    pytest.param(
        {"items": [
            {"name": "меновазин", "quantity": 2, "category": "🏥 Здоровье"},
            {"name": "уголь", "quantity": 1, "note": "250мг 30шт"},
        ]},
        [
            {"name": "меновазин", "quantity": 2, "note": "", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "уголь", "quantity": 1, "note": "250мг 30шт", "category": "💳 Прочее", "expiry": ""},
        ],
        id="dict-with-items"),
    # legacy одиночный {item:...}
    pytest.param(
        {"item": "парацетамол", "quantity": 1, "category": "🏥 Здоровье"},
        [{"name": "парацетамол", "quantity": 1, "note": "", "category": "🏥 Здоровье", "expiry": ""}],
        id="legacy-single-dict"),
    # голый list
    pytest.param(
        [{"name": "ромашка", "quantity": 1}, {"name": "шалфей", "quantity": 1}],
        [
            {"name": "ромашка", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
            {"name": "шалфей", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
        ],
        id="bare-list"),
    # дефолты для отсутствующих полей
    pytest.param(
        {"items": [{"name": "x"}]},
        [{"name": "x", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""}],
        id="defaults-for-missing-fields"),
    # пустые имена и мусор-элементы пропускаются
    pytest.param(
        {"items": [{"name": ""}, {"item": None}, {"name": "valid"}, "junk-string"]},
        [{"name": "valid", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""}],
        id="skips-empty-names"),
    # мусор на входе → []
    pytest.param(None, [], id="garbage-none"),
    pytest.param("not json", [], id="garbage-string"),
    pytest.param(42, [], id="garbage-int"),
    # #79: expiry из ключа expires (Haiku-формат)
    pytest.param(
        {"items": [{"name": "крем", "quantity": 1, "expires": "2027-03-31"}]},
        [{"name": "крем", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": "2027-03-31"}],
        id="expiry-from-haiku-expires"),
    # #79: Haiku не выделил срок — дочищаем из note
    pytest.param(
        {"items": [{"name": "гексаспрей", "quantity": 1, "note": "30гр годен до 03.2027"}]},
        [{"name": "гексаспрей", "quantity": 1, "note": "30гр", "category": "💳 Прочее", "expiry": "2027-03-31"}],
        id="expiry-recovered-from-note"),
])
def test_normalize_inv_items(parsed, expected):
    """_normalize_inv_items: {items:[...]} / одиночный {item:...} / голый list →
    список dict'ов с дефолтами; мусор → []."""
    assert lists_mod._normalize_inv_items(parsed) == expected


# ── #76: category-хинты ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hint,category", [
    pytest.param("лекарства", "🏥 Здоровье", id="health-lekarstva"),
    pytest.param("ТАБЛЕТКИ", "🏥 Здоровье", id="health-tabletki-upper"),
    pytest.param("аптечка", "🏥 Здоровье", id="health-aptechka"),
    pytest.param("продукты", "🍜 Продукты", id="food-produkty"),
    pytest.param("еду", "🍜 Продукты", id="food-edu"),
    pytest.param("бытовая химия", "🧹 Дом", id="home-chemistry"),
    pytest.param("косметика", "💄 Красота", id="beauty-kosmetika"),
    pytest.param("инструменты", "🔧 Инструменты", id="tools-instrumenty"),
    pytest.param("xyz", "", id="unknown-word"),
    pytest.param("", "", id="empty-string"),
])
def test_category_from_hint(hint, category):
    """_category_from_hint: слово-хинт из префикса → категория инвентаря (или '')."""
    assert lists_mod._category_from_hint(hint) == category


# ── #76/#78: regex-fallback построчного разбора ──────────────────────────────

@pytest.mark.parametrize("text,expected", [
    # Префикс с категорией; после #78 fallback извлекает qty/note —
    # дозировка уходит в note, строки без цифр — целиком в name.
    pytest.param(
        "занеси в инвентарь лекарства\n"
        "сироп солодки (немного)\n"
        "рициниол базовый 30мл\n"
        "зубные нити",
        [
            {"name": "сироп солодки (немного)", "quantity": 1, "note": "", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "рициниол базовый", "quantity": 1, "note": "30мл", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "зубные нити", "quantity": 1, "note": "", "category": "🏥 Здоровье", "expiry": ""},
        ],
        id="category-prefix-health"),
    # Префикс без категории → дефолт 💳 Прочее
    pytest.param(
        "занеси в инвентарь\nфонарик\nверёвка",
        [
            {"name": "фонарик", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
            {"name": "верёвка", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
        ],
        id="no-category-uses-default"),
    # Bullet-символы (•, -, —) срезаются
    pytest.param(
        "добавь в инвентарь продукты\n• молоко\n- хлеб\n— сыр",
        [
            {"name": "молоко", "quantity": 1, "note": "", "category": "🍜 Продукты", "expiry": ""},
            {"name": "хлеб", "quantity": 1, "note": "", "category": "🍜 Продукты", "expiry": ""},
            {"name": "сыр", "quantity": 1, "note": "", "category": "🍜 Продукты", "expiry": ""},
        ],
        id="strips-bullet-chars"),
    # Пусто / только префикс → []
    pytest.param("", [], id="empty-text"),
    pytest.param("занеси в инвентарь лекарства", [], id="prefix-only"),
    # Регресс #78: реальный batch Кай должен сохранить количества
    pytest.param(
        "занеси в инвентарь лекарства\n"
        "меновазин 2 шт\n"
        "глюкофаж 1000мг 10 шт\n"
        "пластырь 58шт\n"
        "бинт обычный",
        [
            {"name": "меновазин", "quantity": 2, "note": "", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "глюкофаж", "quantity": 10, "note": "1000мг", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "пластырь", "quantity": 58, "note": "", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "бинт обычный", "quantity": 1, "note": "", "category": "🏥 Здоровье", "expiry": ""},
        ],
        id="extracts-qty-for-each-line"),
    # Без префикса, но много фарм-маркеров → дефолт = 🏥 Здоровье
    pytest.param(
        "велаксин 75мг 10шт\nвенлафаксин 37.5мг\nзубные нити",
        [
            {"name": "велаксин", "quantity": 10, "note": "75мг", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "венлафаксин", "quantity": 1, "note": "37.5мг", "category": "🏥 Здоровье", "expiry": ""},
            {"name": "зубные нити", "quantity": 1, "note": "", "category": "🏥 Здоровье", "expiry": ""},
        ],
        id="auto-detects-health-from-pharm-markers"),
    # Без фарм-маркеров — дефолт остаётся 💳 Прочее
    pytest.param(
        "фонарик\nверёвка\nспички",
        [
            {"name": "фонарик", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
            {"name": "верёвка", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
            {"name": "спички", "quantity": 1, "note": "", "category": "💳 Прочее", "expiry": ""},
        ],
        id="no-pharm-markers-keeps-default"),
])
def test_fallback_split_inv_text(text, expected):
    """_fallback_split_inv_text: построчный regex-fallback — категория из
    префикса/фарм-маркеров, qty/note из каждой строки, bullets срезаются."""
    assert lists_mod._fallback_split_inv_text(text) == expected


# ── #77: эвристика «медицинский список без префикса» ─────────────────────────

@pytest.mark.parametrize("text,expected", [
    # Реальный кейс из бага #77: список лекарств без префикса
    pytest.param(
        "велаксин таблетки 75мг 9.5шт\n"
        "венлафаксин-алси таблетки 37.5мг 10шт\n"
        "мендилекс бипериден 2мг8 шт\n"
        "пластырь 58шт\n"
        "измеритель артериального давления\n"
        "сироп солодки (немного)\n"
        "рициниол базовый без эмульсии 30мл\n"
        "зубные нити",
        True, id="typical-med-batch"),
    # Слишком мало строк
    pytest.param("парацетамол 500мг", False, id="one-line-not-enough"),
    pytest.param("парацетамол 500мг\nибупрофен 200мг", False, id="two-lines-not-enough"),
    # Нет фарм-маркеров
    pytest.param("купить молоко\nкупить хлеб\nкупить сыр\nкупить масло",
                 False, id="no-pharm-markers"),
    # «чек 4к продукты 2500» и подобное не должно ловиться (финансы)
    pytest.param("чек 4к привычки 1500 продукты 2500", False, id="finance-not-matched"),
    # Одной фарм-строки недостаточно
    pytest.param("парацетамол 500мг\nкупить кота\nпозвонить маме",
                 False, id="single-pharm-line-not-enough"),
])
def test_looks_like_med_inventory(text, expected):
    """_looks_like_med_inventory: >=3 строк + >=2 фарм-маркера → True;
    финансы/задачи/короткие списки → False."""
    from core.list_classifier import _looks_like_med_inventory
    assert _looks_like_med_inventory(text) is expected


@pytest.mark.asyncio
async def test_classifier_routes_med_list_to_inventory_not_budget():
    """Главный регресс #77: медицинский список → list_inventory_add, НЕ budget.

    Это предотвращает дорогостоящий Sonnet-вызов budget-анализа.
    """
    from core import classifier
    text = (
        "велаксин таблетки 75мг 9.5шт\n"
        "венлафаксин-алси таблетки 37.5мг 10шт\n"
        "пластырь 58шт\n"
        "сироп солодки\n"
        "рициниол базовый 30мл"
    )
    # Haiku Router НЕ должен вызываться — pre-filter ловит раньше.
    with patch.object(classifier, "ask_claude", AsyncMock(side_effect=AssertionError(
        "ask_claude must NOT be called — pre-filter should match first"
    ))):
        result = await classifier.classify(text, tz_offset=3)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["type"] == "list_inventory_add"


@pytest.mark.parametrize("text", [
    # `занеси/положи/закинь/запиши в инвентарь` ловятся pre-filter'ом,
    # не уходят в Haiku
    pytest.param("занеси в инвентарь лекарства", id="zanesi"),
    pytest.param("положи в инвентарь", id="polozhi"),
    pytest.param("закинь в инвентарь молоко", id="zakin"),
    pytest.param("запиши в инвентарь", id="zapishi"),
    # Старые варианты тоже работают
    pytest.param("добавь в инвентарь", id="dobav-legacy"),
    pytest.param("дома есть: парацетамол", id="doma-est-legacy"),
])
def test_list_inv_add_re_catches_variants(text):
    """_LIST_INV_ADD_RE: все глаголы-варианты «… в инвентарь» ловятся pre-filter'ом."""
    from core.list_classifier import _LIST_INV_ADD_RE
    assert _LIST_INV_ADD_RE.search(text)


# ── #78/#79: _parse_inv_line — name + qty + note + expiry из одной строки ────

@pytest.mark.parametrize("line,name,qty,note,expiry", [
    pytest.param("меновазин 2 шт", "меновазин", 2, "", "", id="qty-sht"),
    pytest.param("глюкофаж 1000мг 10 шт", "глюкофаж", 10, "1000мг", "", id="dose-then-qty"),
    pytest.param("активированный уголь 250мг 1 пачка 30шт", "активированный уголь", 30, "250мг", "", id="last-qty-wins"),
    pytest.param("пластырь 58шт", "пластырь", 58, "", "", id="qty-no-space"),
    pytest.param("бинт обычный", "бинт обычный", 1, "", "", id="no-digits"),
    pytest.param("хлорид натрия 0,9% 400мл", "хлорид натрия", 1, "0,9% 400мл", "", id="percent-and-ml-to-note"),
    pytest.param("вата", "вата", 1, "", "", id="single-word"),
    pytest.param("ибупрофен 400мг 16 шт", "ибупрофен", 16, "400мг", "", id="ibuprofen"),
    pytest.param("шприцы 5кубов 3 шт", "шприцы", 3, "5кубов", "", id="kuby-note"),
    pytest.param("лизобакт 20 таблеток", "лизобакт", 20, "", "", id="tabletok-qty"),
    pytest.param("активированный уголь 500мг 50 таблеток", "активированный уголь", 50, "500мг", "", id="dose-and-tabletok"),
    pytest.param("сульфат магния 6 пакетов по 25г", "сульфат магния", 6, "по 25г", "", id="pakety-qty"),
    pytest.param("найз 100мг 6шт", "найз", 6, "100мг", "", id="dose-qty-no-space"),
    pytest.param("супрастин 20 шт", "супрастин", 20, "", "", id="plain-qty"),
    # #79: «годен до …» извлекается в expiry, в note остаётся только дозировка
    pytest.param("гексаспрей аэрозоль 2,5% 30гр годен до 03.2027",
                 "гексаспрей аэрозоль", 1, "2,5% 30гр", "2027-03-31", id="expiry-month-year"),
    pytest.param("парацетамол 500мг 10шт годен до 15.06.2026",
                 "парацетамол", 10, "500мг", "2026-06-15", id="expiry-with-qty"),
    # bullet-символы срезаются
    pytest.param("• молоко", "молоко", 1, "", "", id="strips-bullet-dot"),
    pytest.param("— парацетамол 2 шт", "парацетамол", 2, "", "", id="strips-bullet-dash"),
])
def test_parse_inv_line(line, name, qty, note, expiry):
    """_parse_inv_line: одна строка → name + quantity + note + expiry."""
    out = lists_mod._parse_inv_line(line)
    assert out is not None
    assert out["name"] == name, f"name mismatch for {line!r}: got {out['name']!r}"
    assert out["quantity"] == qty, f"qty mismatch for {line!r}: got {out['quantity']!r}"
    assert out["note"] == note, f"note mismatch for {line!r}: got {out['note']!r}"
    assert out["expiry"] == expiry, f"expiry mismatch for {line!r}: got {out['expiry']!r}"


@pytest.mark.parametrize("line", [
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace-only"),
])
def test_parse_inv_line_empty_returns_none(line):
    """_parse_inv_line: пустой ввод → None."""
    assert lists_mod._parse_inv_line(line) is None


# ── #79: извлечение срока годности ───────────────────────────────────────────

@pytest.mark.parametrize("text,iso,cleaned", [
    pytest.param("гексаспрей 30гр годен до 03.2027", "2027-03-31", "гексаспрей 30гр", id="goden-do-month-year"),
    pytest.param("парацетамол годен до 15.06.2026", "2026-06-15", "парацетамол", id="goden-do-full-date"),
    pytest.param("мазь до 01.2027", "2027-01-31", "мазь", id="do-month-year"),
    pytest.param("сыворотка срок годности 12.2025", "2025-12-31", "сыворотка", id="srok-godnosti-month-year"),
    pytest.param("крем срок годности до 28.02.2026", "2026-02-28", "крем", id="srok-godnosti-do-full-date"),
    pytest.param("таблетки до 30.04.27", "2027-04-30", "таблетки", id="two-digit-year"),
    pytest.param("просто крем без даты", None, "просто крем без даты", id="no-date"),
    pytest.param("крем до 99.2027", None, "крем до 99.2027", id="invalid-month-ignored"),
])
def test_extract_expiry(text, iso, cleaned):
    """extract_expiry: «годен до» / «срок годности» + дата → ISO + очищенный
    текст; невалидный месяц / отсутствие даты — текст не трогаем."""
    from core.inv_line_parser import extract_expiry
    got_iso, got_clean = extract_expiry(text)
    assert got_iso == iso, f"iso mismatch for {text!r}: got {got_iso!r}"
    assert got_clean == cleaned, f"cleaned mismatch for {text!r}: got {got_clean!r}"


def test_extract_expiry_month_only_uses_last_day():
    from core.inv_line_parser import extract_expiry
    # февраль невисокосного 2027 → 28 число
    iso, _ = extract_expiry("до 02.2027")
    assert iso == "2027-02-28"
    # февраль високосного 2028 → 29 число
    iso2, _ = extract_expiry("до 02.2028")
    assert iso2 == "2028-02-29"


# ── handle_list_inv_add: smoke-флоу с моками (уникальная логика) ─────────────

@pytest.mark.asyncio
async def test_handle_list_inv_add_batch_does_not_ask_expiry():
    """5 позиций → одно сводное сообщение, без pending_set на срок годности."""
    parsed = {"items": [
        {"name": "меновазин", "quantity": 2, "category": "🏥 Здоровье"},
        {"name": "уголь", "quantity": 1, "note": "250мг", "category": "🏥 Здоровье"},
        {"name": "амоксиклав", "quantity": 1, "note": "500+125мг", "category": "🏥 Здоровье"},
        {"name": "гексикон", "quantity": 1, "category": "🏥 Здоровье"},
        {"name": "ромашка", "quantity": 1, "category": "🏥 Здоровье"},
    ]}
    created = [
        {"id": f"page-{i}", "name": it["name"], "type": "📦 Инвентарь", "category": it["category"]}
        for i, it in enumerate(parsed["items"])
    ]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "занеси в инвентарь\nменовазин 2 шт\n..."

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value=parsed)), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_set.assert_not_called()
    assert msg.answer.call_count == 1
    sent = msg.answer.call_args.args[0]
    assert "5 позиций" in sent
    assert "меновазин" in sent and "× 2" in sent
    assert "ромашка" in sent


@pytest.mark.asyncio
async def test_handle_list_inv_add_single_asks_expiry():
    """Одиночный ввод сохраняет поведение: добавляет + спрашивает срок."""
    parsed = {"items": [{"name": "парацетамол", "quantity": 1, "category": "🏥 Здоровье"}]}
    created = [{"id": "page-1", "name": "парацетамол", "type": "📦 Инвентарь", "category": "🏥 Здоровье"}]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "дома есть парацетамол"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value=parsed)), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_set.assert_called_once()
    pending_args = p_set.call_args.args
    assert pending_args[1]["action"] == "inv_expiry"
    assert pending_args[1]["item_name"] == "парацетамол"
    assert msg.answer.call_count == 2
    assert "Срок годности" in msg.answer.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_handle_list_inv_add_empty_parse_responds_gracefully():
    """Если Haiku вернул мусор И fallback ничего не извлёк — отвечаем подсказкой.

    Используем ввод состоящий только из префикса без позиций — тогда fallback
    тоже вернёт [], потому что body_lines пуст.
    """
    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "занеси в инвентарь"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value={"items": []})), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=[])) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()):
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_add.assert_not_called()
    assert msg.answer.call_count == 1
    assert "Не смог разобрать" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_list_inv_add_uses_fallback_when_haiku_returns_empty():
    """Haiku вернул items=[] — переходим на regex-fallback, создаём айтемы."""
    text = (
        "занеси в инвентарь лекарства\n"
        "сироп солодки (немного)\n"
        "рициниол базовый 30мл\n"
        "зубные нити"
    )
    fallback_items = lists_mod._fallback_split_inv_text(text)
    created = [
        {"id": f"p{i}", "name": it["name"], "type": "📦 Инвентарь", "category": it["category"]}
        for i, it in enumerate(fallback_items)
    ]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = text

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value={"items": []})), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": text}, user_notion_id="user-page-id",
        )

    p_add.assert_called_once()
    sent_items = p_add.call_args.args[0]
    # После #78: «рициниол базовый 30мл» → name=«рициниол базовый», note=«30мл».
    names = [it["name"] for it in sent_items]
    assert "сироп солодки (немного)" in names
    assert "рициниол базовый" in names
    assert "зубные нити" in names
    assert all(it["category"] == "🏥 Здоровье" for it in sent_items)
    p_set.assert_not_called()
    assert "3 позиций" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_inv_add_single_with_expiry_does_not_ask():
    """Если срок извлечён — не спрашиваем повторно."""
    parsed = {"items": [{"name": "гексаспрей", "quantity": 1, "note": "30гр", "expires": "2027-03-31", "category": "🏥 Здоровье"}]}
    created = [{"id": "p1", "name": "гексаспрей", "type": "📦 Инвентарь", "category": "🏥 Здоровье"}]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "гексаспрей 30гр годен до 03.2027"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value=parsed)), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    # add_items получил expiry
    sent = p_add.call_args.args[0]
    assert sent[0]["expiry"] == "2027-03-31"
    # срок не переспрашиваем
    p_set.assert_not_called()
    assert msg.answer.call_count == 1
    assert "до 2027-03-31" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_list_inv_add_uses_fallback_when_haiku_raises():
    """Haiku упал — regex-fallback всё равно спасает ввод."""
    text = "занеси в инвентарь лекарства\nпарацетамол"

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = text

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(side_effect=ValueError("bad json"))), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=[
             {"id": "p1", "name": "парацетамол", "type": "📦 Инвентарь", "category": "🏥 Здоровье"},
         ])), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": text}, user_notion_id="user-page-id",
        )

    # 1 элемент → должен спросить срок годности
    p_set.assert_called_once()
