# ADR-0029 — Grounded answers in the bot's own words

- **Status:** Accepted
- **Date:** 2026-10-31
- **Relates to:** ADR-0028 (query understanding), ADR-0021 (rerank)
- **Code conforms to:** `core/memory_answer.py`, `core/claude_client.py` (`apply_model_params`),
  `core/memory.py` (`search_memory`, `expand_all_facts`), `alembic/versions/f4c7a1d92e58_memory_answer_log.py`
- Update this ADR in the same PR that changes the answer prompt, the grounding checks or the model.

## Context

After routing, the bot listed facts verbatim. A grouped, short paraphrase reads better, but a language model may add
claims the facts do not support — unacceptable for personal memory.

## Decision

Generate only from the structured result, and make every phrase carry the ids it rests on. Code, not the model,
assembles the final text (headers, the "does / does not like" lead, the source line). Any grounding violation, timeout or
API error discards the answer and shows the existing list. Strict checks first, relaxed from the fallback log
(`memory_answer_log`, daily stats) rather than from guesses. Deterministic branches and single facts skip the model.

Model: Haiku 5.5 with thinking disabled (≈0.7 s vs ≈3 s with the default adaptive thinking, measured in production);
5.x models reject `temperature`, so `claude_client` strips it and maps `thinking="off"` per model family. Other calls
stay on their current models.

## Consequences

Cost per generated answer is a fraction of a cent; the dominant risk is false fallbacks from the strict word check
(irregular forms such as один/одному) — visible in the log, tunable per check number. Rollback by env: `MEMORY_GENERATE_ANSWER=0`
or `MEMORY_ANSWER_MODEL`.
