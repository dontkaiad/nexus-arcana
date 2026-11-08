# Case 0035 — Whose fact is it? Attribution of "Me" facts by the quote, not by grammatical gender

- **Status:** Accepted, applied to production data on 2026-11-05 … 2026-11-08
- **Date:** 2026-11-08
- **Relates to:** Case 0032 (cats as a subject), ADR-0027 (People), `docs/EVALS.md`
- **Code:** `core/memory_subjects.py` (`attribution`, `keyword_category`), `scripts/assign_memory_subjects.py`, `core/self_gender.py`, `scripts/rewrite_self_gender.py`, `core/self_dedup.py`, `tests/test_assign_memory_subjects.py`, `tests/test_self_gender.py`

## Problem

Facts about the owner ("Me" card, about 1.6k rows) were extracted from chat history by an LLM. Some of them were not about the owner at
all: the chat partner talking about themselves, or the owner talking *to* the partner ("you like …"). The first attempt to find them used
the **text of the fact**: a masculine past-tense verb or "его/ему" meant "not about her, move it to the person of that chat". It moved
37 rows in the dry-run, and the owner reviewed them and found it unreliable.

Why it broke:

1. **The text of the fact is not the source.** The extraction model *retold* the owner's own words in the masculine ("купил машину"
   although the quote says "купила"), so the text carried no author signal. The reverse also happened: a partner's sentence was
   retold in neutral third person with no gender marks at all.
2. **Gender is not a safe marker anyway.** The owner writes about herself in both genders, and "его" is usually a direct object
   ("любит его руки"), not the subject.
3. After switching to the quote the number of reassigned facts first dropped to 0. It looked like a regression of the chat→person
   matching, but the matching was fine: almost all partner facts came from one chat that had **no person card at all**.

## Decision

Decide by the **quote** (`source_quote` + `quote_author`: `kai` / `partner` / `mixed`), never by the fact text and never by gender.

Person of the quote: first person = pronoun (я, мне, меня, мой …) **or** a first-person verb (present/future "-ю, -у, -жу …", past tense in any
gender when there is no other subject); second person = ты/тебя/тебе/твой or a "-ешь/-ишь" verb. The subject form wins over an oblique form.

| Quote author | Quote person | Verdict |
|---|---|---|
| owner | 1st only | stays in "Me" |
| owner | 2nd only, as a subject or feeler ("ты любишь", "тебя бесит") | about the interlocutor → moved to the person of that chat |
| owner | 2nd only, as an object ("твои руки …") | check |
| owner | both, but the subject is "я" (the "ты" is an addressee) | stays in "Me" |
| owner | both, "ты" is the subject | check |
| owner | none, no other person named (a fragment she wrote herself) | stays ("elided") |
| partner | 1st only | partner speaks about themselves → moved to the person of that chat |
| partner | 2nd only ("ты работаешь …") | about the owner, per the partner → stays |
| partner | both, or none | check |
| any | the word "Собеседник" in the fact text | rejected (the meaning is inverted) |
| mixed / unknown author | — | check |

