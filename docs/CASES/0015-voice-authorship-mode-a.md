# ADR-0015 — Voice-first authorship: terse-to-full interpretation expansion (mode A)

- **Status:** Accepted
- **Date:** 2026-06-25
- **Relates to:** ADR-0006 (pgvector RAG — authored-only corpus, see Decision),
  ADR-0013 (Waite deterministic card parser)
- **Code conforms to:** `arcana/handlers/sessions.py` — `PERSONAL_INTERP_SYSTEM`,
  `_polish_authored_interpretation`, A/B branch (single/multi), authored gate `:874`
  (single) / `:1508` (multi)
- Update this ADR in the same PR that changes the A/B branch logic,
  `PERSONAL_INTERP_SYSTEM`, or the authored gate.

## Context

Arcana is a voice-first CRM for a tarot practitioner. The practitioner (Kai)
dictates readings hands-free: a short burst of terse per-card accents ("ace —
chance", "lovers — choice") rather than a full paragraph. The system must turn
that raw voice transcript into a structured, readable client record.

Two design pressures pull in opposite directions:

1. **Practitioner voice.** The value of the record is Kai's interpretive voice,
   not a model's synthesis. If the model rewrites the interpretation from scratch,
   the record stops being Kai's — it becomes a generic tarot reading that could
   have come from any LLM. Past authored interpretations are indexed into the RAG
   store (ADR-0006) and recalled when generating future readings for voice-consistency
   — so a model-substituted record poisons the recall corpus.

2. **Readability.** Terse voice notes ("ace — chance") are not suitable to
   send to a client or to index for recall. They need to be expanded into coherent
   prose grounded in actual card meanings from the deck's reference (ADR-0013 covers
   the deterministic card parser that feeds this context).

## Decision

**Mode A: expand the practitioner's terse accents into full prose, using the deck
reference as the grounding anchor — not as a replacement voice.**

`PERSONAL_INTERP_SYSTEM` + `_polish_authored_interpretation`:

- **The accent is the author's interpretive choice** — which facet of the card to
  surface. Kai's accent overrides the reference on conflict ("if Kai's accent and the
  reference diverge — Kai's accent takes priority").
- **The reference prevents hallucination** — the model may only expand into meanings
  present in either (a) the card's deck-specific meaning, or (b) Kai's accent. Nothing
  invented beyond those two sources.
- **First-person voice is inviolable** — "I see this as…" stays first-person; never
  rewritten to "Kai sees this as…". The output must sound like Kai narrating, not a
  summary about her.
- **Temperature 0.5, Sonnet** — lower than mode B's 0.7; expansion follows given
  material, not open generation. Sonnet is justified: the task requires holding the
  deck reference, the accent, and the voice style simultaneously without drifting.

**Mode B (fallback):** if no voice interpretation is present (`authored` is empty),
Sonnet generates a full interpretation from the deck reference, memory context, and
prior sessions. Mode B also injects a RAG voice-block — recalled similar past
interpretations — to approximate voice consistency when no dictated text is available.
Mode B is the original path; mode A is the preferred path when Kai dictates.

**Only mode A (authored) is indexed into RAG.** The gate on `authored`
(`sessions.py:874` single, `:1508` multi) keeps the recall corpus authored-only —
mode B interpretations are saved to the Notion/PG record but excluded from the
voice-consistency corpus, so recall never surfaces machine-generated text as a
"voice example" for future generations. Corpus purity is enforced structurally,
not by hoping mode B stays rare.

## Alternatives considered

- **Store the raw voice transcript verbatim** — rejected: terse per-card notes
  ("ace — chance") are not suitable as a client record or RAG entry; they lack the
  prose needed for recall to surface useful context.
- **Full LLM synthesis (mode B only, no authored path)** — rejected: the model's
  synthesis substitutes its own voice for the practitioner's. Past records stop being
  authored and the RAG corpus would drift toward generic LLM tarot language, degrading
  voice-consistency recall over time.
- **Index both modes into RAG** — rejected (was the prior behaviour, removed in
  commit `4d88dc0`): mode B entries in the corpus would eventually be recalled as
  "voice examples" in subsequent mode B generations, creating a drift loop. The gate
  closes this loop structurally.
- **"Polish" / light editing instead of expansion** — considered but not chosen as a
  separate path. `PERSONAL_INTERP_SYSTEM` is already expansion-first (not a copy-edit
  prompt); the anchor against drift is the reference + hallucination prohibition, not
  conservatism about length.

## Consequences

- Authored voice records are client-ready prose that still sounds like Kai, grounded
  in actual deck meanings with no model invention.
- **Corpus purity is enforced structurally:** the RAG store (ADR-0006) receives only
  authored text. Mode B records are never indexed, so the recall corpus cannot drift
  toward machine-generated language regardless of how often mode B fires.
- **Mode B records are not recallable by semantic search** — they exist in the
  Notion/PG record but are absent from the vector index. This is the intended
  behaviour: they are not the practitioner's voice and should not inform voice
  consistency.
- **Trade-off — expansion ≠ verbatim:** mode A output is not the literal voice
  transcript — it is a Sonnet expansion of Kai's accents. If Kai's accent was itself
  wrong or incomplete, the expansion amplifies it with grounded card meanings; it does
  not correct the interpretive premise. The practitioner owns the premise; the model
  owns the expansion.
