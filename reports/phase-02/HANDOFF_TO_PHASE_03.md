# Handoff to Phase 03

Date: 2026-10-22 · Phase 02 status: **PASS for architecture and foundation**; LIVE readiness is explicitly not claimed.

## 1. Selected architecture

Deterministic trading kernel + bounded AI agent, hexagonal (ports and adapters). The AI chooses among deterministic
candidates or WAITs (optionally requests a risk-reducing early exit); a single deterministic path
`AgentDecision → validator → permission policy → risk engine → [approval → risk] → execution adapter` is the only
route to an order. Same core in BACKTEST/SHADOW/LIVE; only adapters differ. Details: `docs/ARCHITECTURE.md`,
`docs/adr/ADR-002-agent-safety-boundary.md`.

## 2. Repository state

- GitHub: `setoodehsamin9-hash/wallex-agentic-trader`, **private**. `main` = research import; Phase 02 work on
  branch `phase-02/architecture-foundation` (merge by PR). Branch protection unavailable on this plan → convention.
- Implemented and tested: `contracts/` (all data contracts), `policy/permissions.py`, `tools/catalog.py` (52 tools
  classified, default deny), `ports.py` (Protocols), CLI `policy-matrix` / `tool-exposure`,
  `scripts/inspect_wallex_mcp.py`. **49 tests pass; Pyright strict: 0 errors.**
- Empty packages with stated responsibilities: `market features strategies agent risk approval execution portfolio journal runtime`.
- Evidence added: `research/phase-02/evidence/` (public MCP tools/list, initialize, llm.txt, 4 public tool calls).
- No product trading logic, no private calls, no orders, no keys.

## 3. Framework choice

PydanticAI `pydantic-ai-slim[mcp]==2.59.0` for the two bounded LLM roles (decision agent, assistant); our own
orchestrator for workflow; SQLite for journal/ledger/tickets. Rejected: LangGraph, LangChain agent, OpenAI Agents
SDK (fallback), custom MCP loop, multi-agent frameworks. Spike results and reasons: `docs/adr/ADR-001-agent-framework.md`.

## 4. Package / module boundaries

| Package | Owns | Must not |
|---|---|---|
| `contracts` | data shapes, validation | do I/O |
| `market` | Wallex reads, QC, snapshot, universe | decide trades |
| `features`, `strategies` | pure features, candidates with fixed exits | read the clock directly or the network |
| `agent` | LLM decision + assistant, validator, replay/baseline providers | hold write tools, size orders |
| `tools` | catalog, exposure, audited gateway | expose non-SAFE_READ to LLMs |
| `policy` | autonomy × action outcome | look at model text |
| `risk` | verdicts, sizing, protections, kill latch | be overridden |
| `approval` | single-use tickets | execute |
| `execution` | order state machine, simulators, live adapter, exits, reconcile | accept an intent without a matching verdict |
| `portfolio` | ledger, equity, reconciliation | trust framework memory |
| `journal` | records, manifest, payloads, export | be edited by hand |
| `runtime` | cycle, loops, composition root | contain environment branches in core logic |
| `interface` | CLI | contain business logic |

## 5. Data contracts

`docs/DATA_CONTRACTS.md`; schema version `1.0.0`. Key: `AgentDecision` (only model output; no numeric trade fields),
`SignalCandidate` (fixed exits), `RiskVerdict` (bound to `intent_hash`, expiring), `OrderIntent` (`client_order_id`
persisted before send), `ApprovalTicket` (single use), `DecisionRecord` (one per cycle), `RunManifest` (per run).

## 6. Tool policy

`docs/WALLEX_TOOL_MAP.md`. 7 SAFE_READ, 12 PRIVATE_READ, 2 WRITE_LOW, 1 TRADE_EXECUTION, 15 DANGEROUS, 15 IRRELEVANT.
Decision agent: 3 public reads (SHADOW/LIVE), none in BACKTEST. Assistant: 6 public reads. Private reads: account
adapter only. Writes: execution engine, LIVE only, REST by default.

## 7. Unresolved decisions

| # | Decision | Default until resolved | Who |
|---|---|---|---|
| D1 | LLM provider/model | benchmark `anthropic:claude-sonnet-5-5` first; must work from the run host | owner + Phase 03 benchmark |
| D2 | Risk limit values (ADR-002 §7) | none — LIVE refuses to start | owner, from backtest/shadow |
| D3 | Bar interval for C1 (1m/5m/15m from 1m) | test 5m and 15m within trial budget | Phase 03 study |
| D4 | Universe (1–2 TMN symbols) | BTCTMN, ETHTMN candidates, pending spread/depth | Phase 03 data |
| D5 | Whether C2 (mean reversion) is built | no, unless time and evidence allow | Phase 03 gate |
| D6 | Enable `EXIT_POSITION` AI action | off | after ablation |
| D7 | AI breaker fallback to baseline | off (entries WAIT) | owner |
| D8 | Live write transport REST vs MCP | REST | organiser answer (U04) |

## 8. Assumptions

- Python host with outbound HTTPS to `api.wallex.ir` and to the LLM provider (U11 unverified).
- Challenge key is a credit sub-account key without withdrawal permission (Wallex docs; U05 unverified).
- 1m candles via REST are reliable enough to aggregate; history depth sufficient for a small study (U08 unverified).
- Market orders enabled on the chosen symbols at run time (snapshot only).
- Official cutoff will be available before LIVE (U01).

## 9. Blockers

| Blocks | Item |
|---|---|
| Nothing in BACKTEST/SHADOW | — (all public) |
| LLM agent runs | provider access + key on the run host (owner sets it; never in chat) |
| LIVE (any mode) | U01 cutoff, U05 key scope check, U06 fees, owner-approved limits, shadow gate passed |
| LIVE + AUTONOMOUS | U04 organiser answer on MCP/automatic confirmation |
| Realized-profit accounting edge cases | U02 dust/USDT, U10 platform auto-sell timing |

## 10. Recommended Phase 03 order

Plan with interfaces and tests: `docs/superpowers/plans/2026-10-22-phase-03-trading-kernel.md`.

1. Config + hashing → 2. market adapter/QC/snapshot/universe → 3. features + B0/C1 → 4. ledger + sizing + risk
→ 5. execution state machine, simulators, exits, reconciliation → 6. journal/manifest/replay store
→ 7. orchestrator + BACKTEST end-to-end with B0 → 8. LLM agent + validator + gateway + replay + model benchmark
→ 9. approvals + CLI + assistant → 10. backtest study (B0 vs A1 ablation, stress, locked test) + SHADOW with failure
fixtures → 11. LIVE adapter and gate, only on explicit owner instruction.

If less than a day of development remains: skip C2, D6, assistant extras; keep 1–8 + shadow + replay + demo.
Do not trade time for tests, shadow evidence or the demo video.
