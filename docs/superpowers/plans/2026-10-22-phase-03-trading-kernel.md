# Phase 03 — Trading Kernel, Bounded Agent and Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable system that executes the full decision cycle in BACKTEST and SHADOW (all three autonomy modes),
with a bounded LLM agent, deterministic risk/execution, journal and replay — and a LIVE adapter that stays gated.

**Architecture:** Hexagonal core on the Phase 02 contracts (`src/wallex_agent/contracts`), permission policy and tool
catalog; environment differences only behind `ports.py`. PydanticAI runs the bounded agent; our orchestrator owns
workflow; SQLite journal/ledger.

**Tech Stack:** Python 3.12, uv, Pydantic 2.14, pydantic-ai-slim[mcp]==2.59.0, SQLite (stdlib `sqlite3`), pytest, Pyright strict.

**Spec:** `docs/ARCHITECTURE.md`, `docs/AGENT_WORKFLOW.md`, `docs/AUTONOMY_AND_PERMISSIONS.md`,
`docs/DATA_CONTRACTS.md`, `docs/WALLEX_TOOL_MAP.md`, `docs/adr/ADR-001-agent-framework.md`,
`docs/adr/ADR-002-agent-safety-boundary.md`, `AGENTS.md`.

## Amendment A — owner constraints accepted 2026-10-22 (binding; supersedes conflicting lines below)

**Time:** ~24 h remain. Priority: (1) trading/risk math correctness, (2) BACKTEST end-to-end, (3) SHADOW end-to-end,
(4) real AI through NineRouter, (5) journaling/replay/reporting, (6) LIVE readiness + preflight, (7) refinements.
No UI work; no new frameworks; smallest correct implementation.

**NineRouter is the only real-model path.** Testing layers: L1 deterministic (no network, scripted models, every
commit); L2 recorded/replay (no network); L3 NineRouter real-model eval (`pytest -m ninerouter`, opt-in, never in
plain `pytest`). Credentials from `NINEROUTER_API_KEY` or `NINEROUTER_API_KEY_FILE` (path **outside** the repo; a
path inside the repo is refused). Base URL, model id, timeout, settings are config. Verified 2026-10-22 on the
owner's machine: `http://localhost:20128/v1` is OpenAI-compatible (`/v1/models` lists 531 models); `av/*` models
return an empty `finish_reason="in_progress"` body for **non-streaming** chat completions but work when streamed, so
the agent runs PydanticAI with an `event_stream_handler` (streaming requests). "Combo" ids may be served by another
model (`claude-sonnet-5` → `gemini-3.8-flash-n`), so the **responding** model name is journaled.

**Autonomy semantics:** ADVISOR = propose only. APPROVAL_REQUIRED = the algorithm builds the complete trade;
operator may only approve/reject (no editing of symbol/side/qty/entry/stop/TP). AUTONOMOUS = final competition
mode; fully automatic after validation → permission → risk → execution; LIVE activation stays fail-closed behind the
U04 gate and the preflight.

**Trading windows:** versioned `[trading_window]` config (IANA timezone, one or more weekly windows, no-new-entry
buffer, flatten buffer). Distinguish competition start/end, recurring windows, no-entry period, forced-flatten
period. Outside a window: no entries, no AI entry decision is made executable, reason + next allowed time journaled.
LIVE refuses to start without it. BACKTEST/SHADOW simulate it when configured. Hours are **not guessed**.

**Capital and preflight:** expected starting capital 5,000,000 TMN (config `run.starting_capital_tmn`). LIVE
preflight (fakes only in Phase 03) verifies spendable TMN, fee rate, eligibility, precision, no existing
orders/positions/unexpected assets/reservations; any material mismatch fails closed.

