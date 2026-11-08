# Case 0034 — Sitter search without an LLM (`POST /api/sitter/ask`)

- **Status:** Accepted (Nexus side); the consumer is the private cat-sitter bot (separate repository, later step)
- **Date:** 2026-11-05
- **Relates to:** ADR-0033 (read-only service token), Case 0032 (cats as a subject), `docs/specs/SITTER_LISTS.md`
- **Code:** `core/sitter_search.py`, `core/sitter_expand.py`, `miniapp/backend/routes/sitter.py`, `evals/synthetic/sitter_ask_world.py`, `tests/test_sitter_ask.py`

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
| Answer | up to 3 fragments `{source, label, text}` (+ `location` for inventory; ≤ 300 chars, no ids, prices or user ids) + `more` (how many more passed); nothing above threshold → `found=false` (the bot asks the owner). `offset` pages through the rest. |
| Cost | the **answer** is never written by an LLM. One query embedding (~$0.000001) + item vectors cached per `(id, updated_at)`; since revision 2 one Haiku call per new question to expand the query (~$0.00036, cached, see below); at most 30 questions per minute per process (HTTP 429 with `Retry-After`). The query text is never logged, only its length. |

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
so 0.58 sits between them (margin about 0.01 to the neighbour, which is thin: re-check it when the data changes). One miss remained
by design at this stage ("чем посветить если выключили свет" → "Фонарик": cosine 0.524, no word in common); lowering the threshold to
catch it brings back "успокоительное рецептурное". Revision 2 below fixes it by query expansion instead of a lower threshold.

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

## Revision 2 (2026-11-08): live check of the bot

Four findings from the first live use, and what was done (fictional corpus, real Voyage embeddings and real Haiku; 16 questions with an
answer, 4 paraphrases, 10 + 3 without an answer, 4 "not confused" questions; the script runs outside the repo):

| Variant | recall (16) | paraphrases (4) | false (10) | injections (3) | forbidden fragment in top 3 (4) | fragments per question | questions with > 3 |
|---|---|---|---|---|---|---|---|
| main before revision 2 | 15 | 1 | 0 | 0 | **1** | 1.81 | 2 |
| + relative threshold 0.8 | 15 | 1 | 0 | 0 | 1 | 1.19 | 0 |
| + cat words in the stoplist | 15 | 1 | 0 | 0 | 0 | 1.69 | 2 |
| + both | 15 | 1 | 0 | 0 | 0 | 1.19 | 0 |
| **+ both + query expansion** | **16** | **4** | **0** | **0** | **0** | 1.38 | 0 |

1. **"Where is it"**: inventory had no place field. `nexus_lists.location` (nullable, reversible migration `f4a8c2d6e913`, field in the Mini App item
   sheet) is returned with inventory hits and is part of the search text. The bot shows "место: …" or "место не указано, спроси у Кай".
2. **More than 3 matches**: the response carries `more`, `offset` pages through the rest ("Показать ещё" in the bot).
3. **Relative threshold**: a fragment is shown only if its score is ≥ 0.8 × the best one (absolute thresholds 0.58 / 0.10 unchanged). Alone it
   does not fix "какой наполнитель у котов": the word "котов" matches "Корм для котов" lexically and the food fragment *is* the best. Cat words
   (кот, кошка and their forms) are therefore removed from the **sitter** FTS query only (everything in this corpus is about cats, so they
   discriminate nothing); the owner's memory search (`core.memory_search.fts_query`) is untouched, a test pins that.
4. **Paraphrases** ("чем наполнять лоток", "чем перевязать лапу", "чем посветить") share no word with the right item and score below the vector
   threshold. One Haiku call (role `parse`) turns the question into 3–6 keywords/synonyms **before** the search. Guards: the question is passed as
   data in tags and the prompt forbids following instructions in it; the reply is accepted only as a comma-separated list of 1–2-word letter tokens
   (≤ 8 words; any prose, refusal, digit, link or punctuation rejects the whole reply); 3 s timeout, any failure → plain search; cache by normalized
   question (6 h, failures 1 min); 500 calls/day cap per process; `SITTER_EXPAND=0` switches it off without a code change. The new words only add
   FTS terms: each is ranked on its own (`ts_rank` of an OR-query of N words is diluted below 0.10), visibility filters and thresholds are the
   same, and the vector leg still uses the original question. Variants that also embed the expanded text (or take max of both cosines) were
   rejected: they brought back false answers ("успокоительное рецептурное" → pills, an injection → "ветеринарная клиника").
   Cost: 293 input + 13 output tokens on average ≈ $0.00036 per new question (Haiku 4.5), 0.6 s on average, run in parallel with the query embedding.
   Observation that shaped the prompt: without the "reply `-` if nothing fits" rule Haiku answered off-topic questions with sentences
   ("Я не могу помочь…"); the structural check would have rejected them, but they are now `-`.

## Isolation

`tests/test_sitter_ask.py` builds a world with sentinels (a hidden prescription item, an unflagged purchase, a person's memory, a finance
memory, a "Me" memory, another owner's rows, a rejected cat, archived/old/pending facts) and asserts none of them is returned for any
question, including their own wording. A route-enumeration test pins the set of routes the read token opens:
`GET /api/sitter/lists`, `GET /api/care/schedule`, `POST /api/sitter/ask`.

## Known gaps

Rule: every known gap is written together with the condition under which we fix it; a gap without a condition is not a known gap, it is a bug.

| Gap | Fix when |
|---|---|
| Thresholds are calibrated on a 24-document fictional set; margin to the nearest unanswerable neighbour is ~0.01 | re-check when the owner's inventory grows by 50% or the embedding model changes |
| Expansion words can match an unrelated item on the owner's real data (not measurable on fictional data) | after deploy, run the real-data probe (ranks only); if any off-topic probe answers, set `SITTER_EXPAND=0` and report |
| Per-process caches and the 500/day cap reset on restart | if Haiku spend per day exceeds $0.5 in `llm_call_log` |
| `location` is empty for existing inventory items | the owner fills it from the Mini App; fix by script only on request |

## Not covered

The bot side (`/ask`, free text) is a separate step in the private repository.
