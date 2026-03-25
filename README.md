# nexus-arcana

Dual Telegram bot system — two AI assistants sharing a Notion backend.

**Telegram:** [@hey_lark](https://t.me/hey_lark)

## ☀️ Nexus — Personal AI Assistant

Smart life management hub with ADHD-friendly features.

- 📋 **Tasks** — natural language, deadlines, reminders, recurring, streaks
- 💰 **Finance** — expense/income tracking, category detection, budget limits
- 💰 **Budget** — Sonnet-powered financial advisor with impulse reserves
- 🗒️ **Lists** — shopping lists, checklists, inventory with multi-select checkout
- 📝 **Notes** — auto-tagged, weekly digests
- 🧠 **Memory** — preferences, patterns, people, ADHD profile
- 🔥 **Streaks** — daily task completion tracking with rest days

## 🌒 Arcana — Esoteric Practice CRM

Digital grimoire for esoteric practitioners.

- 👥 **Client CRM** — sessions history, debts, notes
- 🃏 **Tarot journal** — photo recognition, readings, accuracy stats
- 🕯️ **Ritual log** — structured documentation with results
- 🗒️ **Lists** — ritual supplies inventory, checklists
- 💰 **Practice finances** — income tracking, category analytics

## Stack

```
Python 3.9 · aiogram 3.x · Notion API · Claude API (Haiku + Sonnet) · APScheduler · SQLite
```

## Architecture

```
├── core/               # Shared: classifier, Notion client, Claude, memory, lists
│   ├── classifier.py       # Regex pre-filters + Claude routing
│   ├── notion_client.py    # CRUD for all Notion databases
│   ├── claude_client.py    # Haiku / Sonnet calls
│   ├── list_manager.py     # Shopping lists, checklists, inventory
│   ├── list_classifier.py  # List-specific regex patterns
│   └── config.py           # .env loading
├── nexus/              # ☀️ Nexus bot
│   └── handlers/           # tasks, finance, memory, notes, lists, streaks
├── arcana/             # 🌒 Arcana bot
│   └── handlers/           # clients, sessions, rituals, lists, memory
├── run.sh              # Auto-pull + watchfiles launcher
└── requirements.txt
```

**Data:** Notion as sole database. SQLite only for pending state and scheduler jobs.

**AI routing:** Layout converter → spell correction (Haiku) → regex pre-filters → Claude classification → handler.

## Setup

```bash
cp _env .env          # Fill in your tokens
pip install -r requirements.txt
./run.sh
```

Required tokens: Telegram Bot, Anthropic API, Notion API.

## Links

- Telegram: [@hey_lark](https://t.me/hey_lark)
- GitHub: [@dontkaiad](https://github.com/dontkaiad)

---

*Built by Kai Lark with Claude Code*
