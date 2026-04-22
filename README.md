# Nexus Arcana

Dual Telegram bot system — two AI assistants sharing a Notion backend.

Created by [Kai Lark](https://github.com/dontkaiad) · [@hey_lark](https://t.me/hey_lark)

## ☀️ Nexus — Personal AI Assistant

Smart life management hub with ADHD-friendly features.

- 📋 **Tasks** — natural language, deadlines, reminders, recurring, streaks
- 💰 **Finance** — expense/income tracking, budget limits, photo receipt parsing
- 💰 **Budget** — AI-powered financial advisor with impulse reserves
- 🗒️ **Lists** — shopping lists, checklists, inventory with multi-select checkout
- 📝 **Notes** — auto-tagged, biweekly digest reminders
- 🧠 **Memory** — preferences, patterns, people
- 🦋 **ADHD** — personal profile, nudges, support
- 🎤 **Voice** — Whisper transcription → full pipeline
- 📸 **Photo** — bank screenshots & receipts → auto finance entries

## 🌒 Arcana — Esoteric Practice CRM

Digital grimoire for esoteric practitioners.

- 👥 **Client CRM** — sessions, debts, notes
- 🃏 **Tarot journal** — readings, accuracy stats
- 🕯️ **Ritual log** — structured documentation
- 🗒️ **Lists** — ritual supplies, checklists
- 💰 **Practice finances** — income tracking

## Stack

```
Python 3.9 · aiogram 3.x · Notion API · Claude API (Haiku + Sonnet) · OpenAI Whisper · APScheduler · SQLite
```

## Architecture

```
├── core/               # Shared: classifier, Notion, Claude, memory, lists, voice, vision
├── nexus/              # ☀️ Nexus bot + handlers
├── arcana/             # 🌒 Arcana bot + handlers
├── miniapp/            # Telegram Mini App (FastAPI backend + React frontend)
├── run.sh              # Auto-pull + watchfiles launcher
└── requirements.txt
```

**Data:** Notion as primary database. SQLite for pending state and scheduler jobs.

**AI routing:** Layout converter → spell correction → regex pre-filters → Claude classification → handler.

## Setup

```bash
cp _env .env          # Fill in your tokens
pip install -r requirements.txt
./run.sh
```

Required: Telegram Bot, Anthropic API, Notion API, OpenAI API (for voice).

## Mini App

Backend встроен в Nexus (FastAPI на `:8000`, см. `miniapp/backend/`).
Фронтенд — React + Vite в `miniapp/frontend/` (11 экранов, glass-стиль).

### Dev локально

```bash
./run.sh                                 # бэкенд
cd miniapp/frontend && npm install
cd miniapp/frontend && npm run dev       # → http://localhost:5173
```

### Через Telegram-туннель

Обычный `npm run dev` виснет через Cloudflare tunnel из-за стриминга большого
`App.jsx`. Для туннеля используем prod-build в watch-режиме:

```bash
cd miniapp/frontend && npm run dev:tunnel    # build --watch + preview
cloudflared tunnel --url http://localhost:5173 --protocol http2
```

URL туннеля настраивается в BotFather как menu button.