**Reporting:** performance metrics (start capital, equity, realized/unrealized P&L, fees, net, return %, peak,
max drawdown, trades, wins/losses, win rate, avg trade, open positions, exposure, rejected, WAIT, risk-blocked,
AI latency/errors, execution errors). Export `SUMMARY.json`, `TRADES.jsonl`, `DECISIONS.jsonl`, `RISK_EVENTS.jsonl`,
`PERFORMANCE_FA.md`. Runtime status: event-level Persian-friendly lines + periodic summary + `status` command.

**Implementation clarifications decided while executing:**
- B0 is the deterministic *selector* over the same C1 candidates (Phase 01 ablation definition), implemented as
  `agent/baseline.py::BaselineSelector`; there is no separate B0 candidate generator.
- Ports are **synchronous** (bar cadence; simpler and fully testable). The LLM provider wraps its async run
  internally. Recorded in DECISION_LOG.
- Wallex `/v1/udf/history` caps the range per request (a 3-day request returned 1 bar); history is fetched in
  1-day chunks (1440 × 1m bars verified). History reaches ≥180 days back.
- `RiskEngine` is the only producer of executable `OrderIntent`s: it takes an `IntentDraft`, sizes it and returns
  `(RiskVerdict, OrderIntent | None)`; after approval `recheck(intent)` must approve the same qty or reject.
- Contract schema 1.0.0 is finalized in Phase 03 (nothing was persisted before); additive fields were added
  (`RunManifest` model provider/endpoint/trading-window/capital, `DecisionRecord.block_reason`).

**Added scope:** Task 1 also builds `runtime/calendar.py` (trading windows + competition clock). Task 6 adds the
five export artefacts. Task 7 adds runtime status lines and `status`. Task 8 uses `NineRouterSettings` and adds
`tests/ninerouter/` (marker `ninerouter`) + `reports/phase-03/NINEROUTER_EVAL_FA.md`. Task 9 adds `preflight`.
Task 10 adds SHADOW failure injection (stale data, NineRouter timeout, invalid AI output, simulator timeout,
duplicate submit, double spend, window closure, kill switch, cutoff liquidation). Task 11 is limited to the LIVE
REST adapter + preflight on fakes — **no private Wallex call in Phase 03**.

## Global Constraints

- Python `>=3.12`; `uv run pytest` and `uv run pyright` (strict) green at every commit.
- Money/qty `Decimal`; timestamps UTC-aware (`UtcDatetime`); quantities rounded **down** to `amount_precision`.
- `pydantic-ai-slim[mcp]==2.59.0` exact; any upgrade is a DECISION_LOG entry.
- LLM consumers only through `tools.exposed_tools(consumer, environment)`; `invariant_violations() == []`.
- Order path exactly: validate → `policy.decide` → risk → [approval → risk] → `ExecutionPort.submit`.
- No live write, no private Wallex call, no API key in tests. Network tests marked `@pytest.mark.network`, public only, skipped by default.
- Thresholds only from `config/limits.toml`; all keys listed in ADR-002 §7; `null` refuses LIVE start.
- MCP/REST payload status checked (`s == "ok"` for candles) regardless of `isError`.
- Every cycle that reaches a decision writes one `DecisionRecord`.

## Review Focus

1. Lost submit response (timeout after Wallex accepted) → state UNKNOWN, reconcile by `client_order_id`, **zero** second submit — test in Task 5.
2. Two candidates approved in the same cycle/tick must not both reserve the same TMN — test in Task 4 (`test_reservations_prevent_double_spend_same_tick`).
3. Forming (unclosed) last bar or a resolution mismatch must never feed features — test in Task 2 (`test_forming_bar_dropped`, `test_resolution_mismatch_flagged`).
4. Model returns a candidate id it was not shown, or answers after the deadline → WAIT with `UNKNOWN_REFERENCE`/`STALE` — test in Task 8.
5. Approval used twice, after expiry, or for a changed quantity → refused; approved intent re-risked on fresh data — test in Task 9.

---

### Task 1: Canonical hashing and run configuration

**Files:** Create `src/wallex_agent/contracts/hashing.py`, `src/wallex_agent/runtime/config.py`, `config/limits.example.toml`, `config/backtest.toml`, `config/shadow.toml`; Test `tests/test_config.py`, `tests/test_hashing.py`.

