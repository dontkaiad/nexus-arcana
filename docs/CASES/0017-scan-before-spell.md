# ADR-0017 — Scan Waite cards before spell-correction (Variant C)

**Status:** Accepted  
**Date:** 2026-06-25  
**Commit:** 4ea1a5f  
**Relates to:** ADR-0013 (deterministic Waite parser — this ADR closes the
remaining class-bug), ADR-0006 / ADR-0015 (RAG triplets depend on correct cards)  
**Code conforms to:** core/waite_cards.py (scan_card_spans), core/preprocess.py
(extra_protect), arcana/bot.py (scan before normalize_text)

---

## Context

Voice pipeline: Whisper → `normalize_text` (Haiku spell, T=0) →
`PARSE_SESSION_SYSTEM` (Haiku) → Waite card parser (`normalize_waite_cards_in_data`).

**Class-bug: mishear-substitution.** Whisper produces a distorted card span
("крыльево мячей" instead of "Королева Мечей"). The whitelist guard in
`normalize_text` checks for the *canonical* form ("королева мечей") as a
substring of the lowercased transcript — that substring is absent in the
distorted text, so Haiku is free to "correct" the mishear into a *different*
valid card ("Король Жезлов") before the parser ever runs. The parser receives
a clean but *wrong* card.

**Why prior fixes were insufficient.** ADR-0013 introduced dirty-span recovery
inside the Waite parser: detect known mishear tokens, recover the intended card
from the raw reference. The recovery path is structurally fragile — it requires
`len(eligible) == len(dirty_cands)` and breaks at span boundaries. More
fundamentally, it operates on the parser layer, whereas the corruption occurs
in the spell layer upstream. The root cause was not addressed.

**Cost of the bug.** Wrong card → wrong interpretation → contaminated triplet
indexed into the RAG corpus (ADR-0006 / ADR-0015). A single mishear-substitution
propagates to every future session that retrieves that triplet.

---

## Decision — Variant C: scan-before-spell

1. **`core/waite_cards.scan_card_spans(text) → List[str]`** — thin public
   wrapper over the existing `_scan_raw_pairs` private function. Runs the
   *same* deterministic 78-card dictionary (`_RANK_LEX` / `_SUIT_LEX` /
   `_MISHEAR_KEYS`) against the *raw* Whisper transcript, before any
   spell-correction. No LLM involved. Returns the matched raw spans in
   lowercased form.

2. **`core/preprocess.normalize_text(..., extra_protect)`** — new optional
   parameter. Spans present in `extra_protect` are appended to `relevant`
   using the same substring check (`span in low`), then passed to Haiku in
   the "НИКОГДА не исправляй" instruction. Existing callers are unaffected
   (default `None`).

3. **`arcana/bot.py` — scan before normalize.** In `handle_voice`, after
   capturing `_raw_transcript = text` and *before* calling `normalize_text`,
   call `scan_card_spans(text)` and pass the result as `extra_protect`. The
   call is wrapped in a silent try/except — a scan failure degrades to the
   prior behaviour, not a crash.

**Net effect.** The deterministic parser acts as a guard for the
non-deterministic spell layer. A mishear span found by `scan_card_spans` is
declared untouchable; Haiku leaves it intact. The parser then receives the
original distorted span and resolves it via `_scan_raw_pairs` to a clean
English canonical ("Queen of Swords") — via the anchor path (exact match),
not the fragile dirty-span recovery path.

**Dictionary patch (data complement, not structural fix).** Two previously
uncovered Whisper gap forms were added to `_RANK_LEX` / `_SUIT_LEX` /
`_MISHEAR_KEYS`: `"крылева"` (Queen without soft sign) and `"мачей"` (Swords
with а→я substitution). These are additive data entries, not architectural
changes.

---

## Rejected alternatives

**Variant A — protect known mishear forms in the whitelist statically.**  
Reactive by design. Each new Whisper mishear requires a whitelist entry. At
Kai's usage volume with ADHD-driven session patterns, systematic coverage is
not achievable; gaps are guaranteed.

**Variant B — expand the dictionary exhaustively for every distortion form.**  
Same failure mode as A. An unbounded reactive list is not a strategy.

> Note: A and B are applied as a *complement* to C for the two documented gap
> forms. They are not the primary mechanism.

**Dirty-span recovery (ADR-0013, parser layer only).**  
Addresses the symptom in the wrong layer. The corruption is in the spell
layer; patching the parser layer after corruption has already occurred is
structurally late. Kept as a defence-in-depth fallback but not relied upon as
the primary fix.

---

## Consequences

**Closed: class of bug, not one instance.** Any Whisper mishear whose rank or
suit token appears in `_RANK_LEX` / `_SUIT_LEX` is now protected before
spell-correction. Validated on prod: golden voice sample — "крыльево мячей" →
Queen of Swords, plus "рыцарь мячей" / "паж мячей" → Knight / Page of Swords
(the мячей→Swords class). The dictionary patch ("крылева", "мачей") closes
additional known gap forms not present in this golden run — covered by unit
tests, not yet prod-observed.

**Fundamental limit (stated honestly).** If Whisper produces a form not
covered by `_RANK_LEX` / `_SUIT_LEX`, `scan_card_spans` will not recognise
it — the span is unprotected, and the prior class-bug can recur for that
specific form. Deterministic coverage has a boundary. The mitigation is a
data patch (add the new form to the dictionary); this is low-cost but requires
observation. There is no mechanism for automatic discovery of novel mishear
forms.

**Scope: rider-waite deck only.** The `scan_card_spans` / `extra_protect`
path applies exclusively to the rider-waite branch. Authored-deck sessions use
`core/card_grounding.py` (SequenceMatcher) and are unaffected.

**No runtime cost.** `scan_card_spans` is fully deterministic string matching;
no LLM call, no I/O. Added latency is negligible.