"Check" is a category, `❓ Проверить автора`, the facts stay pending in "Me" so the owner can reject or hand them over in the Mini App. A chat
that cannot be matched to exactly one person (no facts/accounts of anybody else in it) also goes to "check". Chats are matched through other facts with the same `source_chat_id` (any review status), or explicitly by the owner (`--chat-map` records
the chat as the person's Telegram account in `person_accounts`). Rejected people and "Me" never match.

**Limit we accepted.** Role-play ("ролки") fragments, where an author writes as a character, cannot be told apart by rules. No heuristic was added for them: a wrong
guess is one tap in the Mini App, a smart rule is a new source of silent errors.

## Numbers (synthetic examples, real run)

| Step | Result |
|---|---|
| Rule v1, by the text of the fact | 37 moved, rejected by review (retold texts) |
| Rule by the quote, first dry-run | 0 moved: all partner facts sat in one chat without a card (card created, chat linked → 70 moved) |
| Rule by the quote, first version | "check" = 195 (first-person verbs were missing) |
| + verb forms, gender dropped | 116 (81 of them: one chat without a card) |
| + "both persons → check" | 92 |
| + "I … when you …" → stays | **50** |
| Final run, 1563 pending facts | 1414 stay (1290 first person, 42 addressee, 71 fragments, 11 "you" from the partner) · 70 moved to a person · 29 rejected · 50 check |
| Temporary states ("сейчас …", "до 5 января") | 12 rejected, 4 explicit exceptions kept |

Example (invented): quote "я люблю, когда ты меня обнимаешь" → stays; quote "ты любишь острое" by the owner → goes to the chat's person;
quote "я не ем мясо" by the partner → goes to the chat's person; quote "тебя бесит шум как и меня" → check.

## Categorization (keywords + Haiku)

1402 facts of "Me" got a category from a fixed list (preferences, health, relations, hobby, work, pattern, appearance, home, cats, other,
practice, ADHD).

- **Keywords first** (997 facts, no LLM), with context guards: a cat name counts only as a nickname at the start of the fact and not inside a
  title; "ритуал" needs an esoteric context; tactile facts about hair or skin are preferences, not appearance.
- **The rest to Haiku 5.5** (405 facts, batches of 50, JSON `{id: category}`), 12,702 input and 3,987 output tokens, **$0.0033**. The leftover
  pass (75 confirmed or old rows) cost $0.0003.
- Bug found on the first try: the model answered with category names **without the emoji**, so every row fell to "other". The parser now
  matches categories by letters only and the prompt says the emoji may be omitted.
- Changing a category never touches the review status and needs no re-embedding (the category is not in the vector).

## Feminine rewrite with a strict validator

Haiku had retold the owner's words in the masculine ("забыл поздравить"). One pass over **all** 1550 live texts (not only the ones a
regex flagged) asks the model to change *only* gender forms that refer to her. Model output is never trusted blindly; a change is accepted
only if all of this holds (`core.self_gender.validate`):

- the same number of words, the same digits, the text at least 80 % similar;
- no word is replaced by a different word (same first three letters when it is longer than three);
- the change is not only "ё ↔ е";
- no pronoun of **another person** is touched (он, его, ему, них …): "это его раздражает" is not rewritten automatically.

Result: 68 proposed → **53 accepted**, 14 held back for the owner's decision (pronoun changes), 5 rejected (tense change, a synonym instead of
an ending, a profane word replaced by another). After the owner's review 63 rows were rewritten (52 + 11 pronoun cases), the key
(`person_<id>_<type>_<hash(text)>`) was recomputed, embeddings were reset and rebuilt through Voyage (63 of 63, ≈ $0.00002), a backup
table (`memories_text_bak`) allows a one-statement rollback. Example (invented): "забыл поздравить кого-то" → "забыла поздравить кого-то";
"считает себя нейроотличным" → "считает себя нейроотличной".

## Duplicates

Near-duplicate facts are grouped without an LLM, on existing embeddings: complete-linkage clusters (cosine ≥ 0.90, same polarity,
"это разное" pairs excluded). Connected components chained hundreds of unrelated facts together at 0.82, so every member of a group must
be close to every other. The Mini App shows "Похожие" and merges reversibly (`merged_into`).

## Costs

All LLM work in this case ran through the separate tools key and Haiku 5.5 ($0.10 / $0.50 per 1M tokens): categorization $0.0036 in total,
feminine rewrite $0.0122, a one-call key check < $0.0001. About **$0.016** for the whole case; attribution, deduplication and the
Mini App tools cost nothing.

## Consequences

- Facts of a chat partner no longer sit in the owner's profile; the unclear ones are visible and cheap to fix by hand ("handing over" a
  fact to a person is one tap and reversible).
- Every automatic step has a dry-run with a local sample (texts stay outside git), an owner approval and a backup table.
- A class of errors is accepted knowingly: role-play fragments are not told apart. It is cheaper to review than to model.