**Interfaces:**
- Produces: `canonical_json(obj: BaseModel | Mapping[str, object]) -> bytes`; `sha256_hex(data: bytes) -> str`;
  `RunConfig` (frozen Pydantic: `environment`, `autonomy`, `live_autonomous_enabled: bool`, `symbols: tuple[str, ...]`,
  `bar_interval_seconds`, `limits: Limits`, `agent: AgentSettings`, `cutoff_end_utc: UtcDatetime | None`);
  `AgentSettings` (`model_id: str | None`, `decision_deadline_s: int`, `max_model_requests: int`, `max_tool_calls: int`,
  `output_retries: int`, `allow_exit_position: bool = False`, `invalid_streak_breaker: int`);
  `load_run_config(path: Path) -> RunConfig`; `Limits` with every ADR-002 §7 key as `Decimal | int | None`;
  `RunConfig.config_sha256 -> str`.

- [ ] **Step 1: Failing tests** — `test_canonical_json_sorts_keys_and_stringifies_decimal` (`canonical_json({"b":Decimal("1.10"),"a":1}) == b'{"a":1,"b":"1.10"}'`); `test_live_requires_all_limits` (LIVE config with any `None` limit → `RunConfigurationError`); `test_live_requires_cutoff`; `test_live_autonomous_needs_gate` (delegates to `policy.check_run_allowed`); `test_config_hash_stable_across_key_order`.
- [ ] **Step 2:** `uv run pytest tests/test_config.py tests/test_hashing.py -v` → FAIL (import errors).
- [ ] **Step 3:** Implement; TOML via stdlib `tomllib`.
- [ ] **Step 4:** Tests PASS; `uv run pyright` clean.
- [ ] **Step 5:** Commit `feat(config): canonical hashing and validated run config`.

### Task 2: Market data adapter, QC and snapshot builder

**Files:** Create `src/wallex_agent/market/wallex_public.py` (REST client: candles `/v1/udf/history`, depth `/v1/depth`, markets `/v1/markets` + `/hector/web/v1/markets`, package `/v1/prop/packages/183`), `market/quality.py`, `market/snapshot.py`, `market/universe.py`, `market/dataset.py` (recorded dataset reader for BACKTEST); Test `tests/market/test_quality.py`, `test_snapshot.py`, `test_universe.py`; fixtures from `research/phase-01/evidence/_v1_*.json` and `research/phase-02/evidence/mcp_public_call_candles_r1.json`.

**Interfaces:**
- Consumes: `MarketSnapshot`, `DataRef`, `BookTop`, `MarketMeta`, `QualityFlag`, `canonical_json`.
- Produces: `parse_candles(payload: bytes, requested_resolution_s: int, now: datetime) -> tuple[list[Bar], frozenset[QualityFlag]]`
  (`Bar` = frozen dataclass ts_open/o/h/l/c/v Decimal; payload `s != "ok"` raises `WallexPayloadError(errmsg)`); `aggregate(bars: Sequence[Bar], interval_s: int) -> list[Bar]` (only complete buckets);
  `eligible_symbols(spot: set[str], meta: set[str], unavailable: set[str], extra: set[str]) -> frozenset[str]`;
  `build_snapshot(symbol, as_of, bars, book, meta, universe_version, data_ref) -> MarketSnapshot` (snapshot_id = sha256 of canonical content);
  classes `WallexPublicMarketData` and `RecordedMarketData` implementing `MarketDataPort`.

