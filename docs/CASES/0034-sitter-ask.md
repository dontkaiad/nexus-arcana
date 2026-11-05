# Case 0034 — Sitter search without an LLM (`POST /api/sitter/ask`)

- **Status:** Accepted (Nexus side); the consumer is the private cat-sitter bot (separate repository, later step)
- **Date:** 2026-11-05
- **Relates to:** ADR-0033 (read-only service token), Case 0032 (cats as a subject), `docs/specs/SITTER_LISTS.md`
- **Code:** `core/sitter_search.py`, `miniapp/backend/routes/sitter.py`, `evals/synthetic/sitter_ask_world.py`, `tests/test_sitter_ask.py`

## Problem

A cat-sitter needs quick answers such as "where are the headache pills?" without reading the whole guide and without anyone
paying for, or trusting, an LLM. The answer must only come from what the owner chose to share. A general memory search is
the wrong tool: it covers people, finances and everything about the owner.

## Decision

A dedicated endpoint, a fixed SQL over exactly three sources, no LLM, answer as fragments.

| Rule | Detail |
|---|---|
| Sources | facts with `cat_id` · "General about cats" (category cats, `cat_id` empty) · `nexus_lists` rows allowed by `sitter_visible_clause` (inventory unless `sitter_hidden`; shopping and checklists only if `for_sitter`). People, finances, "Me", other lists and other owners are not reachable, not merely filtered. |
| Token | the shared read-only sitter token (`SITTER_LISTS_TOKEN`), the same hard gate as `/api/sitter/lists`; the cats token and the write token do not work. |
| Retrieval | FTS (`russian`, `ts_rank`) + vector (Voyage cosine); an item passes if `cosine >= 0.58` **or** `ts_rank >= 0.10`. Question words ("лежит", "любит", "стоит" …) are removed from the FTS query: they match unrelated rows. |
| Answer | up to 3 fragments `{source, label, text}` (≤ 300 chars, no ids, prices or user ids); nothing above threshold → `found=false` (the bot says "Не нашёл информации, спроси у Кай"). |
| Cost | no LLM; one query embedding (~$0.000001) + item vectors cached per `(id, updated_at)`; at most 30 questions per minute per process (HTTP 429 with `Retry-After`). The query text is never logged, only its length. |

## Calibration (fictional data, real Voyage embeddings)

Corpus: 3 cats, 6 cat facts, 5 general facts, 10 inventory items, 3 list items (`evals/synthetic/sitter_ask_world.py`);
16 answerable and 10 unanswerable questions. The script runs outside the repo and sends only fictional text.

| Variant | recall@3 (16) | false answers (10) |
|---|---|---|
| Before: OR-FTS over all words, cosine ≥ 0.50 | 16 | 4 |
| FTS with question-word stoplist, cosine ≥ 0.50 | 16 | 1 |
| FTS with stoplist, cosine ≥ 0.55 | 15 | 1 |
| **After: stoplist, cosine ≥ 0.58, ts_rank ≥ 0.10** | **15** | **0** |
| stoplist, cosine ≥ 0.60 | 15 | 0 |
| stoplist, cosine ≥ 0.65 | 14 | 0 |

The stoplist removed the lexical false answers ("где лежит паспорт" matched "корм лежит в шкафу"); the cosine threshold removes the
semantic ones. The closest unanswerable neighbour scores 0.569 and the weakest answer that only the vector can find scores 0.622,
so 0.58 sits between them (margin about 0.01 to the neighbour, which is thin: re-check it when the data changes). One miss remains
by design ("чем посветить если выключили свет" → "Фонарик": cosine 0.524, no word in common); lowering the threshold to catch it
brings back "успокоительное рецептурное".

Examples (fictional):

| Input | Output |
|---|---|
| где таблетки от головы | inventory: Цитрамон — таблетки от головной боли, верхний ящик |
| чего боится Тучка | про кота Тучка: Тучка боится пылесоса и прячется под диван в спальне |
| где лежит паспорт | `found=false` (no such item is shared) |

## Real data check (outside the repo, ranks only)

On the owner's data (9 general facts, 11 cat facts, 58 visible inventory items) three kinds of probes ran through the real code:
off-topic questions (weather, borscht, plane ticket, exchange rate) → 0 answers of 4; in-topic questions → 5 of 7 answered
(headache pills and "what to take for a headache" from inventory, cat food, litter, "who to call if a cat is ill" from general facts);
the two unanswered ("где аптечка", "чем обработать порез") had a best cosine of 0.48 and 0.53, i.e. no matching item is shared.
No names or texts were printed. The thresholds were kept.

## Isolation

`tests/test_sitter_ask.py` builds a world with sentinels (a hidden prescription item, an unflagged purchase, a person's memory, a finance
memory, a "Me" memory, another owner's rows, a rejected cat, archived/old/pending facts) and asserts none of them is returned for any
question, including their own wording. A route-enumeration test pins the set of routes the read token opens:
`GET /api/sitter/lists`, `GET /api/care/schedule`, `POST /api/sitter/ask`.

## Not covered

The bot side (`/ask`, free text) is a separate step in the private repository. Thresholds are calibrated on a small fictional set;
they should be re-checked after the owner's inventory grows or the embedding model changes.
