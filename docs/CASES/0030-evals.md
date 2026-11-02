# ADR-0030 — Evals: classifier / memory retrieval / answer grounding

- **Status:** Accepted
- **Date:** 2026-11-01
- **Relates to:** ADR-0020/0021 (memory search), ADR-0028 (query understanding), ADR-0029 (grounded answers), Case 0032 (cats group, ambiguity metric)
- **Code conforms to:** `evals/` (`classifier_eval.py`, `memory_eval.py`, `grounding_eval.py`, `role_eval.py`),
  `tests/test_evals_offline.py`, `.github/workflows/test.yml` (offline evals step), `core/models.py`, `core/llm_usage.py`
- Update this ADR in the same PR that adds an eval or changes what an existing eval measures.

## Context

The memory pipeline (classifier → question understanding → hybrid search → generated answer → grounding check) kept
changing by feel: a stage looked better on a handful of questions and quietly broke another. Model migration made it
worse — "is Haiku 5.5 as good as 4.5 here?" had no answer except trying it in production. Unit tests mock the LLM,
so they cannot tell whether a prompt or a model change degrades behaviour.

## Decision

Three small, repeatable evals plus one for the short model roles, each with a **synthetic offline version that runs in
CI** and a **private version on real data** that never enters the repository.

| Eval | Question it answers | Metric | CI (synthetic, offline) | Private / live |
|---|---|---|---|---|
| Classifier | Does a model/prompt change alter what the bot understands? | agreement of intent types between two models, latency, tokens, $ | deterministic branches return the expected type with **zero** LLM calls | A/B replay of real messages on the server |
| Memory retrieval | Is the right fact among the top 5? | recall@5, precision@5, route accuracy, ambiguity handling (per group: people, cats) | synthetic people/facts, FTS only | owner's questions with expected fact ids |
| Answer grounding | Does the validator reject invented content and accept honest paraphrase? | accept rate, reject recall, check-id match | good + adversarial answers | fallback rate, dropped phrases, latency on real questions |
| Short roles | Does a cheaper model keep parsers/router stable? | agreement on the functions the bot really calls | harness only | A/B on synthetic inputs |

Why these metrics:

- **recall@5, capped** (`|expected ∩ top5| / min(5, |expected|)`): the bot shows at most five facts; a question with
  seven right answers cannot score above what five slots allow. "Correctly returns nothing" scores 1.0 only when the
  result is empty.
- **Agreement, not accuracy, for the classifier**: there is no ground truth for free-form messages. The eval answers
  "did the answer change", and a human reads the differences. It is a regression detector, not a judge.
- **Reject recall for grounding**: the dangerous failure is accepting an invented number or name, so adversarial
  answers are scored separately from honest ones.
- **Deterministic first**: anything that can be checked without a model (regex branches, routing, validators, SQL
  filters) is checked without one, so the CI step costs nothing and never flakes.

Privacy: the repository holds only synthetic cases. Private cases and results live in `data/evals/` (gitignored); the
output helper refuses to write private results anywhere else, and a test scans the synthetic files for long numbers and
`@`. Message capture for the classifier eval is opt-in (`EVAL_CAPTURE=1`), text only, no user id.

## Alternatives rejected

- LLM-as-judge for correctness: another model's opinion on personal facts, cost, and its own drift.
- One big golden set on real data in the repo: impossible for privacy reasons.
- Only unit tests with mocked LLMs: pass while the real model regresses.

## Consequences

- Model changes are decided from numbers per role, not per mood. First use found two real defects that no unit test
  could: the classifier prompt carried only the date (relative times like "in an hour" were guessed or came back as a
  template placeholder), and a chatty model reply to a bare "thanks" was logged as a parse error.
- The synthetic world is small by design; its numbers show that the machinery works and catch regressions, they are not
  a claim about quality on real data. Real-data numbers stay private and are summarised only as aggregates.
- The classifier A/B depends on collected messages; logs do not survive redeploys, hence the opt-in capture.
