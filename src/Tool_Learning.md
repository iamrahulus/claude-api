# Tool-Use Learning Log — Exercises 1–6
> Anthropic API · claude-haiku-4-5 · Raw API, no framework
> Completed: September 2026

---

## What this is

Six hands-on exercises building a governed, production-shaped agentic orchestrator from scratch — no LangChain, no Agent SDK, raw Anthropic API. The goal was implementation fluency, not tutorial completion. Each exercise added a layer that the next one depended on.

---

## Exercise 1 — Single tool, full loop

**Goal:** define one tool, execute the full `tool_use → execute → tool_result → final answer` cycle manually.

**What was built:** `get_stock_price` tool, two-call loop, manual message assembly.

**Key mechanics locked in:**

- `stop_reason: tool_use` → execute tool, loop back. `stop_reason: end_turn` → exit.
- `tool_result` role is always `user` — tool results are environment feedback, not model output.
- Every `tool_use_id` must be paired with exactly one `tool_result` in the immediately following message. Miss it → 400.
- `input_schema` not `parameters` — Anthropic's key, not OpenAI's.
- 400 and 401 errors are not billed — they fail before the model generates anything.

**Bug caught:** printed message list vs sent message list silently diverged. Lesson: log the exact object immediately before `.create()`.

---

## Exercise 2 — Routing failure, fix via descriptions

**Goal:** two near-identical tools, watch misrouting, fix without adding infrastructure.

**What was built:** `get_customer` / `get_account` with minimal one-line descriptions, then expanded descriptions with input format, example queries, and when-to-use-this-not-that guidance.

**Key finding:** tool descriptions are the only routing signal the model uses. Schema pattern constraints (JSON Schema `enum`, `pattern`) produced no measurable routing improvement over prose-only descriptions.

**Architectural principle:** fix the root cause (ambiguous descriptions) not the symptom (add a router/classifier). A classifier on top of a description problem is adding a layer without fixing the cause.

**Hard gate still required:** application-level validation after tool call return, before execution — soft signals (descriptions, schema, prompts) cannot substitute for it.

---

## Exercise 3 — Multi-turn loop, policy engine, audit trail

**Goal:** tool A returns partial info, tool B needs A's output. Real `while stop_reason == "tool_use"` loop with policy enforcement.

**What was built:** prerequisite dependency graph in `policy.py`, SQLite-backed `events` table, `AuditLogger`, correlation ID as audit spine.

**Key mechanics:**

- Loop condition is `stop_reason`, not iteration count or text parsing — only the model knows when it has enough to answer.
- Message history is the full accumulated list on every API call — Claude has no memory between calls, the messages array *is* the state.
- `MAX_ITERATIONS` is a blast-radius guard, not the exit condition. Hitting it must emit a terminal audit event, not a silent break.
- Policy rejections fed back to the model as `tool_result` with `is_error: true` → 45% of trials self-corrected with zero hard failures.

**Audit design:** unified `events` table with `event_type` column. Single `WHERE correlation_id = ? ORDER BY created_at` reconstructs the full story. `event_id` UUID as PRIMARY KEY for idempotency.

---

## Exercise 4 — Structured errors, retry classification

**Goal:** make a tool fail realistically. Return bare string error → observe bad handling. Return structured error → compare.

**What was built:** `errorCategory`, `isRetryable`, `attemptedInput`, `message` on every error return. `RetryPolicy` from `retry_policy.json`. `ClassifierMapping` for HTTP → category translation. Backoff computation.

**Key mechanics:**

- `isRetryable` from the tool is advisory — the orchestrator's own classification governs actual retry decisions. Both must agree before retrying.
- Three error handling paths: `orchestrator_retry`, `return_to_model`, `escalate`.
- `return_to_model` feeds structured error as `tool_result` — model decides next action.
- Deterministic failures (city not found, bad input) must not retry — `isRetryable: false` short-circuits immediately.
- Audit failure is not a critical-path gate — synchronous write + filesystem fallback, never block the user because the audit subsystem failed.

**Separation:** `policy.py` owns structural governance (what tools, what order, what action type). `retry_policy.json` owns reliability governance (how many attempts, backoff, budget exhaustion action). They change at different rates for different reasons.

---

## Exercise 5 — Parallel tool calls, dependency graph

**Goal:** multiple independent tools in one question. Confirm parallel `tool_use` blocks return in a single turn, all results sent back together.

**What was built:** `get_coordinates_for_city` + `get_weather`. Parallel geocoding for two cities, then parallel weather calls. Policy prerequisite graph extended with output→input binding matching.

**Key mechanics:**