- [ ] **Step 1: Failing tests** — `test_forming_bar_dropped` (last `t` within interval of `now` excluded); `test_resolution_mismatch_flagged` (Phase 01 `resolution=15` fixture → `RESOLUTION_MISMATCH`); `test_payload_error_is_error` (`{"s":"error","errmsg":"Invalid resolution!"}` raises `WallexPayloadError`); `test_depth_parsed_as_decimal` (no float in BookTop); `test_crossed_book_flagged`; `test_aggregate_only_complete_buckets`; `test_eligible_formula_matches_phase01_snapshot` (=120 symbols from `eligible_symbols.json` inputs); `test_snapshot_id_stable`.
- [ ] **Step 2–4:** fail → implement (`httpx` not required: use `urllib` or add `httpx2` already locked) → pass.
- [ ] **Step 5:** Commit `feat(market): wallex public adapter, QC, snapshot, universe`.

### Task 3: Features, regime label, B0 and C1 candidate generators

**Files:** Create `features/engine.py`, `strategies/base.py`, `strategies/b0_baseline.py`, `strategies/c1_breakout.py`; Test `tests/strategies/test_no_lookahead.py`, `test_c1.py`.

**Interfaces:**
- Produces: `compute_features(snapshot: MarketSnapshot, bars: Sequence[Bar], book: BookTop | None) -> FeatureSet`;
  `class Strategy(Protocol): strategy_id: str; version: str; def candidates(self, snapshot: MarketSnapshot, features: FeatureSet, bars: Sequence[Bar], now: datetime) -> list[SignalCandidate]`;
  `C1Breakout(params: C1Params)`; `C1Params` fields `lookback_bars`, `buffer_atr`, `trend_bars`, `stop_atr`, `tp_atr | None`, `max_holding_s`, `candidate_ttl_s`, `min_edge_over_cost_bps` (values from config, not code).
- [ ] **Step 1: Failing tests** — `test_features_ignore_future_bars` (appending future bars does not change features at `as_of`); `test_c1_uses_prior_window_high` (breakout measured vs. highs of bars *before* the signal bar); `test_c1_skips_when_edge_below_cost`; `test_candidate_exits_fixed` (stop/TP/time set; geometry validated by contract); `test_b0_and_c1_share_candidates_schema`.
- [ ] **Step 2–4:** fail → implement → pass. Feature ids follow `"{symbol}.{name}.{window}"`.
- [ ] **Step 5:** Commit `feat(strategies): features, B0 and C1 candidates`.

### Task 4: Ledger, sizing and Risk Engine

**Files:** Create `portfolio/ledger.py`, `risk/sizing.py`, `risk/engine.py`, `risk/protections.py`, `risk/kill_switch.py`; Test `tests/risk/test_sizing.py`, `test_engine.py`, `test_protections.py`, `tests/portfolio/test_ledger.py`.

**Interfaces:**
- Consumes: `OrderIntent`, `RiskVerdict`, `RiskCheck`, `Limits`, `MarketMeta`, `BookTop`.
- Produces: `Ledger` (`apply(event: ExecutionEvent)`, `reserve(intent_hash, tmn) -> str`, `release(reservation_id)`, `equity_tmn(marks: Mapping[str, Decimal]) -> Decimal`, `high_water_mark`, `positions()`, `view(marks) -> LedgerView`;
  `LedgerView` frozen: `spendable_tmn`, `reserved_tmn`, `positions`, `equity_tmn`, `high_water_mark_tmn`, `realized_pnl_tmn`, `recent_exit_pnls`);
  `size_entry(equity, entry, stop, fee_rate, stress_bps, caps, spendable, liquidity_qty, meta, posture) -> Decimal | None` (formula: `research/phase-01/STRATEGY_AND_RISK_RESEARCH_FA.md` sizing section; `CAUTIOUS` halves; round down; below min → None);
  `RiskEngine.evaluate(intent: OrderIntent, ctx: RiskContext) -> RiskVerdict`, where `RiskContext` (frozen) =
  `snapshot: MarketSnapshot`, `candidate: SignalCandidate | None`, `ledger_view: LedgerView`, `limits: Limits`,
  `eligible: frozenset[str]`, `kill_latched: bool`, `posture: RiskPosture`, `now: datetime`, `cutoff_end_utc: datetime | None`; `KillSwitch.latch(reason)`, `.is_latched`, `.reset(reason, operator)` persisted in SQLite.
