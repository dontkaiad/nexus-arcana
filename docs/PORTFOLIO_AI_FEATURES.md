# PORTFOLIO_AI_FEATURES — Prompt Engineering & AI Architecture

Real examples of prompt engineering and AI-architecture decisions from the codebase
of a twin Telegram-bot system (☀️ Nexus — personal assistant, 🌒 Arcana — practice
CRM). Each feature: **where** (file:line), **what it does**, **why it's interesting**
from a prompt-engineering standpoint.

> All examples are production code. Personal data (the user profile, client names,
> amounts) is generalized/replaced with placeholders in this document; in the code it
> is pulled from PostgreSQL at runtime.

---

## TL;DR — what's inside

| Topic | Where to look | Gist |
|---|---|---|
| Cost-aware model routing | `core/config.py`, `core/claude_client.py`, `tests/test_models_audit.py` | Haiku by default + a static test guard against "leaking" onto Sonnet |
| Two-tier intent classification | `core/classifier.py`, `arcana/handlers/base.py` | Regex pre-filter → Haiku few-shot; verb tense = planned/done |
| Layered context injection (RAG) | `arcana/handlers/sessions.py:1170-1175` | One system-prompt assembled from 4 sources on the fly |
| Vector semantic recall | `core/rag.py` | Reading triplets embedded into pgvector (Voyage), similarity search |
| Keyword-RAG memory | `core/memory.py` | Fact search without embeddings: normalization + alias-resolution |
| Whitelist spell-correction guard | `core/preprocess.py` | Haiku typo-correction that does NOT touch the 78 Tarot cards + client names |
| Constraint-based generation | `miniapp/backend/routes/today.py` | ADHD tip: ≤15 words, validator + retry + fallback, temperature=0.4 |
| Vision parsers | `core/vision.py`, `arcana/handlers/sessions.py:287`, `arcana/handlers/clients.py` | Receipts, reading photos, profile screenshots → JSON |
| Chain-of-thought | `arcana/handlers/sessions.py:254`, `nexus/handlers/finance.py:2313` | Step-by-step Tarot interpretation and budget algorithm |
| Behavioral evals | `tests/` (~880 test functions, 81 files) | Mock-API contracts on intents, max_tokens, output quality |

**Model stack:** Claude Haiku `claude-haiku-4-5-20251001` (routine),
Claude Sonnet `claude-sonnet-4-6` / `claude-sonnet-4-20250514` (deep
interpretation/budget/vision), OpenAI Whisper `whisper-1` (voice).

---

## 1. Cost-Aware Model Routing — the central architectural decision

The user pays for every token out of pocket, so model choice is not a detail but an
architectural invariant, guarded by a test.

### 1.1 Default is Haiku, Sonnet only when explicit
- **`core/config.py:65-66`** — the single source of model ids:
  ```python
  MODEL_HAIKU  = "claude-haiku-4-5-20251001"
  MODEL_SONNET = "claude-sonnet-4-6"
  ```
- **`core/claude_client.py:32`** — `used_model = model or config.model_haiku`.
  Any `ask_claude()` without an explicit `model=` goes to Haiku.
- **Why it's interesting:** the cheap model is the *default behavior*, not something
  to remember. The expensive model requires a deliberate `model=...`. This is a "pit
  of success" at the API-wrapper level.

### 1.2 A test guard against price regression
- **`tests/test_models_audit.py`** — a purely static audit (greps the sources, no
  live calls):
  - `HAIKU_REQUIRED` (13 files: router, deleter, reply_update, finance, stats,
    works, clients, grimoire, rituals, notes, notes_smart_select, nexus_bot,
    miniapp/today) — each MUST contain `model="claude-haiku...`. Otherwise the test
    fails.
  - `SONNET_LEGIT` (4 files: `core/memory.py`, `core/vision.py`,
    `arcana/handlers/sessions.py`, `miniapp/.../arcana_sessions.py`) — a whitelist of
    places where Sonnet is justified. Sonnet anywhere else = a red test.
