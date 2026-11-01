# Case 0031 — Why memory RAG "did not work", and what fixed it

- **Status:** Closed
- **Date:** 2026-11-01
- **Relates to:** ADR-0020, ADR-0021, ADR-0028, ADR-0029; measured by [docs/EVALS.md](../EVALS.md)

## Symptom

Questions to the bot about people ("what does X not like", "what is important about Y", "what do I not eat") returned
plausible-looking lists that were wrong: facts about other people, facts with the opposite sign ("likes" for "not
likes"), a group header as the only structure, and "nothing found" for questions that had answers.

## Diagnosis (in the order it was found)

1. **Keyword search without relevance.** Substring `ILIKE` over three columns, newest first, limit 10. A common word in
   the question matched unrelated facts; ranking was recency, not relevance.
2. **Vector search almost never ran.** It was a fallback gated on "fewer than 3 keyword hits" (a free-tier rate limit
   workaround); substring OR matching nearly always produced three hits.
3. **The embedding text carried noise.** Fact + aliases tail + category diluted the vector. Person facts, stored
   without the name, lost the subject entirely once the noise was removed — the name had to be put back.
4. **The question was never understood.** Subject ("me" / a person / unknown) and polarity ("not like" vs "like") were
   ignored, so the retriever could not tell "X's dislikes" from "everything mentioning X".
5. **Two data problems under the retrieval.** Fact types set by an LLM at import time were unreliable ("does not like"
   typed as "likes"), and multi-word aliases like "<name>'s husband" indexed the inner name word by word, creating
   false homonyms.
6. **The user's words never reached the retriever.** The classifier handed over a trimmed keyword list, dropping
   exactly the type and polarity words the new routing needed.

## Fix

| Layer | Change |
|---|---|
| Keyword | Postgres full-text search (Russian, ranked), question words filtered; `ILIKE` kept only for destructive paths |
| Vector | Always on, hybrid with FTS via reciprocal rank fusion, optional LLM rerank; embedding text = the fact (plus the person's name) |
| Understanding | Deterministic parser: subject via the people resolver, type from a negation-aware lexicon; person + type → direct query by id, "I" → restricted hybrid, birthday → card |
| Data | Types recomputed from text (lexicon overrides the LLM where it fires); multi-word aliases match only as a phrase |
| Plumbing | The search receives the original message, not the classifier's keywords |
| Answer | Paraphrase only from the found facts, every phrase cites fact ids, a strict validator discards anything unsupported |

## Numbers

Synthetic world of 9 questions (offline, vector off, so hybrid is understated):

| Stage | recall@5 | precision@5 |
|---|---|---|
| Before: `ILIKE`-OR, newest first | 72.2% | 59.3% |
| Hybrid (FTS + RRF), no question understanding | 86.1% | 61.5% |
| With question understanding | 100% | 100% |

Reproduce: `python -m evals.memory_eval compare`. On the real data the answer-grounding check accepted 4 of 4 generated
answers at p50 ≈ 1.6 s after tuning (see EVALS.md); real-data recall is tracked privately.

## Takeaway

Retrieval quality was not a vector-database problem. Three of the six causes were upstream of retrieval (what the
question means, what the stored data says, what text reaches the retriever), and none of them showed up as an error —
only as plausible wrong answers. The cheap, deterministic parts (question parsing, data hygiene) moved the metric
more than the embedding model did.