- [ ] **Step 1: Failing tests** — `test_size_rounds_down_and_respects_min_notional`; `test_size_never_exceeds_caps`; `test_cautious_never_increases_size`; `test_reject_unknown_symbol`; `test_reject_when_kill_latched`; `test_reject_stale_book`; `test_reservations_prevent_double_spend_same_tick`; `test_drawdown_uses_open_position_marks` (unrealized loss triggers pause); `test_loss_streak_cooldown_blocks_entries_not_exits`; `test_verdict_lists_limit_keys`; `test_fee_in_coin_reduces_sellable_qty`.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5:** Commit `feat(risk): ledger, sizing, risk engine, protections, kill latch`. Run `safety-boundary-reviewer`.

### Task 5: Execution engine, simulators, exit coordinator, reconciliation

**Files:** Create `execution/state_machine.py`, `execution/engine.py`, `execution/sim_backtest.py`, `execution/sim_shadow.py`, `execution/exits.py`, `execution/reconcile.py`, `tests/fakes/fake_exchange.py`; Test `tests/execution/test_state_machine.py`, `test_engine.py`, `test_sim_backtest.py`, `test_exits.py`, `test_reconcile.py`.

**Interfaces:**
- Consumes: `ExecutionPort` Protocol, `OrderIntent`, `RiskVerdict`, `ExecutionEvent`, `OrderState`, `Ledger`.
- Produces: `transition(state: OrderState, event: ExchangeObservation) -> OrderState` (illegal → `IllegalTransition`), where
  `ExchangeObservation` (frozen) = `kind: Literal["SUBMIT_SENT","ACK","TIMEOUT","STATUS","FILL","CANCEL_ACK","REJECT","NOT_FOUND"]`,
  `exchange_status_raw: str | None`, `cum_filled_qty: Decimal`, `at: datetime`;
  `ExecutionEngine.submit(intent, verdict) -> ExecutionEvent` (persists INTENT first; checks hash/expiry/qty/environment);
  `BacktestFillSimulator(fee_rate, slippage_bps, latency_bars)`: fills at **next** bar open ± slippage; stop/TP same bar → worse outcome;
  `ShadowFillSimulator`: consumes visible depth, partial fills, latency;
  `ExitCoordinator.on_market(symbol, book, now) -> list[OrderIntent]` (one owner per position; partial fill protected immediately);
  `Reconciler.resolve_unknown(client_order_id) -> ExecutionEvent`.
- [ ] **Step 1: Failing tests** — `test_lost_response_goes_unknown_then_reconciles_without_resubmit` (fake exchange accepts, drops response; exactly one order on fake); `test_submit_refuses_mismatched_hash`, `..._expired_verdict`, `..._wrong_environment`; `test_backtest_fill_next_bar_not_signal_close`; `test_same_bar_stop_and_tp_takes_stop`; `test_partial_fill_protected_immediately`; `test_fill_after_cancel_is_booked`; `test_no_double_sell_between_stop_and_tp`; `test_restart_rearms_exits_from_ledger`.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5:** Commit `feat(execution): state machine, simulators, exits, reconciliation`. Run `safety-boundary-reviewer`.

### Task 6: Journal, manifest and replay store

**Files:** Create `journal/store.py` (SQLite), `journal/manifest.py`, `journal/export.py` (JSONL to `runs/published/<run_id>/` with secret scan); Test `tests/journal/test_store.py`, `test_export.py`.

**Interfaces:**
- Produces: `SqliteJournal(path)` implementing `Journal`; `put_payload(data: bytes) -> str` (sha256 key) / `get_payload(ref) -> bytes`;
  `build_manifest(config: RunConfig, code: CodeVersion, ...) -> RunManifest`; `current_code_version(repo: Path) -> CodeVersion`;
  `export_run(run_id, out_dir) -> Path` (fails if a secret pattern is found).
