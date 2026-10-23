# ADR-002: Agent safety boundary — the AI proposes, deterministic code disposes

**Status:** Accepted (Phase 02). Numeric thresholds deliberately left as required configuration.
**Date:** 2026-10-22
**Deciders:** Project owner (approves limits before LIVE), lead architect

## Context

The AI must have a real effect on trading decisions (brief: "AI had a real role, not just its name"), and the
system must never let a model control capital without limits (brief: order size cap, stop loss, no all-in entry).
Wallex's official MCP rules (`research/phase-02/evidence/wallex_llm_txt_2026-10-22.txt`) say: obtain explicit user
confirmation immediately before any state-changing action, never claim a tool call succeeded unless the result
says so, and never silently retry a state-changing call. All 52 Wallex tools lack safety annotations, and the order
tool's schema only requires `side/symbol/type`.

## Decision

### 1. The model never holds a write capability

No LLM-facing consumer (`DECISION_AGENT`, `ASSISTANT`) is ever given a `WRITE_LOW`, `TRADE_EXECUTION`,
`DANGEROUS` or `PRIVATE_READ` tool. Enforced in code (`tools/catalog.py::invariant_violations`, tested) and by
construction: the agent's toolsets are built only from `exposed_tools(consumer, environment)`.

### 2. One deterministic path from AI intent to an order

```
AgentDecision (model output, typed)
  → [1] schema validation          extra fields forbidden; action shape; bounded reason codes      → else WAIT
  → [2] reference validation       snapshot_id current; candidate_id/position_id were in the input;
                                   candidate unexpired; action enabled in config                    → else WAIT
  → [3] intent construction        deterministic: symbol/side/prices/exits come from the candidate,
                                   qty from the sizer; intent_hash computed
  → [4] permission policy          autonomy × action kind (policy/permissions.py)                   → PROPOSE_ONLY | REQUIRE_APPROVAL | BLOCKED | EXECUTE
  → [5] risk engine                checks + sizing + reservation → APPROVE | REDUCE | REJECT (verdict bound to intent_hash, short expiry)
  → [6] approval (if required)     single-use ticket for exactly this intent_hash, env, policy version, expiry
  → [7] risk engine again          fresh snapshot/balances after approval; human approval cannot override REJECT
  → [8] execution adapter          refuses unless verdict.intent_hash == intent.intent_hash, verdict unexpired,
                                   approved_qty ≥ intent.qty, intent.environment == adapter.environment
```

Every step writes to the Decision Journal, including the step where the cycle stopped.

### 3. What the AI may and may not influence

| AI may | AI may not |
|---|---|
| Select one provided candidate, or WAIT | Invent a symbol, price, quantity, stop, TP, time limit |
| Request early exit of an open position (risk-reducing; config-gated) | Widen a stop, extend holding time, add to a loser |
| Signal `risk_posture=CAUTIOUS` (risk engine may scale size **down**) | Scale size up, choose leverage, change limits |
| Read allowlisted public market data, snapshot features, portfolio/risk summaries | Read raw private account data, PII, or call any write |
| Explain (brief rationale, reason codes, evidence ids) | Change autonomy mode or environment (host-owned, fixed per run) |

### 4. Risk engine authority

Runs on every EXECUTE and after every approval. Can reject or reduce; nothing upstream overrides it (not the AI,
not a human approval, not a prompt). Its inputs are ledger state, fresh market data and versioned config; its
output is a `RiskVerdict` whose `checks` list names the config key of each threshold applied.

Check families (thresholds are config keys, see §7): symbol eligibility (fail closed on UNKNOWN), data freshness and
quality flags, kill latch, drawdown pause/emergency, session and total loss budget, loss-streak cooldown, per-trade
risk budget and stop-distance sizing, per-symbol and total exposure including pending reservations, max open
positions, min qty/notional and precision (round **down** only), spendable balance incl. fees, liquidity
(spread / depth for the size), own-cross and double-sell prevention, exit path exists (Market-disabled symbols are
excluded in v1), no-new-entry window before the official cutoff.

### 5. Kill switch and circuit breakers (concepts; implemented in Phase 03)

