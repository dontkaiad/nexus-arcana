# SYSTEM_MAP — AI_Agents Architecture
> Версия 4.0 | Март 2026 | RPi3

---

## 1. СТРУКТУРА NOTION

```
AI_Agents (root)
├── ☀️ Nexus
│   ├── ✅ Задачи          31a42b3b1ac08051a3ccde86e6233d30
│   ├── 🧠 Память          31a42b3b1ac0801f8e3cf1441b61bc69
│   ├── 💡 Заметки         31a42b3b1ac0807ba68fd700ab695e7c
│   └── 🔐 Пароли          31a42b3b1ac0804faae6f599d91e08a8
├── 🌒 Arcana
│   ├── 👥 Клиенты         31b42b3b1ac08022baafffbcc8237bbd
│   ├── 🃏 Сеансы          31b42b3b1ac08038b4a7e88c8c382875
│   └── 🕯️ Ритуалы         31b42b3b1ac0800b81a3cf9cbcc7cd6b
├── 💰 Финансы             31a42b3b1ac080ae8b6ad8ba84d141bb
└── ⚠️ Ошибки              31a42b3b1ac080558c68fe885ece5b2a
```

---

## 2. БЕЗОПАСНОСТЬ

```
Whitelist TG IDs: [67686090, 790273371]
Любой другой ID → игнор, без ответа
Реализация: Middleware в aiogram 3.x
Токены: только .env, никогда в коде
```

---

## 3. СТРУКТУРА КОДА

```
ai_agents/
├── core/
│   ├── schema.py           # Все поля Notion, ID, варианты select
│   ├── notion_client.py    # CRUD для всех баз
│   ├── field_mapper.py     # Ответ Claude → Notion поля
│   ├── claude_client.py    # Haiku / Sonnet вызовы
│   ├── whisper_client.py   # Голос → текст
│   ├── finance_sync.py     # Arcana оплата → Финансы
│   └── config.py           # .env загрузка
│
├── nexus/
│   ├── bot.py
│   ├── middleware.py       # Whitelist
│   └── handlers/
│       ├── tasks.py
│       ├── notes.py
│       ├── finance.py
│       ├── memory.py
│       ├── passwords.py
│       └── voice.py
│
├── arcana/
│   ├── bot.py
│   ├── middleware.py       # Whitelist
│   └── handlers/
│       ├── clients.py
│       ├── sessions.py
│       ├── rituals.py
│       ├── tarot.py        # Vision
│       ├── finance.py
│       ├── grimoire.py     # Blocks API
│       └── voice.py
│
├── .env
├── .env.template
├── requirements.txt
├── run_nexus.py
├── run_arcana.py
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
```

---

## 5. МОДЕЛИ CLAUDE

```
Парсинг текста, простая логика   → claude-haiku-4-5-20251001
Vision, сложная трактовка таро   → claude-sonnet-4-6
```

---

## 6. ДЕПЛОЙ RPi3

```bash
pip3 install aiogram notion-client anthropic openai cloudinary python-dotenv

nohup python3 run_nexus.py >> app.log 2>&1 &
nohup python3 run_arcana.py >> app.log 2>&1 &

# Автозапуск (crontab -e):
@reboot sleep 30 && cd /home/pi/ai_agents && python3 run_nexus.py >> app.log 2>&1 &
@reboot sleep 35 && cd /home/pi/ai_agents && python3 run_arcana.py >> app.log 2>&1 &
```

---

## 7. СИНХРОНИЗАЦИЯ ФИНАНСОВ

```
Arcana: оплата сеанса/ритуала
  → finance_sync.py → запись в 💰 Финансы
  → Категория: 🔮 Практика | Тип: 💰 Доход | Бот: 🌒 Arcana
  → Источник оплаты: копируется из записи
```
