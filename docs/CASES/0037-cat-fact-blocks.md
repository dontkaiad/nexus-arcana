# Case 0037 — Block of a cat fact (who may see it): profile | care | health | relations | owner | unsorted

- **Status:** Accepted (Nexus side, step 1 of 5; the sitter endpoint, the app tab, the guide transfer and the one-time sorting of existing facts are separate PRs)
- **Date:** 2026-11-11
- **Relates to:** Case 0032 (cats as a subject), Case 0034 (sitter search), Case 0036 (cat health input), `docs/specs/SITTER_LISTS.md`
- **Code:** `core/cat_blocks.py`, hook in `core/memory.py` (`parse_and_store`, `_parse_fact`), migration `d3f5a7c9e124`, `PATCH /api/cats/facts/{id}/block`,
  `evals/cat_block_eval.py`, `evals/synthetic/cat_block_cases.jsonl`, `tests/test_cat_blocks.py`, `tests/test_cat_block_eval.py`

## Problem

Facts about the cats are shown to a cat-sitter by their **type** (important, likes, …). The type says what the fact is, not who may read it: "the owner worries,
the vet cost 12 000" and "does not eat chicken" can both be `other`. One mistake leaks something private; a blunt rule hides what the sitter needs.

## Decision

Every cat-category fact (bound to a cat or "general") carries a **block** in a new nullable column `memories.cat_block` (CHECK: the six values).
The block decides the audience: the sitter sees `profile`, `care`, `relations`; from `health` only the existing type "critical" (`important`) and, from
`cat_medications`, current courses; `owner` and `unsorted` never. **NULL, an empty or an unknown value is closed.** The rule lives in `cat_blocks.audience`
(and, in the next PR, in the SQL of the sitter endpoint).

- No new LLM call. A free check (`wants_block`: a cat word or a cat name in the text) adds a short description of the blocks to the **same** parse request;
  the model returns one more key `"блок"`. Messages without cat words cost nothing extra.
- The code validates strictly (`normalize`): only an exact value counts; no answer, another word, a list, a number → `unsorted`. A fact that did not get the
  question (no cat word) is `unsorted`, not NULL, so that it appears in the owner's "unsorted" list.
- A repeat of the same fact never overrides a block that is already set (including the owner's own choice); it may only refine `unsorted`.
- The owner sets a block with one request (`PATCH /api/cats/facts/{id}/block`); an unknown value is 422 (never silently `unsorted`), a person-card fact is 404.
  A fact added by hand in the app is `unsorted` unless a valid block is given.
- The block is shown only to the owner's session. Reading by the Bearer token (the app) keeps the contract `cats_read_v1` unchanged.

## What is measured (`python -m evals.cat_block_eval offline|live`)

20 fictional messages (cats Тучка, Пончик, Кекс): 3 profile, 5 care, 3 health (current medicine, critical allergy, a symptom), 3 relations, 3 owner (money, plans,
feelings), 2 unsorted, 1 without a cat word. Metrics: `block_acc`, `audience_acc`, **`false_open`** (a fact that must stay closed became visible to the sitter;
must be 0), `missed_open` (needed by the sitter, stayed closed; tolerated, the owner sorts it by hand) and `unsorted_pct`. `offline` runs the gate, `normalize`
and `audience` on hand-written model answers (two of them deliberately bad); `live` sends the same fictional text to the real Haiku with the real prompt.

| Measurement | Before | After |
|---|---|---|
| Offline, 20 cases | no block at all: every fact is closed, block_acc 0%, false_open 0, missed_open 12 | block_acc 95%, audience_acc **100%**, false_open **0**, missed_open 0 (the one miss is "no answer" → unsorted, correct and closed) |
| Live Haiku, first prompt | n/a | block_acc 90%, audience_acc 95%, **false_open 1**: "Тучка такая смешная" (an impression) was called `profile` |
| Live Haiku, final prompt (profile = only how to recognise the cat; impressions are unsorted; plans are owner) | n/a | block_acc **100%**, audience_acc **100%**, false_open **0**, missed_open 0, unsorted 15% |
| Existing offline evals (`python -m evals offline`) | all 100% | all 100% (unchanged); the bundle now also fails on `false_open` |

Caveat: the final prompt was fixed after that one live failure, and the examples added to the prompt use other words than the set. The set is small (20),
so 100% is "no error found", not a guarantee; the one-time sorting of the real facts (step 5) is shown to the owner as a table before anything is written.

## Examples (fictional)

| Input | Block | Audience |
|---|---|---|
| Тучка рыжая, трёхлетняя, с белой грудкой | profile | sitter |
| Пончик не ест сухой корм, только влажный | care | sitter |
| Пончику нельзя курицу — аллергия | health (type important) | sitter |
| Кекс принимает таблетки от давления по утрам | health (type other) | owner (the sitter sees the course from `cat_medications`) |
| ветеринар для Кекса обошёлся в 12 тысяч | owner | owner |
| Тучка такая смешная | unsorted | owner |

## Not covered

- The sitter endpoint (`/api/sitter/cat-care`, `/api/sitter/ask`) does not read blocks yet (PR 2). Until then nothing changes for the sitter.
- The app reads the cards by the Bearer token with all facts (`/api/cats?with_facts=1`): the next PRs must give the sitter's passport the block-aware data.
- Existing facts have NULL (closed) until the one-time sorting (step 5).

## One-time sorting of existing facts (step 5, `scripts/sort_cat_blocks.py`)

Existing facts have no block (NULL = closed). The script builds a **plan** (dry-run, no writes): for each current cat-category fact without a block it decides
a block (a person-card fact is `owner` by rule; the owner's `--override ID=BLOCK` wins; guards turn money / plans / feelings into `owner`, passwords / codes /
links into `unsorted`, medicine words into `health`; otherwise one Haiku call for all facts, a sitter block is accepted only when the model says it is sure),
prints the table `id → block → audience` and saves the plan (mode 600). `--apply --plan FILE --rollback FILE` writes exactly that plan in one transaction
(each row must be unchanged since the dry-run and still without a block; the rollback file is written first); `--undo FILE` restores. The database dump before
`--apply` is made by a person. The "needs a drug" fact is reported separately: it is `health`, closed, and no course is created (no dose, no dates).
