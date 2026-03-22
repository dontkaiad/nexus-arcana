# core/schema.py
# Единственный источник правды о структуре Notion.
# При добавлении поля — редактировать только этот файл.

# ─── DATABASE IDs ────────────────────────────────────────────────────────────

DB = {
    # ☀️ Nexus
    "tasks":     "31a42b3b1ac08051a3ccde86e6233d30",
    "memory":    "31a42b3b1ac0801f8e3cf1441b61bc69",
    "notes":     "31a42b3b1ac0807ba68fd700ab695e7c",
    # 🌒 Arcana
    "clients":   "31b42b3b1ac08022baafffbcc8237bbd",
    "sessions":  "31b42b3b1ac08038b4a7e88c8c382875",
    "rituals":   "31b42b3b1ac0800b81a3cf9cbcc7cd6b",
    # Общие
    "finance":   "31a42b3b1ac080ae8b6ad8ba84d141bb",
    "errors":    "31a42b3b1ac080558c68fe885ece5b2a",
}

# ─── NEXUS ───────────────────────────────────────────────────────────────────

TASKS_SCHEMA = {
    "title_field": "Задача",
    "status": {
        "field": "Статус",
        "type": "status",
        "options": ["Not started", "In progress", "Done"],
        "aliases": {
            "сделать": "Not started",
            "не начато": "Not started",
            "в процессе": "In progress",
            "делаю": "In progress",
            "готово": "Done",
            "сделано": "Done",
        }
    },
    "priority": {
        "field": "Приоритет",
        "type": "select",
        "options": ["Можно потом", "Важно", "Срочно"],
        "aliases": {
            "низкий": "Можно потом",
            "потом": "Можно потом",
            "средний": "Важно",
            "важно": "Важно",
            "высокий": "Срочно",
            "срочно": "Срочно",
        }
    },
    "deadline": {"field": "Дедлайн", "type": "date"},
}

MEMORY_SCHEMA = {
    "title_field": "Ключ",
    "value": {"field": "Значение", "type": "rich_text"},
    "category": {
        "field": "Категория",
        "type": "select",
        "options": ["📁 Данные", "👤 Личное", "🐱 Коты"],
    },
}

NOTES_SCHEMA = {
    "title_field": "Заголовок",
    "tags": {
        "field": "Теги",
        "type": "multi_select",
        "options": ["🛒 Покупки", "🎧 Послушать", "📌 Важное", "📝 Список", "🎬 Посмотреть", "🧠 Идея"],
    },
    "date": {"field": "Дата", "type": "date"},
}

# ─── ARCANA ──────────────────────────────────────────────────────────────────

CLIENTS_SCHEMA = {
    "title_field": "Имя",
    "contact": {"field": "Контакт", "type": "rich_text"},
    "request": {"field": "Запрос", "type": "rich_text"},
    "notes": {"field": "Заметки", "type": "rich_text"},
    "status": {
        "field": "Статус",
        "type": "status",
        "options": ["🟢 Активный", "🌙 Разовый", "⛔ Закрыт"],
        "aliases": {
            "активный": "🟢 Активный",
            "активна": "🟢 Активный",
            "разовый": "🌙 Разовый",
            "разовая": "🌙 Разовый",
            "закрыт": "⛔ Закрыт",
            "закрыта": "⛔ Закрыт",
        }
    },
    # Читать только (Relations + Rollups + Formula):
    # sessions_count, first_session, total_paid,
    # sessions_debt, rituals_count, rituals_debt, total_debt
}

