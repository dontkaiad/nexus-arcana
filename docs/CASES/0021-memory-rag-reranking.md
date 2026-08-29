# ADR-0021 — Haiku reranking for 🧠 Память semantic search

- **Status:** Accepted
- **Date:** 2026-08-29
- **Relates to:** ADR-0020 (memory RAG search, #184, #185)
- **Code conforms to:** `core/memory_rag.py` (`search_memory_semantic`,
  `rerank_memory_candidates`), `core/memory.py` (`_semantic_search_memory`)
- Update this ADR in the same PR that changes the memory-RAG ranking
  strategy.

## Context

ADR-0020 shipped semantic fallback search over `memories.embedding`
(pgvector, cosine distance, `ORDER BY distance LIMIT top_k`) with a
documented open question (#185): no similarity threshold is applied, so
the query always returns its `top_k` nearest neighbors, even when none of
them are actually relevant to the query.

Investigating a concrete failure closed that open question in an
unexpected direction. For the query "что я помню про луну", pgvector's
top-1 result by cosine distance was an unrelated 🦋 СДВГ chronotype note —
not either of the two facts that are actually about "Луна" (a cat's name
and its medication). Comparing the raw score log
(`search_memory_semantic`'s `ids=... scores=...` line) confirmed the
gap: the irrelevant note's score was not a clear outlier in the tail of
the distribution — it was close enough to the genuinely relevant facts
that no single cutoff value separates them on this data. The failure mode
isn't "the correct answer barely misses the cutoff," it's "distance
ranking and semantic relevance disagree at the top of the list." A
general-purpose embedding model does not reliably distinguish "луна" the
common noun from "Луна" the pet's name — short, atomic facts like this one
carry little enough distinguishing text that the vector alone can't
separate them.

## Decision

**Widen the candidate pool, then rerank by meaning instead of by
distance.** `search_memory_semantic`'s default `top_k` moves from 5 to 10
(`DEFAULT_TOP_K` in `core/memory_rag.py`) — a wider net gives the reranker
more to work with, since the correct answer isn't guaranteed to be in the
old top-5 by raw distance. A new `rerank_memory_candidates(query,
candidates)` sends the widened candidate set to Haiku
(`claude-haiku-4-5-20251001`, `temperature=0` for deterministic
filtering) with a structured-JSON prompt (same pattern as
`parse_finance`/`parse_task` in `core/claude_client.py`): return only the
`id`s of candidates that are actually relevant, nothing else. Each
candidate line includes `fact_text` **and** `category`/`related_to` —
this is the signal the embedding lost: a fact tagged 🐾 Коты next to the
word "луна" unambiguously reads as a pet's name to a model that can read
the category, even when the vector alone couldn't encode that
distinction from a three-word fact.

`core/memory.py:_semantic_search_memory` wires this in as a second
gated step, keeping the existing #184 economics: Voyage still only fires
when ILIKE found fewer than 3 hits, and now Haiku only fires when Voyage
returned at least one candidate. The rerank result — not the raw pgvector
result — is what gets merged into the ILIKE hits. If Haiku rejects every
candidate, the semantic add-on is simply empty; the function falls back
to the ILIKE-only list with no placeholder text ("nothing relevant
found") — the existing hybrid design already treats an empty semantic
contribution as a normal, silent case, so introducing one here needed no
new UI path.

Both failure modes are graceful by construction, following the same
convention as every other RAG helper in this file: `rerank_memory_
candidates` returns `[]` on an empty candidate list (no API call), on a
Haiku/network failure (`ask_claude` itself already swallows
`anthropic.APIError` and returns `""`), and on unparseable JSON. `[]` and
"no candidates were relevant" are indistinguishable to the caller by
design — a reranker that can't decide should behave exactly like a
reranker that decided against everything, not raise.

Logging carries the score distribution (unchanged, now with row `id`s
attached for correlation) *and* which of those ids the reranker kept
(`rerank_memory_candidates`'s own INFO log) — both distance-based and
meaning-based signal land in the same log stream, so a future
investigation doesn't need to re-derive either from scratch.

## Alternatives considered

- **Cosine distance threshold (`min_score`, the #185-proposed fix)** —
  rejected for this problem specifically. The mechanism already exists
  (`search_memory_semantic(min_score=...)`, unused by any caller) and
  would have been the cheaper fix if it worked, but the "луна" score log
  showed the irrelevant top-1 sitting inside the same score band as the
  relevant results — there is no threshold value that keeps the cat facts
  and drops the chronotype note on this data. A threshold fixes "the tail
  is noisy;" it does not fix "the head is wrong." `min_score` stays in the
  function signature (harmless, still logged) in case a future, coarser
  pre-filter ahead of reranking turns out useful, but it is not the
  mechanism doing the relevance decision here.
- **Enrich `_embed_text` with more context before embedding** (e.g.
  prefixing facts with a category-derived phrase instead of just
  appending the raw category string) — rejected for this change, not
  rejected outright. It attacks the actual root cause (short facts carry
  a weak vector signal) rather than compensating for it downstream, but
  it requires a full re-embed of every existing row
  (`scripts/migrate_memory_embeddings.py`, rate-limited backfill) and
  doesn't guarantee separation on its own — "в Алании" is short no matter
  how it's prefixed. Left as future work; worth revisiting if reranking
  alone proves insufficient on a wider sample of queries.
- **Cross-encoder reranking model** instead of an LLM call — not pursued;
  the project has no existing infrastructure for a dedicated reranker
  model, while Haiku is already the standing choice for every other
  cheap classification/extraction step in this codebase (`core/router.py`,
  `core/deleter.py`, `core/reply_update.py`) and needed no new
  infrastructure to add here.

## Consequences

- One additional Haiku call per semantic search that actually returns
  candidates — but semantic search itself is already gated behind ILIKE
  returning fewer than 3 hits (ADR-0020), so this is not a cost added to
  every memory search, only to the already-rare fallback path. Haiku is
  the cheap-routing model per `CLAUDE.md`'s cost rules; no Sonnet call is
  introduced (guarded by `tests/test_models_audit.py`).
- Semantic search latency increases on the fallback path by one sequential
  LLM round-trip (pgvector query → Haiku call), versus the previous
  single pgvector query. Fallback-only, not felt on the common ILIKE-hit
  path.
- `min_score` remains unwired and effectively dead for now — #185 is not
  fully closed, just superseded as the *primary* mechanism for this
  specific relevance problem. If a future case shows reranking alone
  passing through too much irrelevant volume before Haiku even sees it
  (e.g. `top_k` needs to grow further), a coarse `min_score` pre-filter
  ahead of the Haiku call is still available without new plumbing.
- Reranking is read-path only, same as the semantic fallback it extends —
  `deactivate_memory`/`delete_memory`'s `use_semantic=False` opt-out
  (ADR-0020) is untouched; destructive operations still never see a
  semantic or reranked result.
