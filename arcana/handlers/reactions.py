"""arcana/handlers/reactions.py — карта реакций Arcana по intent."""

"""Разрешённые Telegram-эмодзи реакций (тот же набор что в Nexus):
⚡ 🔥 👌 🏆 ✍️ 💅 🫡 🌚 🤓 😈 🤔 🤡 👀 ❤️‍🔥
Использовать ТОЛЬКО их — иначе set_message_reaction падает молча.
"""

ARCANA_REACTION_MAP = {
    # Processing / meta
    "processing":     "👀",
    "unknown":        "🤔",
    "error":          "🤡",
    "parse_error":    "🤡",

    # Clients
    "new_client":     "👀",
    "client_info":    "👀",

    # Sessions / Tarot
    "session":        "✍️",
    "session_search": "👀",
    "tarot_interp":   "✍️",

    # Rituals
    "ritual":         "💅",

    # Works
    "work":           "⚡",
    "work_done":      "🔥",
    "work_list":      "🫡",

    # Finance
    "finance":        "👌",
    "expense":        "👌",
    "income":         "🏆",
    "debt":           "🏆",

    # Grimoire
    "grimoire":       "✍️",
    "grimoire_add":   "✍️",
    "grimoire_search":"👀",

    # Stats / verify
    "stats":          "🤓",
    "verify":         "✍️",

    # Delete
    "delete":         "😈",

    # Memory
    "memory_save":       "💅",
    "memory_search":     "💅",
    "memory_deactivate": "💅",
    "memory_delete":     "😈",

    # Cross-bot
    "nexus":          "🌚",
}


def reaction_for(intent: str) -> str:
    """Вернуть эмодзи реакции по intent. Дефолт — ⚡."""
    return ARCANA_REACTION_MAP.get(intent or "", "⚡")
