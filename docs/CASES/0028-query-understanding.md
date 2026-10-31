# ADR-0028 — Query understanding before memory retrieval

- **Status:** Accepted
- **Date:** 2026-10-31
- **Relates to:** ADR-0020, ADR-0021 (hybrid search), ADR-0027 (People domain)
- **Code conforms to:** `core/query_parse.py`, `core/memory_search.py` (`answer`, `rerank`),
  `core/memory.py` (`search_memory`), `miniapp/backend/routes/memory.py`
- Update this ADR in the same PR that changes query parsing or routing.

## Context

Hybrid retrieval (FTS + vector + RRF + Haiku) answers *similar* text, but many questions are
structured: «what does <person> not like», «what is important about <person>». People facts
already carry a type in `memories.key_name` (`person_<id>_<type>_<hash>`) and a `person_id`, and
`pg_people_repo.resolve` already maps mentions (with case forms) to people. Similarity search
ignores both: it mixed facts of different people, ignored polarity, and returned plausible but
wrong facts instead of «no data».

## Decision

Parse the question first, deterministically (regex lexicon + `resolve`), and route:
a resolved person + a type is a direct SQL lookup (confirmed, non-tombstone facts only); an empty
result is an honest «don't know», never similar facts. Homonyms are shown as separate labeled
groups, not guessed. The owner's own facts have no stored type, so «I»-questions use hybrid search
restricted to the owner's facts with a strict reranker (negation/person mismatch = irrelevant,
empty beats wrong). Unresolved names in a subject position that appear nowhere in the facts give
«not found in People». Hybrid remains the fallback only when the structure cannot be parsed.

## Alternatives rejected

- LLM query parsing: adds latency/cost and non-determinism for a closed vocabulary.
- Typing owner facts by regex at read time: brittle; the strict reranker is the safety net instead.
- Asking «which one?» for homonyms: a dialogue where two labeled blocks answer immediately.

## Consequences

Direct routes need no Voyage/Haiku (cheaper and faster). Wrong parse is the main risk (false
«don't know»); anything not matching the lexicon falls through to hybrid. Rollback:
`MEMORY_QUERY_UNDERSTANDING=0`. No schema change.
