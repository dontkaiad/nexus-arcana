# ADR-0019 — Strip reversed meanings from card reference before LLM prompt

**Status:** Accepted  
**Date:** 2026-06-26  
**Relates to:** ADR-0013 (deterministic Waite parser — same principle: control the
data source, not the prompt), ADR-0015 (voice authorship mode A — the hallucination
appeared specifically in mode A where Sonnet expands terse author notes)  
**Code conforms to:** 1a7a105  
**Verify against:** arcana/tarot_loader.py (`_REVERSED_KEYS`, `_format_card_info`)

---

## Context

In voice-authorship mode A (ADR-0015), Sonnet receives the practitioner's terse
notes (e.g. "туз — прорыв") and expands them into a full interpretation using
`PERSONAL_INTERP_SYSTEM`. The expansion prompt includes a card reference block
(`cards_context`) built by `get_cards_context → _format_card_info` from the deck
JSON files (waite.json, playing_cards.json, dark_wood.json, deviant_moon.json).

Each card in these JSONs has two meaning sets: upright and **reversed**. The deck
keys differ by file:

| Deck | Reversed key |
|------|-------------|
| Waite + playing | `"rev"` |
| Dark Wood | `"перевёрнутая"` |
| Deviant Moon | `"перевёрнутое"` |

When `_format_card_info` emitted all keys unconditionally, Sonnet saw reversed
meanings as a legitimate source — and produced interpretations that included
"перевёрнутое положение" even when the practitioner had said nothing of the kind.
The bug manifested in mode A because Sonnet was interpolating author notes against
the full card context; in mode B (pure generation) the TAROT_SYSTEM prompt's
framing was sufficient to suppress it.

A prompt-level prohibition ("не выдумывай перевёрнутые значения") was tried and
did not hold: negation priming in a system prompt is unreliable when the negated
concept is present as data in the same message.

---

## Decision

Add `_REVERSED_KEYS = frozenset({"rev", "перевёрнутая", "перевёрнутое"})` and
skip any card-info key that matches it inside `_format_card_info`. The filter
applies unconditionally to all callers, which are exclusively LLM-interpretation
paths (6 call sites: 4 in sessions.py, 2 in base.py — building card context for
Sonnet or Haiku). Reversed meanings are never needed for any current use case.

The fix is at the **data source**, not the prompt, following the same principle as
ADR-0013 (deterministic parser) and ADR-0017 (scan-before-spell): when a
stochastic model is unreliable about a constraint, remove the input that triggers
the violation rather than hoping the prompt will hold.

---

## Alternatives considered

**A) Stricter system-prompt prohibition.**  
Rejected: negation priming is unreliable — the prohibited concept is still encoded
in the card reference data Sonnet reads. Empirically did not hold in mode A.

**B) `include_reversed: bool` flag on `get_cards_context`.**  
Rejected: all call sites would pass `include_reversed=False`; the flag becomes
dead configuration. Unconditional removal is simpler and carries no branching risk.

---

## Trade-offs

If the practitioner returns to reversed-card practice, the filter must be removed
(or the flag approach from option B adopted). This is a known trade-off: the
filter encodes a current practice assumption. As of 2026-06-26 reversed cards
are not used.

The reversed meanings remain in the deck JSON files; only the LLM-facing
extraction is filtered. Raw JSON access (if ever needed for other tools) is
unaffected.
