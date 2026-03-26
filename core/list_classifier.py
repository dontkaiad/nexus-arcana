"""core/list_classifier.py — regex pre-filters + Haiku fallback types for 🗒️ Списки."""
from __future__ import annotations

import re

# ── Покупки — создание ────────────────────────────────────────────────────────
_LIST_BUY_RE = re.compile(
    r"купи(?:ть)?\b|надо купить|нужно купить|добавь в (?:покупки|список)|в список\b",
    re.IGNORECASE,
)

# ── Чеклист — создание ───────────────────────────────────────────────────────
_LIST_CHECK_RE = re.compile(
    r"список:\s|разбей (?:задачу|работу)|подзадачи|чеклист",
    re.IGNORECASE,
)

# ── Инвентарь — добавление ───────────────────────────────────────────────────
_LIST_INV_ADD_RE = re.compile(
    r"дома есть:?\s|в инвентар|добавь в инвентарь",
    re.IGNORECASE,
)

# ── Инвентарь — поиск ("есть ибупрофен?", "есть ли дома X", "есть ли у меня X")
_LIST_INV_SEARCH_RE = re.compile(
    r"есть\s*(?:ли)?\s*(?:у меня)?\s*(?:дома)?\s*(?!задач|работ)\w+\??$",
    re.IGNORECASE,
)

# ── Чек покупки ("купила молоко 89р", "чек 4к привычки 1500") ────────────────
_LIST_DONE_RE = re.compile(
    r"купила?\b.*?\d+\s*[рр₽к]|^чек\s+",
    re.IGNORECASE,
)

# ── Инвентарь — обновление ("осталась 1 пачка", "закончился") ─────────────────
_LIST_INV_UPDATE_RE = re.compile(
    r"остал(?:ось?|ась?|ся)\s+\d|закончил(?:ся|ась|ось)|кончил(?:ся|ась)",
    re.IGNORECASE,
)

# ── Haiku fallback types (описания для classifier.py build_system) ────────────
LIST_HAIKU_TYPES = [
    'list_buy — добавить в список покупок:',
    '{"type":"list_buy","items":["молоко","яйца","корм"],"bot":"nexus"}',
    '',
    'list_check — создать чеклист:',
    '{"type":"list_check","name":"Собраться в поездку","items":["паспорт","зарядка"],"task_id":"optional"}',
    '',
    'list_inventory_add — добавить в инвентарь:',
    '{"type":"list_inventory_add","item":"парацетамол","quantity":2,"note":"верхний ящик ванной"}',
    '',
    'list_inventory_search — поиск в инвентаре:',
    '{"type":"list_inventory_search","query":"ибупрофен"}',
    '',
    'list_done — чек покупки (купила X за N руб):',
    '{"type":"list_done","items":[{"name":"молоко","price":89}],"total":null,"category":null}',
    '',
    'list_done_bulk — пакетный чек ("чек 4к привычки 1500 продукты 2500"):',
    '{"type":"list_done_bulk","total":4000,"breakdown":[{"category":"привычки","amount":1500},{"category":"продукты","amount":2500}]}',
    '',
    'list_inventory_update — обновить количество в инвентаре:',
    '{"type":"list_inventory_update","item":"парацетамол","quantity":0}',
    '',
    'ПРАВИЛА СПИСКОВ:',
    '- "купить молоко, яйца, корм" → list_buy (несколько айтемов)',
    '- "купила молоко 89р" → list_done (прошедшее время + цена)',
    '- "чек 4к привычки 1500 продукты 2500" → list_done_bulk (разбивка по категориям)',
    '- "чек лента 2340 продукты" → list_done (общий одной категорией)',
    '- "дома есть: парацетамол, ибупрофен" → list_inventory_add',
    '- "есть ибупрофен?" → list_inventory_search',
    '- "закончился парацетамол" → list_inventory_update quantity=0',
    '- "осталась 1 пачка парацетамола" → list_inventory_update quantity=1',
]