- **Why it's interesting:** this is an **eval of the price architecture**. Cost
  control usually lives in review and gets forgotten; here it's formalized as a
  regression test. A new contributor can't accidentally "drop" a routine parser onto
  Sonnet.

### 1.3 Where Sonnet is justified (and why exactly there)
| Place | File:line | Why not Haiku |
|---|---|---|
| Tarot interpretation | `arcana/handlers/sessions.py:254 (TAROT_SYSTEM)`, calls `:744, :974, :1412, :1635` | Narrative + empathy + tying into the client's history |
| Budget analytics | `nexus/handlers/finance.py:2313 (BUDGET_SONNET_SYSTEM)`, call `:3196` | Multi-step algorithm, debt re-planning, two output variants |
| Vision (receipts) | `core/vision.py:22-103` | Image understanding |
| Session summary (Mini App) | `miniapp/backend/routes/arcana_sessions.py:481` | Narrative synthesis over N triplets |
| Long-form ADHD advice | `core/memory.py:459-484` | Context-aware generation against the profile |
| Arcana's poetic tip | `miniapp/backend/routes/arcana_today.py:119` | Tone/style matter more than speed |

> A note for honesty: two Sonnet literals coexist in the code —
> `claude-sonnet-4-6` (config, `core/memory.py:484`, `nexus/handlers/memory.py:197`)
> and `claude-sonnet-4-20250514` (calls in `arcana/handlers/sessions.py`,
> `base.py:268`, `arcana_sessions.py:481`). A candidate for consolidation into a
> single constant.

---

## 2. Multi-Domain Intent Classification — two-tier

Two independent classifiers, one per bot, with a shared technique: **a cheap regex
layer catches the obvious before the LLM**, and the LLM only sees the ambiguous.

### 2.1 Nexus: the classifier mega-prompt
- **`core/classifier.py:88` — `build_system(tz_offset)`** builds a large dynamic
  system-prompt (the file is ~1169 lines): 13+ types (expense / income /
  task / note / memory_save / memory_search / stats / list_* / arcana_redirect…),
  dozens of few-shot examples, injection of the current date/time.
- **`core/classifier.py:741`** — the main call goes *without* `model=` → the default
  Haiku, `max_tokens=1024`. The system's most frequent call — and the cheapest.
- **Night-mode logic** right in the prompt: before 05:00 "tomorrow" = today — removes
  a class of errors for the night-owl user.
- **`arcana_redirect`** (`:166, :248-249, :298`): if the text has words from
  `ARCANA_KEYWORDS` but no explicit type — send it to the sibling bot. Cross-domain
  routing is baked into classification.
- **Pre-regex layer** (dozens of patterns) catches commands/deletions/timezone/lists
  before Claude — savings and determinism.
- **Why it's interesting:** one Haiku call decides "where does this go" across 13+
  directions while accounting for the time of day; the "hot path" (regex) is free.

### 2.2 Arcana: ROUTER + disambiguation by verb tense
- **`arcana/handlers/base.py:21` — `ROUTER_SYSTEM`** (Haiku, `max_tokens=10`,
  calls `:446, :460`): ~21 intents (`session_done/planned/search`,
  `ritual_done/planned/ambiguous`, `new_client`, `grimoire*`, `memory_*`,
  `verify`, `stats`, `nexus_redirect`…). The answer is **a single word**.