SESSIONS_SCHEMA = {
    "title_field": "Тема",
    "date": {"field": "Дата", "type": "date"},
    "client": {"field": "Клиенты", "type": "relation", "db": "clients"},
    "session_type": {
        "field": "Тип сеанса",
        "type": "select",
        "options": ["🌟 Личный", "🤝 Клиентский"],
        "aliases": {
            "личный": "🌟 Личный",
            "личная": "🌟 Личный",
            "мой": "🌟 Личный",
            "клиентский": "🤝 Клиентский",
            "для клиента": "🤝 Клиентский",
        }
    },
    "spread_type": {
        "field": "Тип расклада",
        "type": "multi_select",
        "options": [
            "🌀 Триплет",
            "🔮 Сфера жизни",
            "🗝️ Кельтский крест",
            "⚡ Магические воздействия",
            "🕯️ Диагностика перед ритуалом",
            "✨ Диагностика способностей",
            "🌳 Родовой узел",
        ],
    },
    "area": {
        "field": "Область",
        "type": "select",
        "options": ["Отношения", "Финансы", "Работа", "Здоровье", "Род", "Общая ситуация"],
        "aliases": {
            "отношения": "Отношения",
            "любовь": "Отношения",
            "партнёр": "Отношения",
            "деньги": "Финансы",
            "финансы": "Финансы",
            "работа": "Работа",
            "карьера": "Работа",
            "здоровье": "Здоровье",
            "род": "Род",
            "родовое": "Род",
            "общее": "Общая ситуация",
        }
    },
    "decks": {
        "field": "Колоды",
        "type": "multi_select",
        "options": ["Уэйта", "Dark Wood Tarot", "Ленорман", "Игральные", "Deviant Moon"],
        # Добавить новую колоду: в Notion UI + строка сюда
    },
    "cards": {"field": "Карты", "type": "rich_text"},
    "interpretation": {"field": "Трактовка", "type": "rich_text"},
    "photo": {"field": "Фото", "type": "url"},
    "amount": {"field": "Сумма", "type": "number"},
    "payment_source": {
        "field": "Источник",
        "type": "select",
        "options": ["💳 Карта", "💵 Наличные", "🔄 Бартер"],
        "aliases": {
            "карта": "💳 Карта",
            "картой": "💳 Карта",
            "нал": "💵 Наличные",
            "наличные": "💵 Наличные",
            "наличкой": "💵 Наличные",
            "бартер": "🔄 Бартер",
        }
    },
    "paid": {"field": "Оплачено", "type": "number"},
    # "debt" — Formula Notion: Сумма − Оплачено (только читать)
    "fulfilled": {
        "field": "Сбылось",
        "type": "select",
        "options": ["✅ Да", "❌ Нет", "〰️ Частично", "⏳ Не проверено"],
        "default": "⏳ Не проверено",
        "aliases": {
            "да": "✅ Да",
            "сбылось": "✅ Да",
            "нет": "❌ Нет",
            "не сбылось": "❌ Нет",
            "частично": "〰️ Частично",
            "не проверено": "⏳ Не проверено",
        }
    },
}

RITUALS_SCHEMA = {
    "title_field": "Название",
    "date": {"field": "Дата", "type": "date"},
    "client": {"field": "Клиенты", "type": "relation", "db": "clients"},
    "ritual_type": {
        "field": "Тип",
        "type": "select",
        "options": ["🌟 Личный", "🤝 Клиентский"],
        "aliases": {
            "личный": "🌟 Личный",
            "личная": "🌟 Личный",
            "клиентский": "🤝 Клиентский",
            "для клиента": "🤝 Клиентский",
        }
    },
    "goal": {
        "field": "Цель",
        "type": "multi_select",
        "options": [
            "🚀 Привлечение",
            "🛡️ Защита",
            "🧹 Очищение",
            "💞 Любовь",
            "💎 Финансы",
            "🖤 Деструктив/Возврат",
            "⚔️ Развязка/Отсечение",
            "🔗 Приворот/Присушка",
            "🌀 Другое",
        ],
    },
    "place": {
        "field": "Место",
        "type": "select",
        "options": [
            "🏠 Дома", "🌲 Лес", "✝️ Погост",
            "🛤️ Перекрёсток", "⛪ Церковь",
            "💧 Водоём", "🌾 Поле", "🌍 Другое"
        ],
    },
    "consumables": {"field": "Расходники", "type": "rich_text"},
    "offerings": {"field": "Подношения", "type": "rich_text"},
    "offerings_amount": {"field": "Сумма подношений", "type": "number"},
    "duration_min": {"field": "Время (мин)", "type": "number"},
    "forces": {"field": "Силы", "type": "rich_text"},
    "structure": {"field": "Структура", "type": "rich_text"},
    "ritual_notes": {"field": "Заметки", "type": "rich_text"},
    "price": {"field": "Цена за ритуал", "type": "number"},
    "payment_source": {
        "field": "Источник оплаты",
        "type": "select",
        "options": ["💳 Карта", "💵 Наличные", "🔄 Бартер"],
        "aliases": {
            "карта": "💳 Карта",
            "картой": "💳 Карта",
            "нал": "💵 Наличные",
            "наличные": "💵 Наличные",
            "бартер": "🔄 Бартер",
        }
    },
    "paid": {"field": "Оплачено", "type": "number"},
    # "debt" — Formula Notion (только читать)
    "result": {
        "field": "Результат",
        "type": "select",
        "options": ["✅ Сработал", "❌ Не сработал", "〰️ Частично", "⏳ Не проверено"],
        "default": "⏳ Не проверено",
        "aliases": {
            "сработал": "✅ Сработал",
            "да": "✅ Сработал",
            "не сработал": "❌ Не сработал",
            "нет": "❌ Не сработал",
            "частично": "〰️ Частично",
        }
    },
}

