# Data contracts

Implemented as frozen Pydantic models in `src/wallex_agent/contracts/` (schema version `1.0.0`). This document
explains them; the code is authoritative.

## Rules for all contracts

- `extra="forbid"`, frozen. Unknown fields are errors — this is what stops a model from smuggling `qty` into a decision.
- Money and quantities: `Decimal` end to end. Wallex depth returns JSON numbers; parse with `parse_float=Decimal`.
- Timestamps: timezone-aware **UTC** only (`UtcDatetime`); Asia/Tehran only for display.
- Every persisted record carries `schema_version`. Minor bump = additive; major bump = replay of old journals needs a migrator.
- IDs are opaque strings; hashes are lowercase hex SHA-256 of canonical JSON (sorted keys, no whitespace, Decimals as strings).

## Flow

```
DataRef ─┐
BookTop ─┼─> MarketSnapshot ──> FeatureSet ──> SignalCandidate[] ──> AgentDecision (AI)
MarketMeta┘        │                                  │                    │
                   └──────────────┬───────────────────┴────────────────────┘
                                  ▼
                       ValidatedDecision ──> OrderIntent ──> RiskVerdict ──> [ApprovalTicket] ──> ExecutionEvent*
                                  │                                                                    │
                                  └──────────────────────────> DecisionRecord <─────────────────────────┘
                                                                     ▲
                                                               RunManifest (per run)
```

## Contracts

| Contract | File | Purpose | Key fields |
|---|---|---|---|
| `DataRef` | market.py | Exact bytes a decision used | `dataset_id`, `content_sha256`, `source`, `first_ts`/`last_ts`, `resolution_seconds` |
| `BookTop` | market.py | Best bid/ask snapshot | prices/qty, `received_at`, `exchange_ts` |
| `MarketMeta` | market.py | Order constraints | precisions, `min_qty`, `min_notional`, `market_order_enabled`, `metadata_version` |
| `MarketSnapshot` | market.py | Everything known at `as_of` | `snapshot_id` (content hash), `last_closed_bar_open`, `candles: DataRef`, `book`, `quality_flags`, `universe_version` |
| `FeatureSet` | market.py | Deterministic features | `feature_engine_version`, `features[feature_id, value, unit, window_bars]`, `deterministic_regime` |
| `SignalCandidate` | signals.py | One fully specified opportunity | `candidate_id`, `strategy_id/version`, `reference/stop/take_profit_price`, `max_holding_seconds`, `exit_policy_id`, `estimated_round_trip_cost_bps`, `expires_at` (validated geometry: stop < ref < TP) |
| `AgentDecision` | decision.py | **The model's only output** | `snapshot_id`, `action` (ENTER/WAIT/EXIT_POSITION), `selected_candidate_id`, `position_id`, `regime_assessment`, `confidence` (LOW/MED/HIGH, categorical), `risk_posture` (reduce-only), `reason_codes` (1–5, enum), `evidence_feature_ids`, `brief_rationale` (≤600 chars) |
| `DecisionProvenance` | decision.py | How the decision was produced | `source` (LLM/REPLAY/BASELINE), `model_id`, `model_settings_sha256`, `prompt_version`, `prompt_sha256`, `tool_calls[]`, tokens, latency, `raw_output_ref` |
| `ToolCallRecord` | decision.py | One audited tool call | `seq`, `tool_name`, `args_sha256`, `result_sha256`, `result_ref`, `is_error`, `latency_ms` |
| `ValidatedDecision` | decision.py | Validator output | `status` (VALID / INVALID_SCHEMA / UNKNOWN_REFERENCE / STALE / TIMEOUT / BUDGET_EXCEEDED / PROVIDER_ERROR / ACTION_DISABLED), `effective_action` (WAIT on any failure) |
| `OrderIntent` | execution.py | Deterministic order request | `intent_hash`, `client_order_id` (persisted before send), `side`, `order_type`, `qty`, `limit_price`, `purpose`, `environment`, `autonomy` |
| `RiskVerdict` | risk.py | Risk engine output | `outcome` (APPROVE/REDUCE/REJECT), `approved_qty`, `checks[name, passed, detail, limit_key]`, `limits_version`, `expires_at`, `reservation_id` |
| `ApprovalTicket` | execution.py | Single-use human approval | `intent_hash`, `environment`, `policy_version`, `expires_at`, `status`, `decided_by/at` |
| `ExecutionEvent` | execution.py | Order lifecycle event | internal `state`, raw `exchange_status_raw`, `cum_filled_qty`, `avg_fill_price`, `fee`, `fee_asset`, `source` (BACKTEST_SIM/SHADOW_SIM/EXCHANGE) |
| `DecisionRecord` | journal.py | **One per cycle**, the audit unit | environment, autonomy, data refs, candidates, provenance, validated decision, policy outcome, risk verdict, approval, intent, events, `status`, `config_sha256`, `code` (commit + dirty) |
| `RunManifest` | journal.py | One per run | code, config/limits/prompt/model/lockfile hashes, universe version, dataset refs, clock, seed, strategy versions |

## Mapping to the requested audit fields

| Requested | Field |
|---|---|
| timestamp | `DecisionRecord.recorded_at`, `MarketSnapshot.as_of` |
| market | `DecisionRecord.symbol` |
| data snapshot reference | `snapshot_id`, `data_refs[].content_sha256` |
| features | `features_ref` → `FeatureSet` |
| market regime | `deterministic_regime` + `AgentDecision.regime_assessment` |
| candidate strategies/signals | `candidates[]` (id, strategy, version) |
| AI decision, confidence | `validated.decision.action`, `.confidence` |
| rationale / evidence | `brief_rationale`, `reason_codes`, `evidence_feature_ids`, `provenance.tool_calls` |
| requested action | `validated.decision` |
| risk evaluation | `risk_verdict.checks` |
| approved / rejected action | `effective_action`, `policy_outcome`, `risk_verdict.outcome`, `approval.status` |
| autonomy mode, environment | `autonomy`, `environment` |
| execution result | `execution_events[]`, `status` |
| strategy version | `candidates[].strategy_version`, `RunManifest.strategy_versions` |
| prompt version | `provenance.prompt_version`, `prompt_sha256` |
| model / provider | `provenance.model_id`, `model_settings_sha256` |
| code version / commit | `code.git_commit`, `code.dirty` |

**No chain-of-thought is stored or requested.** `brief_rationale` is a concise, presentable justification grounded
in the evidence ids; provider "thinking" content, if any, is not persisted.

## Storage (Phase 03)

SQLite in `var/` (git-ignored): `decisions`, `intents`, `events`, `tickets`, `ledger`, `payloads` (recorded model
outputs and tool results, keyed by sha256). JSONL export per run into `runs/published/<run_id>/` for delivery after
a secret scan. Payload records never contain `X-API-Key` or provider keys.