- **A grammatical disambiguator** (the core of the prompt):
  > "an infinitive verb (\"to do\", \"to perform\", \"to lay out\") = PLANNED
  > (*_planned). Past tense (\"did\", \"performed\") = DONE (*_done).
  > NEVER pick *_done for an infinitive."
- A backstop guard on the code side (`_PAST_TENSE_RE`): even if Haiku returned
  `ritual_done` for an infinitive — the system downgrades to the preview flow rather
  than writing the fact immediately (see `tests/test_intent_fallback.py:76-89`).
- **Why it's interesting:** Russian morphology (verb aspect/tense) is turned into a
  planning signal. `max_tokens=10` for single-word output — the minimal classification
  cost. Double protection: prompt instruction + code guard.

---

## 3. Few-Shot Prompting — everywhere there's parsing

Few-shot is the primary technique for structured parsers. Examples:

- **`arcana/handlers/works.py:15-41` — `PARSE_WORK_SYSTEM`**: input→output right in
  the prompt, including relative dates ("tomorrow" → `YYYY-MM-DDT18:00`, "on Friday"
  → the nearest Friday).
- **`arcana/handlers/sessions.py:117` — `PARSE_SESSION_SYSTEM`**: three input formats
  for readings (single triplet / numbered session / free form
  "question → cards"), with an example for each. `max_tokens=4000` (call `:649`).
- **`core/lists_parser.py` (`_PARSE_*`)**: categorization examples (energy drinks →
  🚬 Habits, pet food → 🐾 Cats, medication → 🏥 Health), `max_tokens=2000` for
  long lists.
- **`tests/test_lists_parser_tech_category.py`**: a few-shot fix for a hallucination —
  explicit examples "iPhone/laptop = 💻 Tech, don't confuse with 💻 Subscriptions" +
  a negative instruction "don't confuse a one-off purchase with a subscription".
- **`arcana/handlers/sessions.py:242 (SESSION_SEARCH_PARSE_SYSTEM)`**: examples of
  keyword extraction ("readings about work" → `["work"]`).
- **Why it's interesting:** few-shot is applied surgically — where the format is
  ambiguous — and backed by tests that pin down specific hallucination cases
  (v1.2.1 tech category).

---

## 4. Chain-of-Thought / structured reasoning

### 4.1 Tarot interpretation — numbered rules + a strict format
- **`arcana/handlers/sessions.py:254` — `TAROT_SYSTEM`** (Sonnet, `max_tokens=2000`):
  - 6 numbered rules: meanings STRICTLY from the reference, each card
    "Position → Name → meaning in this position", tie into past readings,
    a short conclusion of 2-3 sentences, "NO poetry, no filler".
  - An anti-pattern right in the prompt: "do NOT assign cards Past/Present/Future —
    a triplet is not a timeline but three angles on the essence".
  - **HTML-only output guard**: only `<h3>/<b>/<i>/<p>`, "no markdown
    (no **, ##, * — ever), no `<div>/<span>/inline styles`".
- **Why it's interesting:** CoT here is an explicit reasoning structure (card by card,
  then synthesis), plus negative constraints against the model's default habits
  (markdown, temporal positions, "filler").

### 4.2 Budget algorithm — multi-step stateful reasoning
- **`nexus/handlers/finance.py:2313` — `BUDGET_SONNET_SYSTEM`** (Sonnet,
  `max_tokens=4096`, call `:3196`):
  - Step by step: income − fixed = distributable; "one debt at a time";
    detection of a "hard month"; ADHD-oriented distribution constraints.
  - **Two-variant output**: in a hard month — variant A ("pay per plan")
    and variant B ("renegotiate the debt") with narrative.
  - A strict JSON schema with nested objects (income / fixed / debts / variants /
    limits / goals).
- **Why it's interesting:** one of the few *justified* Sonnet cases — the task
  requires state (carrying over the remainder), re-sorting debts, and generating an
  action plan, not field extraction.

---

## 5. Context Injection / RAG patterns

### 5.1 Layered system-prompt assembly (Tarot)
- **`arcana/handlers/sessions.py:1170-1175`** — one prompt assembled on the fly
  (the section markers are the verbatim Russian strings from the code):
  ```python
  system = TAROT_SYSTEM
  if cards_context:  system += "\n\n--- СПРАВОЧНИК КАРТ ---\n" + cards_context
  if memory_context: system += "\n\n--- ПАМЯТЬ ---\n" + memory_context
  if prev_context:   system += "\n\n--- ПРЕДЫДУЩИЕ РАСКЛАДЫ КЛИЕНТА ---\n" + prev_context
  ```
  4 sources: base rules + canonical deck card meanings + personal facts from memory +
  the client's recent readings.
- **Why it's interesting:** *deterministic* layered context assembly — no embeddings,
  no vector search: relevant sections (the specific deck's reference, this exact
  client's history) are injected by key/relation. The model "remembers" the client and
  doesn't invent card meanings. This is a separate mechanism from vector semantic
  recall (see §5.4) — there it's similarity by meaning, here it's exact assembly by
  identifier.

### 5.2 Keyword-RAG memory (no embeddings)
- **`core/memory.py`**: `save_memory` (`:489`), `search_memory` (`:610`),
  `get_memories_for_context` (`:991`), `auto_suggest_memory` (`:1044`).
  - The fact parser `_PARSE_SYSTEM` (`:42`, Haiku, `max_tokens=200`): text → JSON
    `{fact, category, relation, key}` with 17 categories (including 🦋 ADHD).
  - Search — token normalization (case/diacritics/cases) + lookup over
    Text/Key/Relation + **alias-resolution** (recognizes "also known as…",
    recursively, depth ≤3).
  - `get_memories_for_context` deduplicates by page_id and filters by bot label
    (Nexus vs Arcana) — a shared store, different context.
- **Why it's interesting:** pragmatic RAG for a small personal corpus —
  no embeddings/infrastructure, but with alias canonicalization and injection into
  downstream prompts.

### 5.3 Memory auto-suggest on repetition
- **`arcana/handlers/memory.py` / `core/memory.py`**: a counter on the pair
  (intent, topic); on the 3rd repetition of one fact → "🧠 Remember?". Fires
  only for `session_done / client_info / ritual_done`
  (`tests/test_arcana_memory.py:74-104`).
- **Why it's interesting:** proactive memory based on a behavioral signal
  (repetition = a pattern), without an explicit user command.

### 5.4 Vector semantic recall (reading triplets)
- **`core/rag.py`** — `index_triplet` / `index_triplets_batch` / `search_triplets` /
  `delete_triplet` / `ensure_collection`.
- Each reading → a triplet (cards · question · interpretation), embedded with
  **Voyage `voyage-4-lite` (dim 1024)** and stored in **pgvector**, in the same
  Postgres the bot already runs (table `arcana_triplets`, HNSW `vector_cosine_ops`).
  Cosine search: `1 - (embedding <=> query)`.
- **Rate-limit-aware for Voyage's free tier (3 RPM):** `index_triplets_batch` embeds
  N triplets in a single request (one reading = one call, not N); the client runs
  `max_retries=5`.
- **Graceful by contract:** no key / DB down / pgvector absent → warning + empty/no-op;
  the reading still works without RAG.
- **Why it's interesting:** real semantic recall *by meaning* (not by key, unlike
  §5.1) — and right-sized: zero extra services, no external Docker network, inside the
  Postgres that already exists. The decision and the rejected alternatives (Qdrant,
  keyword-RAG) are in ADR-0006.

---

## 6. Vision — three separate parsers for three tasks

| Task | File:line | Model | Prompt specifics |
|---|---|---|---|
| Receipts/bank transactions | `core/vision.py:22 (_RECEIPT_SYSTEM)`, parse `:71-103` | Sonnet, `max_tokens=2048` | Category whitelist; income vs expense by sign; `math.ceil(abs(amt))` — conservative round-up; unknown category → 💳 Other + `need_clarify` |
| Tarot reading photo | `arcana/handlers/sessions.py:287 (VISION_SYSTEM)`, call `:1483` | Vision | Card order "left-to-right, top-to-bottom"; heuristic "an extra 4th card in a triplet = bottom of the deck → into `bottom_card`" |
| Client TG-profile screenshot | `arcana/handlers/clients.py (VISION_CONTACT)` | Vision | Extract name/username/birthday/contacts → JSON, everything `or null` |
- **Why it's interesting:** not "one vision prompt for everything", but three
  specialized ones with built-in domain heuristics (rounding amounts up, layout
  geometry, null-safety against hallucinations).

---

## 7. Whisper — voice with graceful degradation

- **`core/voice.py:15` — `transcribe(file_bytes, ...) -> str | None`**:
  OpenAI `whisper-1`, `language="ru"` (`:28-29`). If there's no `OPENAI_API_KEY` —
  returns `None` (`:22`), without crashing. Called from `nexus/nexus_bot.py` and
  `arcana/bot.py`; with no key the bot replies "🎤 Voice is not configured".
- **Why it's interesting:** multi-provider (the only non-Anthropic path) with
  fail-soft — the feature can be turned off (no credits) without breaking the bot. The
  language is hardcoded to the domain → more accurate recognition.

---

## 8. ADHD adaptation and feminine grammatical gender — personalization as a system technique

Gender and neurodivergence are not cosmetics but prompt parameters and test contracts.
(The contents of the personal profile are deliberately not quoted here — privacy; below
is the *technique*, not the data.)

- **Feminine gender + name + direct "you"** in generative prompts:
  `core/memory.py:459` (`_ADHD_TIP_SYSTEM`), `nexus/handlers/tasks.py:1069`
  (`_NUDGE_SYSTEM`), `:2763, :2997`, `miniapp/backend/routes/today.py:177`
  (`_TIP_SYSTEM`).
- **Neuro-profile injection**: into `_ADHD_TIP_SYSTEM` / `_NUDGE_SYSTEM` a
  structured profile is fed (procrastination patterns, triggers) → advice
  *specific to the user*, not a generic "make a list".
- **ADHD-aware business rules in prompts**: the budget algorithm
  (`finance.py:2313`) bakes in anti-impulse distribution constraints;
  the nudge after creating a task (`tasks.py:1082`) gives ONE concrete step.
- **"Voice" protection in the whitelist**: spell-correction does not touch the terms
  on which the domain/personal language is built (`tests/test_preprocess.py:31-37`).
- **Why it's interesting:** the persona (gender, name, neurotype) runs through the
  whole stack — parsing, generation, and tests — as an explicit contract, not a
  one-off phrase in a single prompt.

---

## 9. Guardrails — where the system doesn't trust the model

The most "engineering" part: every LLM output is wrapped in protection.

### 9.1 Whitelist spell-correction
- **`core/preprocess.py`**: Haiku fixes typos, but a "NEVER correct" whitelist is
  injected into the prompt: 78 RU Tarot cards + ~30 esoteric terms + **client names
  from PostgreSQL**. A SQLite whitelist cache, `TTL=3600s` (`:37`); on client creation
  — `invalidate_whitelist` (`:126`), otherwise a fresh name would get "corrected".
- A real bug this fixes (`tests/test_preprocess.py:48-60`): Haiku would turn a client
  name into a common noun. The whitelist blocks this.
- **Why it's interesting:** a generic LLM feature (fix typos) is fenced by a domain
  dictionary that is **built from the user's live data** and cached with invalidation.

### 9.2 Output validators + retry + fallback (ADHD tip)
- **`miniapp/backend/routes/today.py`**: `_TIP_SYSTEM` (`:177`), `_validate_tip`
  (`:163`), Haiku call `max_tokens=80, temperature=0.4` (`:195-197`).
  - Contract: ≤15 words, 1 sentence, no emoji/markdown, no "thing/stuff/
    something" (anti-placeholders).
  - `tests/test_tip_validation.py`: a bad answer → **retry once** → if still
    bad → fallback string, and **the bad answer is not cached** (cache hygiene).
- **Why it's interesting:** a classic constraint-satisfaction loop around a
  nondeterministic model: validate → retry → fallback, plus `temperature=0.4` for
  stability of the short format.

### 9.3 Anti-conversational and anti-truncation guard
- **`core/preprocess.py`**: if Haiku returned "sorry, I can't…" or the text is
  too long/truncated — return the original (`tests/test_preprocess.py:95-110`).
- **`core/classifier.py:732-735`**: if the *input* starts with "I can't / as an AI /
  i cannot" — return `unknown` (protection against feeding a leaked LLM answer into
  the system).
