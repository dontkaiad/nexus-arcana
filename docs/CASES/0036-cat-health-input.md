# Case 0036 — Cat health from chat ("<cat> weighs 4.2 kg", "<cat> vet 12 December", "<cat> drops from the 15th for 10 days")

- **Status:** Accepted (Nexus side, phase 1 of the cats app)
- **Date:** 2026-11-10
- **Relates to:** Case 0032 (cats as a subject), PR #313 (tables), #314 (token and routes), #315 (this input path)
- **Code:** `core/cat_health_chat.py`, hook in `core/memory.py` (`parse_and_store`), classifier rule in `core/classifier.py`,
  `evals/cat_health_eval.py`, `evals/synthetic/cat_health_cases.jsonl`, `tests/test_cat_health_chat.py`

## Problem

Weight, vet visits and medicine courses of the cats should land in structured tables (with a reminder task), not in free-text facts.
Doing it with an extra classifier call on every message would double the cost of the memory path; doing it by guessing would write
records that never happened (a wrong visit date is worse than a plain fact).

## Decision

No new LLM call. A free check (`wants_health`: a cat name **and** a health word in the text) decides whether a short block is added to the
**same** parse request; the model may then return an optional `"здоровье"` object. The code validates it strictly (type, ranges, dates, the number of
grams is checked against the number in the message) and resolves the cat to exactly one card of the owner. Any doubt means the message is saved as a
plain fact, as before. The classifier prompt got a rule so that these messages reach the memory path instead of becoming tasks.

## What is measured (`python -m evals.cat_health_eval offline|live`)

12 fictional messages: weight, vet visit with a date, vaccine with a next term, a medicine course with a start date, a course with doses per day,
"cat is afraid of the vacuum" (a fact, no health), an unknown cat, a cat sentence without any health word, a person ("Оля весит 60 кг"), a person
with a cat in the same sentence, a vague date ("в начале декабря"), a weekday ("в пятницу"). Metrics: outcome accuracy (weight | visit | med | fact, with
the grams / date / kind / days checked), `false_health` (a message that must stay a fact but became a record; the harmful error) and `missed`.
`offline` runs gate + validation + cat resolution on hand-written model answers (a code regression, no LLM, part of `python -m evals offline`);
`live` sends the same fictional text to the real Haiku with the real prompt (cents per run).

| Measurement | Before | After |
|---|---|---|
| Offline, 12 cases: outcome accuracy | 50.0% (everything is a fact; 6 health messages missed) | **100%**, false_health 0, missed 0 |
| Live Haiku, first block (no rule for vague dates): outcome accuracy | n/a | 91.7%: "в начале декабря" became a visit on 2026-12-01 (**false_health 1**) |
| Live Haiku, final block (weekday + "do not guess vague dates"), 2 runs | n/a | **100% / 100%**, false_health 0, missed 0 |
| Classifier live, 10 messages (6 cat health, 2 facts, 2 controls): routed as expected | 5 / 10 (cat health reached memory in 1 of 6) | **9 / 10** (5 of 6) |
| Existing offline evals (`python -m evals offline`): classifier, retrieval recall@5, ambiguity, grounding accept / reject | 100 / 100 / 100 / 100 / 100 | 100 / 100 / 100 / 100 / 100 (unchanged) |

The live run found a real defect that the offline set could not: with only "dates without a year are the nearest future one" the model turns a
vague phrase into an exact date. The block now says not to guess vague time, and has today's weekday for "в пятницу". A first draft of the set also had
"vet 03.04": the model read it as 3 April next year (day.month, the Russian convention) and wrote a visit; that is the intended reading and was kept out of the set.

## Examples (fictional)

| Input | Output |
|---|---|
| Тучка весит 4,2 кг | weight 4200 g for Тучка today, reply "⚖️ Тучка: 4,2 кг"; no memory fact |
| Кексу капли с 15-го на 10 дней | course "капли" 15.11 – 24.11, daily reminder task at 09:00, reply "💊 Кекс: капли с 15.11 на 10 дн., напоминаю каждый день в 09:00" |
| Оля весит 60 кг | no cat name: the old path, a plain fact about a person |
| Кекс ветеринар в начале декабря | health block was added, the model gives no date, the message is saved as a plain fact |

## Not covered

- Past tense "Тучке **сделали** прививку 5 ноября" is still routed by the classifier to `task_done` (the "past-tense verb" rule is stronger than the new
  one); the health path is never reached. 1 of the 10 classifier messages.
- The model, not the code, expands "с 15-го", "в пятницу", "через год"; the code only checks that the date is plausible. A wrong but plausible
  date is recorded (the reply shows it, an edit in the app fixes it). Measured on 12 messages, two runs, one model.
- Several cats in one sentence, a cat and a person with similar names, two cats with the same name (falls back to a fact, tested but not in the set), a
  correction of an earlier record by chat ("не 4,2, а 4,3": same-day weight is replaced, other records are not).
- Prices: no money is parsed or stored by design.
- Real-data check: none yet (needs a week of use; the cost per message is in `llm_call_log` and `scripts/llm_usage_stats.py`).