- [ ] **Step 1: Failing tests** — `test_records_are_append_only`; `test_payload_roundtrip_by_hash`; `test_live_manifest_refuses_dirty_tree`; `test_export_blocks_secret_patterns` (`X-API-Key`, `sk-…`, `gho_…`); `test_every_cycle_status_roundtrips`.
- [ ] **Step 2–5:** fail → implement → pass → commit `feat(journal): sqlite journal, manifest, export`.

### Task 7: Orchestrator, composition root, BACKTEST end to end with B0

**Files:** Create `runtime/cycle.py`, `runtime/compose.py`, `runtime/clock.py`, `runtime/loops.py` (exit watcher, reconciler, cutoff), `agent/baseline.py` (`BaselineSelector` implementing `DecisionProvider`); Test `tests/runtime/test_cycle.py`, `test_compose.py`, `test_cutoff.py`.

**Interfaces:**
- Consumes: all previous tasks; `policy.decide`, `ActionKind`.
- Produces: `run_cycle(deps: CycleDeps, symbol: str, now: datetime) -> DecisionRecord`, where `CycleDeps` (frozen dataclass) =
  `config: RunConfig`, `market: MarketDataPort`, `strategies: Sequence[Strategy]`, `decider: DecisionProvider`, `risk: RiskEngine`,
  `ledger: Ledger`, `approvals: ApprovalStore`, `execution: ExecutionPort`, `journal: Journal`, `kill: KillSwitch`, `clock: Clock`; `compose(config: RunConfig) -> Runtime` (constructs only the adapters for `config.environment`); `VirtualClock`, `WallClock`; `CutoffScheduler`.
- [ ] **Step 1: Failing tests** — `test_no_candidates_means_no_decision_provider_call`; `test_advisor_proposes_never_submits` (spy ExecutionPort has zero calls in all 3 envs); `test_autonomous_backtest_fills_and_arms_exits`; `test_compose_never_builds_live_adapter_outside_live`; `test_cutoff_blocks_entries_then_liquidates_to_tmn`; `test_every_cycle_journaled`.
- [ ] **Step 2–4:** fail → implement → pass; then `uv run wallex-agent run --env BACKTEST --autonomy AUTONOMOUS --config config/backtest.toml` on the Phase 01 BTCTMN day fixture produces a journal and a summary.
- [ ] **Step 5:** Commit `feat(runtime): cycle, composition root, backtest e2e with baseline`.

### Task 8: Bounded decision agent, validator, Tool Gateway recording, replay provider

**Files:** Create `agent/decision_agent.py`, `agent/validator.py`, `agent/prompts/decision_v1.md`, `agent/replay.py`, `tools/gateway.py`, `agent/internal_tools.py`; Test `tests/agent/test_validator.py`, `test_decision_agent.py` (FunctionModel + fake tools), `test_replay.py`, `tests/tools/test_gateway.py`.

**Interfaces:**
- Consumes: `AgentDecision`, `ValidatedDecision`, `DecisionProvenance`, `ToolCallRecord`, `exposed_tools`, `Journal.put_payload`.
- Produces: `LlmDecisionProvider(model: str | Model, settings: AgentSettings, gateway: ToolGateway)` implementing `DecisionProvider`
  (PydanticAI `Agent(output_type=AgentDecision, retries=settings.output_retries)`, `UsageLimits(request_limit=..., tool_calls_limit=...)`, `asyncio.timeout(settings.decision_deadline_s)`);
  `validate(decision: AgentDecision | None, *, snapshot_id, candidate_ids, position_ids, now, deadline, exit_enabled) -> ValidatedDecision`;
  `ToolGateway.toolset(consumer, environment)` → filtered `MCPToolset` + internal tools, every call recorded as `ToolCallRecord` with stored payload;
  `ReplayDecisionProvider(journal, run_id)` serving recorded outputs/tool results with no network.