- Parallel calls are the model's decision — tool descriptions make independence clear, model batches accordingly.
- API enforces set equality: `tool_use_id`s in assistant turn must exactly match `tool_result`s in next user message. Fewer → 400. More → 400. Wrong IDs → 400. All before model sees anything.
- `disable_parallel_tool_use: true` is a sub-parameter of `tool_choice`, not a separate flag.
- Dependency graph encodes **outcome rules, not workflow sequences** — "coordinates must be trusted" not "geocoder must have run." `allow_user_supplied` path satisfies the same invariant via prompt-match when coordinates come from the user directly.
- `query_history` must use the same DB connection as `AuditLogger` — separate `sqlite3.connect()` on the same file causes false policy rejections when rows written by one connection are not visible to a new connection before commit.
- `policy_rejection` is a governance event, not a reliability failure — routes to `return_to_model`, never to retry/escalation.

**Verified:** direct lat/lon prompt (`-33.87, 151.21`) approved via prompt-match path. City-name prompt (`Tokyo, Sydney`) approved via geocoder history match. Both paths confirmed in audit trail.

---

## Exercise 6 — Forced tool execution, tool_choice governance

**Goal:** `tool_choice` modes — `auto`, `any`, named tool. When is forcing correct, not a hack?

**What was built:** `tool_choice` as policy-driven decision resolved from `entry_point` config in `policy.py`, not hardcoded in the loop. Initial forced geocoder call followed by `auto` for subsequent iterations.

**Key mechanics:**

- `auto` → model decides whether to call any tool. Default. Correct for general agentic loop.
- `any` → model must call *something*. On every iteration → loop never exits (`end_turn` never arrives → `max_iterations` hit). Only safe on first iteration.
- `tool: {name}` → model must call that specific tool. API-layer enforcement, not probabilistic.
- `tool_choice` is a tuple bug risk — trailing comma in Python creates `({"type": ...},)` not `{"type": ...}`. Silent wrong type.

**When forcing is correct:**
> Force a specific tool when the action that follows is irreversible and the tool is the verification step that must run before it. Forcing guarantees the gate cannot be bypassed — it's a hard control at the API layer, not a probabilistic prompt instruction.

**When it's a hack:** compensating for a poorly described tool that the model won't call naturally under `auto`. Fix the description, not the routing.

**Unified governance model derived from implementation:**
- `policy.py` owns: prerequisite graph, action type, `entry_point` (tool_choice per input type), retry profile reference.
- `retry_policy.json` owns: retry budgets, backoff curves, `budget_exhausted_action` per error category.
- Orchestrator reads both, resolves `tool_choice` dynamically, never hardcodes governance decisions.

---

## Bugs that matter — keep this list

| Bug | Symptom | Fix |
|---|---|---|
| `parameters` instead of `input_schema` | 400: Field required | Anthropic key is `input_schema` |
| `tool_result` in `assistant` role | 400 or silent corruption | `tool_result` is always `user` role |
| Printed vs sent message list diverge | Wrong list sent, confusing 400 | Log exact object before `.create()` |
| `or` instead of `and` in tool gate | Gate always True, every call rejected | `not in (...)` |
| `float(city)` on string input | ValueError at runtime | `str(city)` |
| `.status_code` on dict return | AttributeError | Both tools return dicts — no Response object |
| `timeout_s=0.001` default | Every call times out | `10.0` |
| `tool_choice` with trailing comma | Tuple not dict, API ignores or errors | Drop trailing comma |
| Separate `sqlite3.connect()` for query | Reads empty DB, false policy rejections | Pass `audit.conn` into `query_history` |
| Retry exhaustion always escalates | Successful retries treated as failures | `while/else` — `else` fires only on exhaustion |
| `policy_rejection` routed to retry | Spurious escalation | Guard at top of error handler, `continue` |
| `last_error_event_id` unassigned on clean exit | NameError at final_answer write | Initialise to `None` at request start |
| `RetryBudget` never incremented | Dead code — budget never depletes | `budget.increment()` before each retry attempt |
| `degrade` path not implemented | Budget exhaustion always escalates | `while/else` + `budget_exhausted_action` branch |

---

## Architectural principles — the ones that held across every exercise

**Hard gates over soft signals.** Descriptions, schema constraints, and prompts are probabilistic. Application-level validation before execution is deterministic. Never rely on the former where the latter is required.

**Policy rejections are governance events, not errors.** Feed them back to the model as `tool_result` content. The model self-corrects. Breaking the loop on a policy rejection is the wrong pattern.

