# Case 0032 — Cats as a memory subject: cards, `cat_id`, and retrieval by cat

- **Status:** Closed (offline eval); live RAG pending (see "Not covered")
- **Date:** 2026-11-02
- **Relates to:** ADR-0027 (People domain), ADR-0028 (query understanding), ADR-0030 (evals), Case 0031; measured by
  [docs/EVALS.md](../EVALS.md)
- **Code conforms to:** `alembic/versions/*_cats_subject.py`, `core/cats.py`, `core/repos/cats_tables.py`,
  `core/repos/pg_cats_repo.py`, `core/memory.py` (`parse_and_store`), `core/query_parse.py`, `core/memory_search.py`,
  `miniapp/backend/routes/cats.py`, `evals/synthetic/memory_world.py`, `evals/memory_eval.py`

## Problem

The owner's cats lived in memory as a flat category ("🐾 Коты") with a free-text `related_to`. There was no card, no
photo, and no subject to ask about, so a question about one cat was answered like any similar-text search:

- "what does Пончик not eat" returned facts about **other cats** as well;
- two different subjects with one name (a cat and a person called Тучка) were silently resolved to the person;
- a cat whose card was removed still had its facts in the answers.

## Decision

Treat a cat the way ADR-0027 treats a person, with no new LLM calls.

- **Data.** Table `cats` (name, aliases, normalised forms, photo, status) and a nullable `memories.cat_id`
  (FK `ON DELETE SET NULL`, indexed). Additive migration, reversible; existing rows are untouched.
- **Write path.** The message parser already returns a category and a name. When the category is cats and **exactly one**
  card matches (same declension-aware normaliser as people, names and nicknames), the fact gets `cat_id`; zero or several
  matches save a plain fact (no guessing), and an unknown name only adds a hint to create a card. Names are injected into
  the parse prompt from the table, never hard-coded.
- **Read path.** `query_parse` resolves the cat by SQL, subject `cat`. A question with a fact type goes straight to
  `cat_id` + type (route `direct`); without a type, hybrid search runs **only inside that cat's facts** (route
  `hybrid_cat`). A name shared by a cat and a person (or two cats) is `ambiguous`: every candidate is shown as its own
  labelled block. A removed card (`status = rejected`) is not resolved and its facts are filtered out of search.
- **Cost.** Resolution is SQL plus a lexicon, so it adds **0 LLM calls**: direct routes need no model, hybrid keeps the one
  existing rerank call (asserted in `tests/test_cats_search.py`).

## What the eval checks

Ten synthetic questions about cats (fictional Тучка, Пончик, Рыжик) join the existing nine about people in
`evals/synthetic/memory_world.py`. The world deliberately contains traps: a person named like a cat, a removed card whose
fact must not surface, a cat fact with no card, and a nickname/declension for each lookup. New metric
**ambiguity handling** = among questions flagged ambiguous, the share answered with subject `ambiguous` and at least
that many blocks (everyone shown, nobody chosen). Results are also split by group so the new set cannot hide a
regression in the old one.

## Numbers

Same 19 questions and the same synthetic world, offline (vector off, so hybrid is understated). **Before** = the code at
the previous release (cards and facts existed, no search by cat); **after** = current.

| Metric | Before | After |
|---|---|---|
| recall@5, all 19 questions | 78.9% | **100%** |
| precision@5, all | 66.6% | 91.2% |
| route accuracy, all | 68.4% | **100%** |
| ambiguity handling (2 questions) | 0% | **100%** |
| cats group (10): recall / precision / route | 60.0% / 41.5% / 40.0% | **100% / 88.3% / 100%** |
| people group (9): recall / precision / route | 100% / 94.4% / 100% | 100% / 94.4% / 100% |
| questions with a wrong or missed answer | 8 | 0 |

The people group is unchanged. Its precision reads 94.4% in both columns because the new world has one more fact that
legitimately matches "как зовут кота" (an unbound cat fact); on the previous nine-question world it was 100%. Precision
of the cats group stays below 100% by design: an untyped question ("какие лекарства у Пончика") returns the cat's
neighbouring facts after the best match.

Reproduce: `python -m evals.memory_eval offline`; CI runs `python -m evals offline` (thresholds: recall 100%, ambiguity
100%).

## Examples (input → output)

1. **Direct, no neighbours.** «что не ест пончик»
   - before: `hybrid`, five facts: *Пончик не ест сыр*, *Пончик принимает лекарства вечером*, *Пончик любит коробки*,
     and the facts of two other cats;
   - after: `direct`, subject `cat`, one block "Пончик": *Пончик не ест сыр*.
2. **Cat and person with one name.** «что не ест тучка»
   - before: `direct`, subject `person`, one block: *не ест мясо*. The cat's fact was silently dropped;
   - after: `direct`, subject `ambiguous`, two labelled blocks: "Тучка Тестова": *не ест мясо*; "Тучка": *Тучка не ест
     курицу*.
3. **Removed card.** «что не ест рыжик»
   - before: `hybrid`, returns *Рыжик не ест рыбу* (the removed cat) plus other cats' facts;
   - after: `unknown_person` ("not found"), nothing from the removed card appears.

## Not covered

- **Live RAG.** The offline eval has the vector leg off and no reranker, so `hybrid_cat` is measured on FTS only and no
  generated answer is judged. Live questions about cats on real data wait for the model budget to be restored; the
  private-case mode (`evals.memory_eval run`) is the place to track them.
- **Real-data recall** is not measured; the synthetic world is small by design (see EVALS.md).
- **Lexicon gap.** "лекарства" is not a stem of the "important" type (same for people), so such questions go through the
  hybrid route rather than a direct lookup.
- The cat-sitting app (`heylark_cats`) reading cards from this API is a separate piece of work and is not evaluated here.

## Takeaway

The gain came from the same two cheap, deterministic things as in Case 0031: resolve the subject before searching, and
filter by it in SQL. Nothing about the embedding model or the LLM changed, and the cost of the new route is zero calls.
