# ADR-0011: Finance categories — single source of truth + domain boundary

## Status

Accepted

## Context

Three category constants existed in `core/config.py`:

- `FINANCE_CATEGORIES` (15 elements) — used as the LLM parser's known universe in `core/claude_client.py`
- `EXPENSE_CATEGORIES` (12 elements) — served by miniapp `GET /categories?type=expense`
- `INCOME_CATEGORIES` (6 elements) — served by miniapp `GET /categories?type=income`

Additionally, `nexus/handlers/finance.py` contained a verbatim copy `CATEGORIES` (15 elements) defined at module level, disconnected from `core/config.py`.

### Bug 1 — LLM parser blind to Nexus income categories

`FINANCE_CATEGORIES` contained only `"💰 Зарплата"` from income, omitting `"💼 Фриланс"`, `"🎁 Подарок"`, `"💵 Возврат/кэшбэк"`, `"💱 Продажа"`. These four categories were present in `INCOME_CATEGORIES` (served correctly by miniapp API) but absent from the LLM prompt in `claude_client.py:171`. A user typing a freelance income entry would get `category=null` from the parser.

### Bug 3 — Duplicate CATEGORIES in finance.py

`nexus/handlers/finance.py:729–734` redeclared the full list independently. Any addition to `core/config.py` required a manual parallel update to `finance.py`, with no automated consistency check.

### Non-bug — Расходники absent from EXPENSE_CATEGORIES

`"🕯️ Расходники"` (ritual supplies) intentionally does not appear in Nexus `EXPENSE_CATEGORIES`. It is an Arcana-domain expense not surfaced in the Nexus expense filter. This is a domain boundary, not an oversight.

## Decision

Introduce `ARCANA_CATEGORIES` as an explicit third domain list for categories that belong to the Arcana bot's domain (practice income, ritual supplies) but must be known to the shared LLM parser.

Derive `FINANCE_CATEGORIES` as a computed deduplication of all three lists:

```python
EXPENSE_CATEGORIES = [...]   # 12 Nexus expense categories
INCOME_CATEGORIES  = [...]   # 6 Nexus income categories
ARCANA_CATEGORIES  = ["🔮 Практика", "🕯️ Расходники"]

_seen, _all = set(), []
for _cat in EXPENSE_CATEGORIES + INCOME_CATEGORIES + ARCANA_CATEGORIES:
    if _cat not in _seen:
        _seen.add(_cat)
        _all.append(_cat)
FINANCE_CATEGORIES: list = _all   # 19 elements, no duplicates
del _seen, _all, _cat
```

Replace the local `CATEGORIES` copy in `nexus/handlers/finance.py` with:

```python
from core.config import FINANCE_CATEGORIES as CATEGORIES
```

The miniapp `/categories` route continues to import `EXPENSE_CATEGORIES` and `INCOME_CATEGORIES` directly — Arcana categories are intentionally excluded from the Nexus UI.

## Consequences

### Positive

- LLM parser now receives all 19 known categories, fixing the freelance/gift/return/sale income miss.
- Single source of truth: adding a category to any typed list automatically propagates to `FINANCE_CATEGORIES`.
- Domain boundary is explicit and documented (`ARCANA_CATEGORIES`), not implicit.
- `test_finance_categories.py` guards dedup invariant and domain boundary on every CI run.

### Negative / Trade-offs

- `FINANCE_CATEGORIES` is now a derived value, not a hand-edited list — contributors must edit `EXPENSE_CATEGORIES`, `INCOME_CATEGORIES`, or `ARCANA_CATEGORIES` rather than `FINANCE_CATEGORIES` directly. A comment in `config.py` explains this.
- The dedup preserves insertion order (EXPENSE → INCOME → ARCANA). If order in the LLM prompt ever matters, the source lists drive it.

## Alternatives Considered

**Keep FINANCE_CATEGORIES hand-edited, add missing income categories manually.**
Rejected: does not fix the synchronisation hazard. Next edit to any typed list would diverge again.

**Remove FINANCE_CATEGORIES, compute inline in claude_client.py.**
Rejected: `claude_client.py` should not know about domain lists. The config is the right place for this derivation.

**Merge Arcana categories into INCOME_CATEGORIES.**
Rejected: breaks the domain boundary — Nexus users would see "🔮 Практика" and "🕯️ Расходники" in income dropdowns. Arcana categories are surfaced only through the shared LLM layer, not the Nexus UI.
