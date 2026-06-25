# ADR-0013: Deterministic card parser for the Rider-Waite deck

## Status
Accepted (implemented in `core/waite_cards.py`, wired in `arcana/handlers/sessions.py`; feature commit `ad139c8`).

## Context
Tarot sessions are dictated by voice. The pipeline is: Whisper → spell-correction
(Haiku, `core/preprocess.py`) → structure parse (Haiku, `PARSE_SESSION_SYSTEM`) →
per-card meaning lookup and interpretation. Card names routinely arrive mangled:
Whisper mishears ("крыльева мячей" for "королева мечей"), and the spell step can
rewrite a misheard phrase into a **valid-but-wrong** card before the parser ever
sees it ("крыльева мячей" → "король жезлов").

The previous defense was `core/card_grounding.py`: after parsing, each card was
compared to the raw transcript by blind string similarity (`SequenceMatcher`) and,
if it failed a threshold, replaced with a verbatim transcript fragment. This
"grounding guesser" was patched in four successive layers — prompt rules → a
lookahead window → switching the reference to the raw (pre-spell) transcript → an
ordering cursor — and **each layer introduced new regressions** (correct cards on
long transcripts scored low and were destroyed; the bottom card was swapped with
the third card; hallucinated cards leaked in).

Root cause: the parser emits a syntactically valid card that is simply the wrong
one, and grounding tried to repair that by comparing surface strings **without any
model of what a tarot card is**. Similarity has no notion of "this is the Queen of
Swords, not the King of Wands"; it only sees shared characters, so it cannot
reliably separate a real mis-spell from coincidental overlap.

The Rider-Waite deck is, by contrast, perfectly regular: 22 major arcana plus
56 minors that decompose into RANK + SUIT. That structure is parseable
deterministically. Authored decks (Dark Wood, Deviant Moon, Lenormand, playing
cards) are not — they carry non-standard names and meanings and are out of scope
here (they keep their existing path).

## Decision
For the Rider-Waite deck **only**, replace grounding with a hard deterministic
parser.

- **Closed dictionary of 78 cards in code** (`core/waite_cards.py`): 22 majors +
  4 suits × 14 ranks, each mapped to its canonical English name. A guard test
  asserts the set equals the `deck_cards.json` registry (single source of truth).
- **Fixed structure**: 3 cards + an optional bottom card, the bottom recognized
  only by an explicit marker ("дно"/"на дне") that the structure parser already
  emits.
- **Normalization**: each card phrase is resolved to one of the 78 by RANK + SUIT
  (or major-arcana name), through lexicons that include phonetic mishear aliases
  ("крыльева"→Queen, "мячей"→Swords). Output is the canonical **English** name
  ("Queen of Swords"). Resolution happens before/independently of spell-correction.
- **Spell-swap recovery**: a slot whose resolved card is not corroborated in the
  raw transcript (a spell-swap) is repaired from the raw transcript — but only
  from **"dirty" spans** (spans containing a mishear word), assigned positionally
  to the unanchored spell-swap slots, and only when their counts match.
- **LLM fallback**: an unknown mishear the lexicons cannot resolve goes to a
  narrow Haiku classifier ("this phrase → one of these 78, or null"), whose output
  is hard-checked for membership in the 78.
- **Deck fork**: `resolve_deck_id(deck) == "rider-waite"` routes to the new parser;
  every other deck keeps the unchanged grounding + RU-canonicalization path.

## Key insight (the reason this is deterministic, earned over five adversarial passes)
Spell-correction only corrupts **misheard** words — the canonical 78 names are
whitelisted in the spell step and never rewritten. Therefore the **origin of any
spell-swap is always a "dirty" span** (it contains a mishear token), while a card
named cleanly in interpretation narrative ("король кубков" as an archetype) is
**clean** and is never a valid recovery source — even when it shares a rank or suit
word with the corrupted phrase.

This single observation removes every similarity threshold:
- Correctly-heard cards match the transcript verbatim → they anchor; recovery never
  touches them.
- Only spell-swapped slots fall into gaps, and they are repaired solely from dirty
  spans, positionally. Clean narrative phantoms are structurally excluded — not
  down-weighted by a tunable score.
- A slot whose true card Whisper dropped (a garble the parser also could not
  resolve) has `enP = None`; such "novel" slots are not eligible for dirty-span
  recovery (their card is a garble, not a dirty resolvable span), so they cannot
  steal a neighbor's recovery span. When the dirty-span count does not match the
  eligible spell-swap count (an origin was dropped, or an extra mishear appears),
  the parser keeps its own value rather than guessing.

## Alternatives considered
1. **Vector search (Qdrant) over the 78 names.** Rejected: overkill for a fixed,
   tiny, closed set; non-deterministic and paid per query for something a hash map
   answers exactly. The vector backend (ADR-0006) stays where it belongs — RAG over
   open-ended interpretation corpora, where similar-by-meaning is the right tool.
2. **Prompt rules telling the parser "do not invent cards".** Rejected: Haiku at
   temperature 0 ignores negative instructions (negation priming); the four
   firefighting commits on grounding are the empirical record of this.
3. **Validating the parsed card against the 78.** Rejected: it catches nonsense,
   not the actual failure — a valid-but-wrong card ("King of Wands") passes the
   membership check while being the wrong card.
4. **Keep grounding (SequenceMatcher).** Rejected: blind string repair produced
   five distinct classes of bug across its patch history — positional shift from
   narrative phantoms, bottom↔card swap, novel-slot-in-a-run misassignment,
   value-blind phantom injection, and same-rank/same-suit phantom theft. None is
   fixable by a threshold because position and surface similarity both fail to
   distinguish a real origin from a phantom.
5. **One deck-agnostic parser for all decks.** Rejected: authored decks have
   non-standard names and meanings; a Waite-shaped RANK+SUIT parser would corrupt
   them. The fork keeps each deck on the path that fits it.

## Consequences
- (+) Waite cards resolve through one hard path with no similarity thresholds;
  the five grounding bug classes are eliminated by construction.
- (+) Free and deterministic for the common case (lexicon hit); the LLM is a rare
  fallback for unknown misheards only.
- (+) Authored decks are untouched — the fork isolates the change; their tests
  stay green.
- (−) The hard dictionary covers Waite only; authored decks remain on grounding.
- (−) A genuinely new mishear not in the alias lexicons either gets added as an
  alias or falls through to the Haiku fallback (it does not silently corrupt — an
  unresolved card is left verbatim for manual correction).
- (−/intended) The Waite session header now shows English card names
  ("Ace of Swords") instead of Russian, so the practitioner can read and verify
  the exact card by eye. Authored decks still render in Russian.
- Residual: if Whisper drops a card whose neighbor in the same gap is also a
  spell-swap, and the counts cannot be reconciled, the parser keeps its own
  (possibly wrong) value rather than stealing the neighbor's span — a safe,
  correctable default rather than a silent wrong assignment.