# ─── ОБЩИЕ ───────────────────────────────────────────────────────────────────

FINANCE_SCHEMA = {
    "title_field": "Описание",
    "date": {"field": "Дата", "type": "date"},
    "amount": {"field": "Сумма", "type": "number"},
    "category": {
        "field": "Категория",
        "type": "select",
        "options": [
            "🔮 Практика", "🟰 Прочее", "💰 Зарплата",
            "📚 Хобби/Учеба", "🌿 Расходники", "🏥 Здоровье",
            "📱 Подписки", "👗 Гардероб", "💅 Бьюти",
            "🚕 Транспорт", "🍽️ Кафе/Доставка", "🛒 Продукты",
            "💊 Привычки", "🏠 Жильё", "🐱 Коты",
        ],
        "aliases": {
            "такси": "🚕 Транспорт",
            "метро": "🚕 Транспорт",
            "кофе": "🍽️ Кафе/Доставка",
            "кафе": "🍽️ Кафе/Доставка",
            "доставка": "🍽️ Кафе/Доставка",
            "ногти": "💅 Бьюти",
            "маникюр": "💅 Бьюти",
            "волосы": "💅 Бьюти",
            "продукты": "🛒 Продукты",
            "еда": "🛒 Продукты",
            "аренда": "🏠 Жильё",
            "квартира": "🏠 Жильё",
            "кот": "🐱 Коты",
            "корм": "🐱 Коты",
            "зарплата": "💰 Зарплата",
            "практика": "🔮 Практика",
            "сеанс": "🔮 Практика",
            "ритуал": "🔮 Практика",
            "свечи": "🌿 Расходники",
            "травы": "🌿 Расходники",
        }
    },
    "type": {
        "field": "Тип",
        "type": "select",
        "options": ["🌿 Расход", "💰 Доход"],
        "aliases": {
            "расход": "🌿 Расход",
            "трата": "🌿 Расход",
            "потратила": "🌿 Расход",
            "купила": "🌿 Расход",
            "доход": "💰 Доход",
            "получила": "💰 Доход",
            "пришло": "💰 Доход",
            "заработала": "💰 Доход",
        }
    },
    "source": {
        "field": "Источник",
        "type": "select",
        "options": ["💳 Карта", "💵 Наличные", "🔄 Бартер"],
    },
    "bot": {
        "field": "Бот",
        "type": "select",
        "options": ["☀️ Nexus", "🌒 Arcana"],
    },
}

ERRORS_SCHEMA = {
    "title_field": "Сообщение",
    "date": {"field": "Дата", "type": "date"},
    "error_type": {
        "field": "Тип ошибки",
        "type": "select",
        "options": [
            "unknown_type",
            "parse_error",
            "api_error",
            "notion_error",
            "media_error",
            "processing_error",
        ],
    },
    "claude_response": {"field": "Ответ Claude", "type": "rich_text"},
    "traceback": {"field": "Трейсбек", "type": "rich_text"},
    "bot": {
        "field": "Бот",
        "type": "select",
        "options": ["☀️ Nexus", "🌒 Arcana"],
    },
}

# ─── ДОСТУП ───────────────────────────────────────────────────────────────────

SCHEMAS = {
    "tasks": TASKS_SCHEMA,
    "memory": MEMORY_SCHEMA,
    "notes": NOTES_SCHEMA,
    "clients": CLIENTS_SCHEMA,
    "sessions": SESSIONS_SCHEMA,
    "rituals": RITUALS_SCHEMA,
    "finance": FINANCE_SCHEMA,
    "errors": ERRORS_SCHEMA,
}