- [ ] **Step 1: Failing tests** — `test_unknown_candidate_id_waits`; `test_late_answer_is_stale_wait`; `test_provider_error_waits`; `test_exit_position_disabled_by_default`; `test_agent_cannot_see_write_tools` (toolset names ⊆ exposed set); `test_tool_calls_recorded_with_hashes`; `test_usage_limits_stop_runaway_tool_loop`; `test_replay_reproduces_decision_without_network` (socket blocked).
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Model benchmark (not a unit test):** 30 fixed snapshots × chosen model(s) from the run host → schema-valid %, latency p50/p95, cost; record in `reports/phase-03/MODEL_BENCHMARK.md`; freeze `prompt_version`.
- [ ] **Step 6:** Commit `feat(agent): bounded decision agent, validator, gateway, replay`. Run `safety-boundary-reviewer`.

### Task 9: Approval service, CLI and assistant

**Files:** Create `approval/store.py`, `interface/cli.py` (extend), `agent/assistant.py`; Test `tests/approval/test_store.py`, `tests/interface/test_cli.py`, `tests/agent/test_assistant.py`.

**Interfaces:**
- Produces: `SqliteApprovalStore` implementing `ApprovalStore`; CLI `run`, `approve`, `reject`, `status`, `kill`, `reset-kill`, `replay`, `ask`;
  `Assistant.ask(question: str) -> AssistantAnswer` (`answer_fa: str`, `cited_decision_ids: tuple[str, ...]`, `tools_used: tuple[str, ...]`); `simulate_cycle(autonomy)` counterfactual with null sink.
- [ ] **Step 1: Failing tests** — `test_ticket_single_use`; `test_expired_ticket_refused`; `test_ticket_hash_mismatch_refused`; `test_approval_reruns_risk_and_can_reject`; `test_assistant_has_no_write_or_approve_tool`; `test_why_not_trading_cites_latest_wait_records`; `test_simulate_cycle_never_calls_execution_port`.
- [ ] **Step 2–5:** fail → implement → pass → commit `feat(approval,cli,assistant)`.

### Task 10: Evidence runs — backtest study and shadow

**Files:** Create `scripts/fetch_history.py` (public, chunked, coverage report), `scripts/backtest_study.py`, `reports/phase-03/BACKTEST_REPORT.md`, `reports/phase-03/SHADOW_REPORT.md`.

- [ ] **Step 1:** Fetch BTCTMN/ETHTMN 1m history in chunks; report coverage/gaps; freeze dataset hash.
- [ ] **Step 2:** Pre-register trial budget (≤ 6 settings per family) and time split 60/20/20; run B0 vs A1 (LLM) vs A1-replay on validation; fee/slippage/latency stress ×2.
- [ ] **Step 3:** Read the locked test **once**; report net realized TMN, max drawdown, trades, AI disagreement vs. B0, inference cost.
- [ ] **Step 4:** SHADOW ≥ the time available, all three autonomy modes; failure fixtures (lost response, partial fill, fill after cancel, LLM timeout, stale book, restart, cutoff). Gate: zero real writes, zero duplicate/overspend, all UNKNOWN resolved.
- [ ] **Step 5:** Commit reports.

### Task 11: LIVE adapter and gate (only on explicit owner instruction)

**Files:** Create `execution/wallex_live.py` (REST orders with `X-API-Key` header from env), `portfolio/wallex_account.py` (private reads), `docs/LIVE_RUNBOOK.md`.

- [ ] **Step 1:** Fake-server tests for request shapes (`client_id`, `stop_Price` casing, quantity/price strings), 429 handling, timeout → UNKNOWN.
- [ ] **Step 2:** Owner runs the private **read-only** spike on the run host (key scope, balances, fees, open orders); fixtures stored without identifiers.
- [ ] **Step 3:** Live gate checklist in `docs/LIVE_RUNBOOK.md`: limits approved, cutoff set, U04 answered (for AUTONOMOUS), clean tag, shadow gate passed, kill switch drill done.
- [ ] **Step 4:** Commit; tag only when the owner says go.