| Mechanism | Trigger (config) | Effect | Reset |
|---|---|---|---|
| **Kill switch (latch)** | manual `wallex-agent kill`; emergency drawdown; N consecutive execution errors; reconciliation mismatch beyond tolerance; clock/cutoff fault; catalog invariant violation at startup | no new entries; cancel open entry orders; exits and liquidation continue; persisted, survives restart | manual only, with a journaled reason |
| Drawdown pause | equity (mark-to-liquidation) below HWM by pause threshold | no new entries for cooldown | automatic after cooldown, if above emergency level |
| Loss-streak cooldown | K losing exits in window | no new entries for cooldown | automatic |
| Data breaker | stale feed / quality flags | no new entries on that symbol | automatic when fresh |
| AI breaker | M consecutive invalid/timeout decisions | entries WAIT; optional switch to baseline **only if configured and journaled** | automatic after probe succeeds |
| Rate-limit breaker | Wallex 429 / budget near limit (20 req/10 s documented for orders) | defer non-essential calls; exits keep priority | automatic |

Exit watcher, reconciler and cutoff liquidation run on their own schedules and never wait for the model or a human.

### 6. Write transport for LIVE

- **Default: Wallex REST** (`POST /v1/account/orders`, `GET/DELETE` by `client_id`) driven only by the Execution
  Engine. Reason: one write path with documented idempotency key, rate limits and order lookup; no model involved.
- **Alternative: Wallex MCP write tools** behind the same `ExecutionPort`, catalogued as `EXECUTION_ENGINE`-only.
  Use only if the organiser requires orders to go through MCP. Even then the model never calls them.
- Wallex's "explicit user confirmation" rule is honoured literally in `APPROVAL_REQUIRED`. Whether a deterministic
  executor running in `AUTONOMOUS` satisfies it for the challenge is **U04, unresolved**. Therefore `LIVE + AUTONOMOUS`
  refuses to start unless `live_autonomous_enabled=true` is set deliberately (`check_run_allowed`), and entries are
  `BLOCKED` by the policy without it.
- Writes are never retried blindly. A lost response is `UNKNOWN` → reconcile by `client_order_id` → only then decide.

### 7. Thresholds are configuration, not code

No numeric limit is chosen in Phase 02; the research pack explicitly says not to copy defaults from reference
repositories. Phase 03 adds `config/limits.toml` with these keys, each **required** (`null` refuses LIVE start):

`risk.per_trade_budget_fraction`, `risk.total_loss_budget_tmn`, `risk.session_loss_budget_tmn`,
`risk.max_position_notional_tmn`, `risk.max_total_exposure_tmn`, `risk.max_open_positions`,
`risk.drawdown_pause_fraction`, `risk.drawdown_emergency_fraction`, `risk.loss_streak_count`,
`risk.loss_streak_window_s`, `risk.cooldown_s`, `risk.max_spread_bps`, `risk.max_depth_fraction`,
`risk.stress_cost_bps`, `risk.verdict_ttl_s`, `data.max_bar_age_s`, `data.max_book_age_s`,
`agent.decision_deadline_s`, `agent.max_model_requests`, `agent.max_tool_calls`, `agent.invalid_streak_breaker`,
`approval.ticket_ttl_s`, `cutoff.no_new_entry_before_end_s`, `cutoff.liquidation_start_before_end_s`,
`kill.max_consecutive_execution_errors`, `kill.reconciliation_tolerance_tmn`.

Values are set by the owner from backtest/shadow evidence and loss appetite, recorded with a `limits_version`, and
hashed into every `RunManifest` and `RiskVerdict`.

### 8. AI failure behaviour

Model timeout, provider error, invalid schema, unknown reference, stale output, budget exceeded → `WAIT` for new
entries, journaled with `ValidationStatus`. Exits, reconciliation and liquidation are unaffected. The system never
silently replaces the AI with rules while claiming the AI decided; a baseline fallback, if enabled, is labelled
`DecisionSource.BASELINE` in the journal.

## Options considered

| Option | Verdict |
|---|---|
| A. AI selects among deterministic candidates; deterministic risk/execution (chosen) | Real causal AI effect, measurable by ablation, cannot widen risk |
| B. AI emits free BUY/SELL/size; risk engine clamps | Rejected: numeric invention, unstable replay, the AI "decides size" only to be overridden |
| C. AI holds MCP order tool with approval hook | Rejected: puts the write path inside model context; approval of tool calls ≠ risk validation |
| D. Rules only, AI explains afterwards | Rejected: fails "genuine AI use"; kept only as baseline B0 for ablation |

## Consequences

- The AI's role is narrow but causal and auditable: every entry exists because the model chose it; ablation against
  B0 shows what it changed.
- Risk thresholds must be set by the owner before LIVE; the system refuses to start otherwise.
- U04 must be answered before LIVE+AUTONOMOUS. APPROVAL_REQUIRED and SHADOW are fully usable meanwhile.
