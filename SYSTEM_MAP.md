# SYSTEM_MAP — AI_Agents Architecture
> Версия 5.0 | Март 2026 | RPi3

---

## 1. СТРУКТУРА NOTION

```
AI_Agents (root)
├── 🪪 Пользователи        32842b3b1ac080f4b4bde1aaa3b9d312  ← мультиаккаунт + права
├── ☀️ Nexus
│   ├── ✅ Задачи          31a42b3b1ac08051a3ccde86e6233d30
│   ├── 🧠 Память          31a42b3b1ac0801f8e3cf1441b61bc69
│   ├── 💡 Заметки         31a42b3b1ac0807ba68fd700ab695e7c
│   └── 🔐 Пароли          31a42b3b1ac0804faae6f599d91e08a8
├── 🌒 Arcana
│   ├── 👤 Клиенты         31b42b3b1ac08022baafffbcc8237bbd
│   ├── 🃏 Расклады        31b42b3b1ac08038b4a7e88c8c382875  ← бывшие Сеансы
│   ├── 🕯️ Ритуалы         31b42b3b1ac0800b81a3cf9cbcc7cd6b
│   └── 🔮 Работы          31d42b3b1ac0805ab88dce8e7e480605  ← задачи практики
├── 💰 Финансы             31a42b3b1ac080ae8b6ad8ba84d141bb
└── ⚠️ Ошибки              31a42b3b1ac080558c68fe885ece5b2a
```

Все базы имеют поле `Пользователь` (Relation → 🪪 Пользователи).
Все запросы фильтруются по этому полю.

---

## 2. БЕЗОПАСНОСТЬ И МУЛЬТИАККАУНТ

```
Слой 1 — Whitelist Middleware:
  TG IDs: [67686090, 790273371]
  Любой другой ID → игнор, без ответа

Слой 2 — База Пользователи:
  Проверка TG ID → читаем права (checkboxes)
  ☀️ Nexus / 🌒 Arcana / 💰 Финансы / 🔐 Пароли
  Нет записи → игнор
  Нет нужного checkbox → "⛔ Нет доступа к [функция]"

Роли: Владелец / Друг / Тест
Права меняются прямо в Notion UI, без деплоя
```

---

## 3. СТРУКТУРА КОДА

```
AI_AGENTS/
├── core/
│   ├── config.py           # .env загрузка + NOTION_DB_USERS
│   ├── user_manager.py     # get_user(), check_permission(), кэш 5 мин
│   ├── schema.py           # Все поля Notion, ID, варианты select
│   ├── notion_client.py    # CRUD для всех баз + _with_user_filter
│   ├── classifier.py       # Классификатор + timezone pre-filter
│   ├── field_mapper.py     # Ответ Claude → Notion поля
│   ├── claude_client.py    # Haiku / Sonnet вызовы
│   ├── middleware.py       # Whitelist + DI user_notion_id
│   ├── layout.py           # maybe_convert (ru/en раскладка)
│   └── time_manager.py     # Парсинг времени + ночная логика до 05:00
│
├── nexus/
│   ├── nexus_bot.py
│   └── handlers/
│       ├── tasks.py        # task_done fuzzy, reminder/deadline логика
│       ├── notes.py
│       ├── finance.py      # description_search, статистика → Notion
│       ├── memory.py
│       ├── passwords.py
│       └── voice.py
│
├── arcana/
│   ├── bot.py
│   ├── middleware.py       # Whitelist + Arcana checkbox проверка
│   └── handlers/
│       ├── base.py
│       ├── clients.py
│       ├── sessions.py
│       ├── rituals.py
│       └── delete.py
│
├── .env
├── .gitignore
├── requirements.txt
├── run.sh                  # Запуск обоих ботов через watchfiles
└── app.log
```

---

## 4. ПРАВИЛО ДОБАВЛЕНИЯ ПОЛЕЙ

```
Новая опция Select/Multi-select:
  1. Добавить в Notion UI
  2. Добавить строку в schema.py → нужный список
  Готово. Код не трогать.

Новое поле целиком:
  1. Добавить поле в Notion UI
  2. Добавить в schema.py в словарь базы
  3. Добавить обработку в field_mapper.py
  Код ботов не трогать.

Новый пользователь:
  1. Создать запись в 🪪 Пользователи в Notion UI
  2. Указать TG ID и выставить нужные чекбоксы
  Код не трогать.
```

---

## 5. МОДЕЛИ CLAUDE

```
Парсинг текста, классификация   → claude-haiku-4-5-20251001
Vision, сложная трактовка таро  → claude-sonnet-4-6
```

---

## 6. ДЕПЛОЙ (сейчас — Mac, потом — RPi3)

```bash
# Запуск
cd /Users/dontkaiad/PROJECTS/ai-agents/AI_AGENTS && ./run.sh

# Перезапуск с обновлением
git pull origin main && find . -name "*.pyc" -delete && ./run.sh
```

RPi3 — после стабилизации всего функционала на Mac.

---

## 7. СИНХРОНИЗАЦИЯ ФИНАНСОВ

```
Arcana: оплата сеанса/ритуала
  → finance_sync.py → запись в 💰 Финансы
  → Категория: 🔮 Практика | Тип: 💰 Доход | Бот: 🌒 Arcana
  → Источник оплаты: копируется из записи
  → Пользователь: прокидывается из контекста
```

---

## 8. СТАТУС

☀️ **NEXUS:**
✅ Задачи (создание, дедлайн, напоминание, кнопки, повторяющиеся, нудж прокрастинации)
✅ Финансы (расходы/доходы, лимиты, /finance_stats, Phase 10: мультимесяц/сравнение/прогноз)
✅ Заметки (теги, поиск, редактирование, дайджест)
✅ Память (сохранение, поиск, /memory, /adhd, СДВГ-фичи, auto-suggest)
⏳ Пароли — следующий спринт

🌒 **ARCANA:**
✅ Клиенты, расклады, ритуалы (базово)
✅ Память (зеркало)
⏳ CRM фиксы (дубли клиентов, тип расклада), Гримуар