- **Why it's interesting:** the system protects itself from its own models in both
  directions — from a "chatty" spell-checker, and from an LLM answer fed back in.

### 9.4 Adaptive max_tokens
- **Spell-correction**: `max_tokens = max(300, len(converted)//2 + 200)` — long
  sessions (5+ triplets) are no longer truncated (issue #82).
- **Lists**: `max_tokens=2000` for multi-item lists (issue #66,
  `tests/test_lists_v1_2.py:145-176` checks `>= 2000`).
- **Session parser**: `max_tokens=4000` (`sessions.py:649`).
- **Why it's interesting:** the token budget scales to input length — a balance of
  "don't overpay" vs "don't lose the tail", pinned by tests.

### 9.5 JSON-only + heuristic fallback parser
- All Haiku parsers: "Reply with ONLY valid JSON, no markdown", then a safe
  strip of ` ```json ` fences.
- On invalid JSON — a **heuristic parser** preserves the input:
  `arcana/handlers/grimoire.py` (`tests/test_grimoire_parse.py:35-73`),
  `arcana/handlers/ritual_writeoff.py`.
- **Why it's interesting:** structured output never loses the user's data — even when
  the model "broke" the JSON, the regex fallback extracts the title/body.

---

## 10. Evals — tests that check model behavior

~880 test functions across 81 files (per CLAUDE.md — 936 passing cases:
parametrize expands the count). The AI-specific ones:

| Test | What it checks | Why it's an eval |
|---|---|---|
| `tests/test_models_audit.py` | Haiku in 13 cost-critical files; Sonnet only in 4 allowed ones | A regression guard for the price architecture |
| `tests/test_router_intents_regression.py` | mock Haiku, contract `model == "claude-haiku-4-5-20251001"`, ≥8 few-shot, 10 intent cases, the "1 intent = 1 handler" dispatcher | Behavioral eval of the classifier + dispatcher contract |
| `tests/test_intent_arcana.py` | `ROUTER_SYSTEM` contains all intents + examples "did/performed/to plan" | Validation of the prompt's linguistic accuracy |
| `tests/test_intent_fallback.py` | `ritual_done` without past tense → downgraded to planned | A prompt→behavior guard on top of classification |
| `tests/test_preprocess.py` | whitelist (≥78 cards, esoteric terms, a client name), anti-conversational, cache hit/miss/invalidate | Spell-correction guard + cache strategy |
| `tests/test_tip_validation.py` | anti-placeholder, length bounds, retry→fallback, cache hygiene | Behavioral eval of generation quality |
| `tests/test_lists_v1_2.py` | `max_tokens >= 2000` for long lists | A regression on output truncation |
| `tests/test_lists_parser_tech_category.py` | few-shot fixes 💻 Tech vs 💻 Subscriptions | An eval against a specific hallucination |
| `tests/test_grimoire_parse.py` | invalid Sonnet JSON → heuristic fallback writes to PG | Graceful degradation |
| `tests/test_arcana_memory.py` | auto-suggest on the 3rd repetition; only the right intents | A behavioral memory trigger |

- **Why it's interesting:** models are tested as **contracts** — statically (which
  model where), against a mock API (which intent → which handler), and by output
  quality (validators). This is rare: most LLM projects have no regression tests on
  prompts.

---

## 11. Summary of engineering patterns

1. **Cheap-by-default** — Haiku as the wrapper default, Sonnet requires an explicit
   choice and is guarded by a whitelist test.
2. **Regex pre-filter → LLM** — the obvious is caught for free, the model only sees
   the ambiguous (classifier, list-classifier).
3. **Morphology as a signal** — Russian verb aspect/tense = planned/done.
4. **Layered prompt assembly** — the system-prompt is assembled from sources by
   key/relation (deterministic, vector-free — §5.1); semantic recall by meaning is a
   separate mechanism on pgvector + Voyage (§5.4, ADR-0006).
5. **Whitelist guards** — generic LLM features (spell, vision categories) are fenced
   by domain dictionaries from live data + a cache with invalidation.
6. **Validate → retry → fallback** — nondeterministic output is always wrapped in a
   contract and degrades softly (heuristic parser, fallback string).
7. **Adaptive token budgets** — `max_tokens` to input length, pinned by tests.
8. **Persona-as-contract** — gender/name/neurotype run through parsing, generation,
   and tests.
9. **Prompts as tested artifacts** — prompt contents and model routing are under
   regression tests.

---

*This document was produced by analyzing the codebase; the links are clickable (paths
relative to the repository root). No code was changed in preparing it.*