**Dependency graphs encode outcome rules, not workflow sequences.** "Coordinates must be trusted" not "geocoder must have run." Two paths can satisfy the same invariant.

**Tool_choice is a governance decision.** It belongs in `policy.py`, resolved dynamically. Hardcoding it in the loop couples orchestration mechanics to business rules.

**Reads degrade, writes escalate.** Budget exhaustion on a read tool degrades the answer — model notes the gap and continues. Budget exhaustion on a write tool escalates — side effect status unknown, human judgment required.

**Audit is infrastructure, not a gate.** Synchronous write + filesystem fallback. Never block a user because the audit subsystem failed.

**Autonomy proportional to reversibility.** Read → autonomous. Write → idempotency key + careful retry. Irreversible → hard gate before first attempt.

**Two failure modes of parameter corruption.** Fabrication (model invents a conforming value) and truncation (model silently drops data to force fit). Both require hard-gate validation.

---

## What frameworks abstract vs what they don't

| Concern | LangGraph abstracts | Remains custom |
|---|---|---|
| The while loop | ✅ StateGraph | — |
| Message history management | ✅ State | — |
| Checkpointing / resumability | ✅ Built-in persistence | — |
| HITL pause/resume over HTTP | ✅ interrupt() primitive | — |
| Streaming | ✅ One flag | — |
| Policy engine | ❌ | policy.py |
| Audit trail | ❌ | AuditLogger + events table |
| Structured error classification | ❌ | retry_policy.json + ClassifierMapping |
| Tool implementations | ❌ | get_weather, get_coordinates_for_city |
| Retry / budget governance | ❌ | RetryPolicy, RetryBudget |

Frameworks abstract the mechanism. They don't abstract the judgment.

---

## Next actions

### Immediate — orchestrator hardening (pre-FastAPI)

- [ ] **Dynamic tool_choice from policy** — add `entry_point` block to each tool in `policy.py`; orchestrator reads and resolves before first API call; remove hardcoded `tool_choice` from notebook
- [ ] **Implement `degrade` path** — `while/else` on retry loop; `budget_exhausted_action` from classification dict drives escalate vs degrade; degrade appends `is_error: true` tool_result and continues loop
- [ ] **Wire RetryBudget correctly** — initialise per `correlation_id` (not per session); `budget.increment()` before each retry attempt; `budget.exhausted()` check before each attempt; `budget.remaining()` in audit events
- [ ] **Fix `policy_rejection` routing** — guard at top of error handler before retry logic; `continue` back to model, no escalation, no break
- [ ] **Fix retry exhaustion logic** — `while/else` pattern; successful retry does not set `escalated = True`
- [ ] **Initialise `last_error_event_id = None` and `audit_event = None`** at request start — prevents NameError on clean exit
- [ ] **Fix `tool_choice` trailing comma** — `tool_choice = {...}` not `tool_choice = ({...},)`

### Track 2 — FastAPI MVP

- [ ] **`AsyncAnthropic` client** — replace sync client; every `.create()` becomes `await .create()`; loop becomes `async def`
- [ ] **`POST /query` endpoint** — accepts user message, generates `correlation_id` server-side, returns `{correlation_id, answer}`
- [ ] **Extract modules** — `policy.py`, `store.py` (AuditLogger), `retry.py` (RetryPolicy, RetryBudget), `tools.py` (execute_tool, execute_tools) as proper importable files
- [ ] **Per-request state** — `budget`, `correlation_id`, `escalated`, `last_error_event_id` all inside request handler; nothing mutable at module level
- [ ] **Replace `print()` with `logging`** — structured log output, queryable
- [ ] **`GET /audit/{correlation_id}`** — single query endpoint returning timeline

### Track 3 — Mini Nexus (after FastAPI MVP stable)

- [ ] LangGraph orchestrator replacing the manual while loop
- [ ] Two subagents — RAG subagent + MCP-connected subagent
- [ ] MCP server exposing two tools
- [ ] LangGraph `interrupt()` for HITL gate on write tools
- [ ] Evaluation layer — LLM-as-judge on every agent output
- [ ] Containerised, published as case study on rahulrahul.com.au

---

## Fluency checkpoints

Before FastAPI work starts — 3 minutes no notes on each:

1. **Exercise 3:** explain the loop condition, why message history is the state, what `MAX_ITERATIONS` is guarding against, and why policy rejections go back to the model not to an error handler.

2. **Exercise 5:** explain what the API enforces on parallel tool_result batching, why the dependency graph encodes outcome rules not workflow sequences, and what the `allow_user_supplied` path is doing and why it exists.

These are the two most likely to expose a gap under Venkata-level probing.