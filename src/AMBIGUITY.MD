# Exercise 2 — Tool Routing Under Ambiguity

Part of CCA Foundations hands-on prep. Investigates how Claude (Haiku 4.5) resolves tool selection when user input conflicts with a tool's declared format constraints — and what actually stops a bad call from reaching a downstream system.

## Setup

Three tools registered: `get_stock_price`, `get_customer` (4-digit ID), `get_account` (8-digit ID). Both ID tools declare their digit-count constraint in the tool-level `description` and the parameter-level `description`; a later variant adds a JSON Schema `pattern` (`^[0-9]{4}$` / `^[0-9]{8}$`) to both.

Two conflicting prompts were tested — a keyword paired with a digit count that belongs to the *other* tool:

- `"Lookup account ID 1234"` — account keyword + 4-digit (customer-shaped) number
- `"Lookup customer ID 12345678"` — customer keyword + 8-digit (account-shaped) number

Each was run 20× per condition across `temperature ∈ {0.0, 0.5, 1.0}`, with and without the `pattern` constraint. All calls used fresh, independent message lists (no conversation carryover).

## Findings

### 1. Schema conflicts cause non-deterministic hedging (fixed early, but a real bug)
An early schema had a tool-level description contradicting its own parameter-level description ("4-digit" vs "8-digit" on the same tool). This alone produced inconsistent behavior — hedge, single call, or dual-call — on identical input. Fixing the internal contradiction was necessary before any of the results below are meaningful. Lesson: a schema must be checked for *internal* consistency, not just correctness of one field in isolation — this exact class of copy-paste error was caught and reintroduced by hand twice during this exercise.

### 2. Keyword vs. format conflicts resolve **asymmetrically**, not randomly
With a consistent, non-contradictory schema (prose-only, then again with `pattern` added), the two conflict directions produced near-opposite outcomes:

| Prompt | Temp 0.0 | Temp 0.5 | Temp 1.0 |
|---|---|---|---|
| "account ID 1234" (wrong direction obeyed) | 100% | 90% | 75% |
| "customer ID 12345678" (wrong direction obeyed) | 0% | 5% | 10% |

n=20 per cell, prose-only and pattern-added conditions both tested, results consistent across both. "Account" + wrong digit count is blindly obeyed almost every time; "customer" + wrong digit count is caught almost every time. Root cause (keyword salience vs. digit-count salience vs. something else in how the two tools are structured) is **not yet isolated** — flagged as an open question, not a resolved one. Practical implication: routing reliability validated for one tool pair does not generalize to a structurally similar pair; each conflict direction needs its own test.

### 3. Lower temperature is not "safer" — it's "more consistently whatever the dominant behavior is"
At temp=0, the "account" direction fails 100% of the time, every time, with near-identical wording. Determinism collapsed variance into the *wrong* outcome for that tool pair. This overturns an assumption worth stating explicitly: temperature reduces variance in the failure, not the failure itself. Whether that variance-reduction helps or hurts depends entirely on which behavior it's converging on.

### 4. Two distinct parameter-corruption failure modes observed
- **Fabrication**: model invents a value to force a fit (e.g. `get_account(id='1234')` → retried with `id='12340000'` or `id='00001234'`, values the user never provided).
- **Truncation**: model silently drops digits to force a fit (e.g. `get_customer(id='1234')` from a stated `12345678`).

Both are worse than a plain wrong-tool call: they don't just misroute, they alter the argument that reaches the downstream function, with no signal to the caller that this happened.

### 5. `input_schema.pattern` does not change model behavior — confirmed empirically
Adding `pattern: "^[0-9]{4}$"` / `"^[0-9]{8}$"` to both schemas produced **no measurable difference** from the prose-only baseline, at any tested temperature. At temp=0 the failure rate was identical (100%) with and without the pattern. This confirms architecturally what the API docs imply: `input_schema` constraints are generation-time guidance the model may weigh against other signals (like a user's keyword) — not a contract enforced before the tool-use block is returned to the caller.

## Conclusion

The only mechanism that reliably prevents an invalid or cross-tool-mismatched argument from reaching a downstream system is **application-level validation after the model returns the tool call and before the corresponding function executes.** Neither prose descriptions, JSON Schema `pattern`, nor low temperature are sufficient on their own.

```python
import re

SCHEMA_PATTERNS = {
    "get_customer": r"^\d{4}$",
    "get_account": r"^\d{8}$",
}

def validate_tool_input(tool_name: str, tool_input: dict) -> bool:
    pattern = SCHEMA_PATTERNS.get(tool_name)
    if pattern and not re.fullmatch(pattern, tool_input.get("id", "")):
        return False
    return True
```

This is a concrete, tested instance of the governed-tool-calling principle: declared constraints are advisory to the model; enforcement is the caller's responsibility, every time, regardless of how confidently the model presents its output.

## Open questions / next steps

- Isolate whether the asymmetry (finding #2) is driven by keyword salience or digit-count salience — requires a schema swap (customer↔4-digit becomes customer↔8-digit, account↔8-digit becomes account↔4-digit) rerun on the same prompts.
- Scale ambiguous-condition trials beyond n=20 to tighten confidence intervals on the mid-range rates (temp 0.5–1.0 cells).
- Wire `validate_tool_input` into the Exercise 1 agentic loop and confirm it catches all logged failure cases end-to-end.