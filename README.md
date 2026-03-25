# 🤖 nexus-arcana

Dual Telegram bot system built with **vibe coding** — AI-directed development via [Claude Code](https://claude.ai) + [Claude.ai](https://claude.ai).

## ☀️ Nexus — Personal AI Assistant
> *Life management hub*

Smart task manager, expense tracker, budget analyst, notes, memory — with ADHD-friendly features.

**What it does:**
- 📝 **Smart tasks** — understands natural language, sets priorities, deadlines, reminders, recurring tasks
- 💰 **Finance tracker** — expenses/income with category detection, budget limits, impulse overflow
- 📊 **Budget analyst** (Sonnet) — autonomous financial advisor with ADHD-adapted plans
- 🔥 **Streaks** — task completion tracking with rest days
- 🧠 **Memory** — remembers preferences, patterns, people
- 🧠 **ADHD features** — procrastination nudges, warm support, digest, profile

## 🌒 Arcana — Esoteric Practice CRM  
> *Digital grimoire*

Client management, tarot sessions, rituals, practice finances — for esoteric practitioners.

**What it does:**
- 👥 **Client CRM** — sessions history, debts, notes
- 🃏 **Tarot journal** — readings with card tracking and accuracy stats
- 🕯️ **Ritual log** — structured ritual documentation
- 📊 **Practice analytics** — income, prediction accuracy, client stats

## Stack

```
Python 3.9 · aiogram 3.x · Notion API · Claude API (Haiku + Sonnet) · APScheduler · SQLite
```

## Architecture

```
├── core/           # Shared: classifier, Notion client, Claude client, memory
├── nexus/          # ☀️ Nexus bot handlers
├── arcana/         # 🌒 Arcana bot handlers
├── run.sh          # Auto-pull + watchfiles launcher
└── requirements.txt
```

**Data:** Notion as sole database. No local DB except SQLite for pending state and scheduler jobs.

**AI routing:** Messages go through layout converter → spell correction (Haiku) → classifier with regex pre-filters → Claude routing → handler.

## Vibe Coding

Development workflow:

1. **Strategy & architecture** → Claude.ai (Pro)
2. **Code execution** → Claude Code (Mac app)  
3. **Version control** → GitHub Desktop

## Setup

```bash
cp _env .env          # Fill in your tokens
pip install -r requirements.txt
./run.sh
```

Required tokens: Telegram Bot, Anthropic API, Notion API.

## Links

- 📱 Telegram: [@witchcommit](https://t.me/witchcommit)
- 🐙 GitHub: [@dontkaiad](https://github.com/dontkaiad)

---

*Built by Kai · Powered by Claude · March 2026*
