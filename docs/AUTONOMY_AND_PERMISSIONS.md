# Autonomy and permissions

Two independent dimensions, both fixed per run by host configuration. The model cannot change either.

| | **Autonomy mode** | **Execution environment** |
|---|---|---|
| Question it answers | How far may an AI decision travel without a human? | Where does an execution land? |
| Values | `ADVISOR`, `APPROVAL_REQUIRED`, `AUTONOMOUS` | `BACKTEST`, `SHADOW`, `LIVE` |
| Implemented by | `policy/permissions.py::decide` | adapters wired by the composition root (`ports.py`) |

## 1. Autonomy modes

### ADVISOR
The agent may inspect permitted data, analyse, answer questions and **propose** trades (intent + risk verdict
preview). Nothing is executed, in any environment, not even protective exits or liquidation of pre-existing
holdings — the operator is in charge. Starting LIVE+ADVISOR with system-opened positions logs a warning.

### APPROVAL_REQUIRED
The agent analyses and constructs an intent; the risk engine evaluates it; an **approval ticket** is opened.
Execution requires `wallex-agent approve TICKET_ID`. Then the risk engine runs **again** on fresh data. Protective
exits (the stop/TP/time plan that was part of the approved entry) and end-of-challenge liquidation execute without
a second approval, because they only reduce risk and were declared before entry.

Ticket rules: bound to `intent_hash`, environment and `policy_version`; expires after `approval.ticket_ttl_s`;
single use (APPROVED → CONSUMED atomically); stored server-side; a changed price or quantity is a new intent and
needs a new ticket; approval never overrides a risk REJECT.

### AUTONOMOUS
Risk-approved intents execute automatically, inside deterministic limits. In LIVE this additionally requires
`live_autonomous_enabled = true` in config (startup refuses otherwise, and the policy returns BLOCKED for entries
as defence in depth) until U04 — Wallex's "explicit confirmation before state-changing actions" vs. the brief's
automatic execution — is answered by the organiser.

## 2. Policy matrix (as implemented; `wallex-agent policy-matrix`)

Outcome is the same in every environment except the LIVE+AUTONOMOUS gate.

| Mode \ action | AI_ENTRY | AI_EXIT | PROTECTIVE_EXIT | LIQUIDATION |
|---|---|---|---|---|
| ADVISOR | PROPOSE_ONLY | PROPOSE_ONLY | PROPOSE_ONLY | PROPOSE_ONLY |
| APPROVAL_REQUIRED | REQUIRE_APPROVAL | REQUIRE_APPROVAL | EXECUTE | EXECUTE |
| AUTONOMOUS | EXECUTE¹ | EXECUTE | EXECUTE | EXECUTE |
| any mode, kill latched | BLOCKED (ADVISOR: PROPOSE_ONLY) | unchanged | EXECUTE (ADVISOR: PROPOSE_ONLY) | EXECUTE (ADVISOR: PROPOSE_ONLY) |

¹ LIVE: BLOCKED unless `live_autonomous_enabled`. Every EXECUTE still passes the risk engine, which can REJECT/REDUCE.

## 3. Environment × autonomy (9 combinations)

| Env \ mode | ADVISOR | APPROVAL_REQUIRED | AUTONOMOUS |
|---|---|---|---|
| BACKTEST | proposals on historical snapshots; no sink | approvals answered by a scripted fixture policy (e.g. approve-all / reject-all) to measure the flow; simulated fills | simulated fills after risk gate |
| SHADOW | live reads, proposals | operator approves simulated intents; still zero real writes | simulated fills on live book |
| LIVE | live reads, proposals; write path closed | approved intent → fresh risk → real order | real orders inside limits; requires operator gate |

Guarantees across BACKTEST and SHADOW: zero real writes (live adapter not constructed; executor tool exposure
empty; intent environment must equal adapter environment). BACKTEST also gives the LLM no live Wallex tools.

## 4. Tool permissions by consumer

From `tools/catalog.py` (`wallex-agent tool-exposure`); full classification in `docs/WALLEX_TOOL_MAP.md`.

| Consumer | BACKTEST | SHADOW | LIVE |
|---|---|---|---|
| DECISION_AGENT (LLM) | none | `candle_history`, `market_depth`, `latest_trades` | same |
| ASSISTANT (LLM) | none | above + `basic_get_markets`, `spot_get_markets`, `prop_package_details` | same |
| MARKET_DATA_ADAPTER | 7 public reads (historical download / metadata) | 7 | 7 |
| ACCOUNT_ADAPTER | none | 12 private reads (fees, balances, orders, package) | 12 |
| EXECUTION_ENGINE | none | none | spot post / cancel ×2 (alternative to REST) |

Plus internal tools (no network) for both LLM roles: `get_candidates`, `get_features`, `get_position`,
`get_risk_status`, `get_recent_decisions`, `get_decision`, `get_trades`, `get_market_overview`, and for the
assistant only `simulate_cycle` (counterfactual, null sink). In BACKTEST, internal tools are served from the frozen
dataset at the virtual time.

Invariants (tested): LLM consumers only ever see `SAFE_READ` tools that need no key; `DANGEROUS`/`IRRELEVANT`
tools reach no consumer; write tools reach only the execution engine, only in LIVE; an unclassified tool is denied.

## 5. Who can change what

| Action | Who | How |
|---|---|---|
| Autonomy mode, environment | operator | run config at start; change = new run |
| Risk limits | owner | `config/limits.toml`, versioned; change = new run |
| Approve / reject a ticket | operator | CLI, journaled with identity |
| Latch kill switch | operator or automatic trigger | CLI / risk engine |
| Reset kill switch | operator only | CLI with reason; journaled |
| Anything above | **model** | **never** |
