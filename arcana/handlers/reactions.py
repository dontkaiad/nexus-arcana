"""arcana/handlers/reactions.py — карта реакций Arcana по intent."""

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
    "session":        "🔮",
    "session_search": "👀",
    "tarot_interp":   "🔮",

    # Rituals
    "ritual":         "🕯️",

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
    "grimoire":       "📖",
    "grimoire_add":   "📖",
    "grimoire_search":"👀",

    # Stats / verify
    "stats":          "🤓",
    "verify":         "✍️",

    # Delete
    "delete":         "😈",

    # Cross-bot
    "nexus":          "🌚",
}


def reaction_for(intent: str) -> str:
    """Вернуть эмодзи реакции по intent. Дефолт — ⚡."""
    return ARCANA_REACTION_MAP.get(intent or "", "⚡")
