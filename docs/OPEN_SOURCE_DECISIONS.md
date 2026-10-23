# Open-source decisions

What we took from other projects, what we rejected, and why — in plain terms. Inputs:
`research/phase-01/OPEN_SOURCE_REVIEW_FA.md` (static review at pinned SHAs, licenses in
`research/phase-01/evidence/*_LICENSE.txt`) and `research/phase-02/AGENT_FRAMEWORK_RESEARCH_FA.md`.

**No upstream source code has been copied into this repository.** Everything below is either a dependency
(installed, pinned, license recorded) or a *reimplemented concept* (an idea re-expressed in our own code and words).
If code is ever adapted, it is listed here with file, commit, license and the notice obligations — before merge.

Decision vocabulary: **dependency** (installed package) · **adaptation** (modified upstream code; license
obligations apply) · **reimplementation of concept** (our code, inspired by a documented behaviour) · **reference only**
(read to understand, nothing taken).

## 1. Runtime dependencies

| Package | Version | License | Why | Notes |
|---|---|---|---|---|
| pydantic | ≥2.14,<3 (2.14.1 locked) | MIT | Contracts, validation | Core |
| pydantic-ai-slim[mcp] | **==2.59.0** | MIT | Bounded agent loop, MCP client, model-free test models (ADR-001) | Pulls fastmcp-slim 4.1.3 (Apache-2.0), mcp 2.3.0 (MIT), httpx2 (BSD-3), opentelemetry-api (Apache-2.0). Apache-2.0 deps: keep their NOTICE files if we redistribute vendored copies (we don't vendor) |
| pytest, pyright (dev) | 9.1.1, 1.1.414 | MIT | Tests, strict type checking | Dev only |

License check rule: before adding any dependency, record its license here; GPL/AGPL/LGPL and "source-available"
licenses (e.g. Commons Clause) need an explicit owner decision first.

## 2. Trading/agent projects reviewed

| Project (license) | Useful idea | Decision | What we rejected and why |
|---|---|---|---|
| **Freqtrade** (GPL-3.0) | Protections: stop entries after repeated losses, cooldown after exits, max-drawdown pause; look-ahead/recursive analysis to catch future leakage in backtests | **Reimplement concept** in `risk/` (loss-streak cooldown, drawdown pause) and in backtest QC (features only from closed bars, next-bar fills) | Code (GPL would constrain our licensing); its MaxDrawdown reads **closed** trades only — we need live equity incl. open positions; its default backtest fills without slippage — we add stress; no Wallex connector |
| **Hummingbot** (Apache-2.0) | Triple-barrier exits (stop, take-profit, time limit, trailing) per position; tracking real filled amount via in-flight orders | **Reimplement concept**: each `SignalCandidate` fixes stop/TP/time; exit coordinator owns exits | Its executor protects only after **full** fill (we protect partial fills immediately); its close uses MARKET (some Wallex markets have Market disabled); full connector framework too heavy |
| **NautilusTrader** (LGPL-3.0) | Independent pre-trade risk engine before execution; reservations; startup/in-flight/continuous **reconciliation** after restart or lost responses; fixed-risk sizing from entry/stop/equity | **Reimplement concept**: `RiskEngine` gate, reservations, UNKNOWN→reconcile, sizing formula with our fees/precision | Whole platform (Rust core, typed instruments, adapter effort) would consume the time needed for shadow tests and demo; LGPL |
| **Condor** (MIT) | AI separated from deterministic execution; risk callback on every tool mutation; bounded tools; decision journal + snapshots; soft pause vs hard shutdown on drawdown; test that two actions in one tick can't double-spend exposure | **Reimplement concept**: Tool Gateway default deny, ADR-002 path, Decision Journal, pause vs kill latch, aggregation test in Phase 03 | Fleet/Telegram/dashboard deployment and Hummingbot API dependency; its risk defaults are disabled (`-1`), so no defaults were taken |
| **Jesse** (MIT) | Small strategy interface (`should_long`, `go_long`, `update_position`); risk-to-quantity sizing helper | **Reference only** for our `strategies` interface shape | Not needed as a dependency; no Wallex connector; its MCP is not Wallex MCP |
| **FinRL / FinRL-Trading** (MIT / Apache-2.0) | Clear layering: data → signal → risk overlay → execution intent | **Reference only** | RL training needs a realistic simulator and data we do not have; equity/weekly horizons; Alpaca-specific execution |
| **vectorbt** (Apache-2.0 **+ Commons Clause**) | Very fast parameter sweeps | **Rejected as dependency** | Not plain open source (Commons Clause); sweeping thousands of parameters is an overfitting risk for a 2-family, budgeted trial plan |
| **backtesting.py** (AGPL-3.0) | Simple bar broker | **Reference only** | AGPL; no live/reconciliation |
| **ccxt** (MIT) | Unified exchange API | **Reference only** | No Wallex exchange in the reviewed tree; we talk to Wallex REST/MCP directly |
| **LangGraph / LangChain** (MIT) | Durable interrupts, explicit graphs, HITL middleware | **Rejected for v1** (ADR-001) | Resume re-runs the interrupted node; checkpoints are not trade truth; extra layers for a mostly linear cycle |
| **OpenAI Agents SDK** (MIT) | Runner, MCP filters, RunState approvals, scripted models | **Fallback option** (ADR-001) | Provider undecided; sensitive-data tracing defaults need care; no capability gap vs. PydanticAI for our use |
| **mhsenpc/wallex_mcp_server** (third-party) | — | **Rejected** | Official Wallex MCP exists; third-party wrappers are not a source of truth |

### Restart recovery (2026-10-22, branch `phase-03/restart-recovery`, ADR-003)

No new dependency and no upstream code. **Reimplemented concept** from NautilusTrader's documented live
reconciliation (cache restored from a database, then aligned with venue reports by client order id before trading;
ambiguous history may recover order state but must not invent position economics; unresolved submissions are kept
and listed) — checked against its public "Live trading" concept docs on 2026-10-22, not against source code.
Hummingbot in-flight order tracking and Freqtrade's database-held open trades refreshed from the exchange at startup
remain **reference only** (concepts already listed above). Ours differs deliberately: an event-sourced, hash-chained
log replayed through our own ledger; simulated venues settle from the journal; a not-found order is never declared
absent automatically.

## 3. Ideas re-expressed in our design (summary)

1. Independent risk gate before every execution, with reservations (Nautilus, Condor).
2. Reconciliation as a first-class state: lost response = UNKNOWN, resolve by client id, never blind resubmit (Nautilus).
3. Triple-barrier exits fixed at entry; protect partial fills at once (Hummingbot, corrected).
4. Loss-streak cooldown, drawdown pause on **live equity** (Freqtrade, corrected).
5. No-look-ahead discipline and next-bar fills in backtests (Freqtrade docs).
6. AI limited to bounded tools; every mutation passes a deterministic gate; journal everything (Condor).

All parameters (windows, thresholds, cooldowns) are **ours to tune against Wallex data**; none were taken from
these projects' defaults.

## 4. Phase 03 real-AI evidence continuation (2026-10-22)

Scope: NineRouter agent/evaluation/SHADOW evidence only. No risk, ledger, recovery or strategy migration.

| Capability / source | Exact source and license checked | Decision |
|---|---|---|
| Structured decisions, tool loop, budgets, error message capture, OpenAI-compatible provider | PydanticAI 2.59.0 installed; upstream `7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9`, MIT LICENSE rechecked | **Existing dependency / supported API use.** Reuse Agent, UsageLimits, OpenAIProvider, FunctionModel and capture_run_messages; no custom model client or generic agent loop. |
| Evaluation datasets, deterministic scorers, traces and execution | MLflow 3.8.1, tag commit `4cc9d5bd7cd1962f7f34017e8d8f133f89ad8d69`, Apache-2.0 LICENSE.txt verified before installation | **Optional evaluation dependency**, separate from trading runtime. Native create_dataset / scorer / genai.evaluate; no custom evaluation framework. No vendor source copied; retain dependency notices if redistributing packages. |
| Agent supervision, model/tool gating and decision audit | Condor `8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c`, MIT LICENSE rechecked; `condor/agents/engine.py` imports PydanticAIClient and its own ACP/runtime/telemetry | **Reference only in this task.** Our generic model loop is already the same upstream dependency; importing Condor would add an unrelated trading/fleet runtime. No rewrite of the existing bounded-wait wrapper. |
| Strategy and trade lifecycle API | Jesse `4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae`, MIT LICENSE rechecked; `jesse/strategies/Strategy.py` inspected | **Reference only.** Its strategy/broker/global-store coupling does not supply a NineRouter or evaluation helper; no strategy or sizing code imported. |

MLflow is explicitly requested for this evaluation task, not a new trading framework. Dataset size is deliberately
small under the owner's capacity/time constraint; results are operational evidence, not a statistical model ranking.
SQLAlchemy is constrained to `>=2.0,<2.1` in the evaluation group (2.0.55, MIT; upstream tag `rel_2_0_55` LICENSE
checked): MLflow 3.8.1 imports `FallbackAsyncAdaptedQueuePool`, removed in the initially resolved SQLAlchemy 2.1.
MLflow 3.8.1 explicitly refuses local registration of decorator scorers; follow its supported source-controlled
`@scorer` + `genai.evaluate` path, preserve registered scorer discovery, and never bypass that security restriction.
Tracing must expose only public context, concise AgentDecision and audited tool data; no HTTP headers, credentials
or private model reasoning may be captured. MLflow's broad PydanticAI autologger is not enabled because it patches
model request/stream internals; use its native application-boundary tracing API with explicit safe inputs/outputs.
Use `MLFLOW_DISABLE_TELEMETRY=true` **before** importing MLflow 3.8.1; the initially used ENABLE flag is not
supported. A real public probe exposed telemetry/import-hook lock contention; the setting prevents background
network in scripted evaluations. Native local `genai.evaluate` exports traces synchronously, so no additional
async-queue flush is attempted. PydanticAI owns its OpenAIProvider HTTP client and refreshes it across evaluation
worker loops; passing a separately owned AsyncOpenAI client would disable that supported lifecycle.

User-provided `research/phase-03/external/muse/` documents are reference evidence. Their proposed all-in sizing,
numeric strategy defaults, inferred trading hours and absence-of-MCP claims do not supersede AGENTS/accepted ADRs.

## 5. Phase 04 experiment harness preparation (2026-10-22)

- **Existing dependency reuse:** MLflow **3.8.1**, Apache-2.0, exact source commit
  `4cc9d5bd7cd1962f7f34017e8d8f133f89ad8d69` (license already verified in §4). Inspected
  `mlflow/tracking/client.py` (`create_run`, `log_param`, `log_metric`, `log_dict`). Use its native manual tracking
  client in `experiments/tracking.py`; exact financial JSON remains authoritative, display metrics use floats only
  at the MLflow boundary. Optional existing `evaluation` group; explicit local SQLite/artifact URI, telemetry off,
  no autolog or model calls. No upstream code copied and no dependency/lockfile change.
- **Reference only:** Jesse MIT `4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae`, license in Phase 01 evidence,
  `jesse/modes/optimize_mode/__init__.py`: optimization couples its candle loader, global store, router and
  OptimizationSession. This is not a small reusable runner for our Wallex kernel. No code/defaults imported.
- **Reference only:** Freqtrade GPL-3.0 `c051e9065c5b677f03817dd1f83cb980af1ed684`, license in Phase 01 evidence,
  `docs/hyperopt.md`: parameter search and backtest-cost concepts reviewed; no GPL source/dependency introduced.
  The project's existing `scripts/backtest_study.py` already demonstrates independent kernel calls. The new layer
  reuses `run_backtest`, RunConfig validation, canonical hashes, ledger summaries and journals; stdlib
  `itertools.product` supplies the bounded grid without Jesse/Freqtrade/vectorbt or an optimizer framework.
- Domino skill used as **tracking guidance only** (unique project/experiment/protocol name, manual non-secret
  parameters/metrics/artifacts). No Domino server, connector, credentials, copied snippets or model registry.

Pinned references: [Jesse](https://github.com/jesse-ai/jesse/blob/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae/jesse/modes/optimize_mode/__init__.py),
[Freqtrade](https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/hyperopt.md),
[MLflow client](https://github.com/mlflow/mlflow/blob/4cc9d5bd7cd1962f7f34017e8d8f133f89ad8d69/mlflow/tracking/client.py).

## 6. Phase 04 news infrastructure (2026-10-22)

| Concrete use | Exact evidence / permission | Decision |
|---|---|---|
| `cryptocurrency.cv` hosted `/api/news` public JSON | `nirholas/cryptocurrency.cv` e9a07f459b30ef3b2f7b91719dba2f2d22026550: LICENSE proprietary/all rights reserved (sha256 62c58e9060fcc9b94fda7756793d4170aa320fb8a578cb90cca61f0358c55e7d); README explicitly permits hosted API use and describes fair use | **Hosted API use only** in `news/providers.py`; independently implemented JSON adapter. No SDK, copied snippet, self-hosted service or upstream numerical default. Article content rights are separate; no full articles fetched. |
| Federal Reserve and Bitcoin Core public feeds; GDELT DOC metadata | Official feed catalogs/terms in `research/phase-04/NEWS_SOURCE_POLICY.md`; Bitcoin Core website bd39e3326742349776243c3aadb8975a03b9def2 LICENSE MIT; GDELT attribution/link required | **Public data interfaces**, independent RSS/Atom/JSON adapters. Preserve source attribution, local raw hashes and original dates. GDELT seen time is not publication. CoinDesk collection denied under its current automated-access terms. |
| Normalization, contracts, hashing, XML/HTML/JSON parsing and snapshots | Existing project Contract/UtcDatetime/content_id, Python standard library | **Reuse existing dependencies/standard library**. No new package, copied source, trading framework or ML model. No strategy or risk defaults taken from external projects. |

The separate algorithm registry is **reference only**, not a decision to import any framework or strategy. GPL/LGPL,
unclear per-file licenses, model-weight/data licenses and third-party services still require the owner's applicable
license decision before future code reuse. No such restricted code enters this branch.

## 7. Phase 04 strategy round 2 (2026-10-23)

| Mechanism | Exact source inspected | License | Classification / what we did |
|---|---|---|---|
| Donchian breakout (F1) | Jesse `jesse/indicators/donchian.py` @ `9a3ebe075d9d0af5f012f921f46c2286f234af68` (sha256 b7e96ebe…); LEAN `Indicators/DonchianChannel.cs` @ `f3d841e60dfd010ee5fc293ea893e56952484108` (sha256 89c2e61e…) | MIT / Apache-2.0 | **Reimplementation of concept**. Both include the current bar's high in the upper band; a breakout must therefore compare with the PRIOR window (our `prior_high` excludes the current bar). No code copied; lookbacks chosen from day multiples (24/72 bars), not Turtle defaults. |
| Kaufman efficiency ratio (regime-r2) | Jesse `jesse/indicators/er.py` @ `9a3ebe07…` (sha256 223debf7…); LEAN `Indicators/KaufmanEfficiencyRatio.cs` @ `f3d841e6…` (sha256 ac55b30c…) | MIT / Apache-2.0 | **Reimplementation of concept**: same formula; ER/volatility thresholds are percentile ranks against our own trailing history, not upstream defaults. |
| Bollinger width squeeze (F3) | Jesse `bollinger_bands_width.py` @ `9a3ebe07…` (sha256 d4103c43…, Rust-backed); LEAN `Indicators/BollingerBands.cs` @ `f3d841e6…` (sha256 4a13b983…) | MIT / Apache-2.0 | **Reference only**: family rejected by the economics gate; nothing entered the kernel. |
| Ratchet trailing barrier (F2 chandelier) | Hummingbot `strategy_v2/executors/position_executor/position_executor.py` `control_trailing_stop` @ `9af100d6822da7d2d0291a906c730ef172284ee2` (sha256 09aef309…) | Apache-2.0 | **Reimplementation of concept**: Hummingbot ratchets a PnL-% trigger after an activation price; we ratchet an ATR-scaled price stop from entry, never below the risk-approved stop, inside our own exit coordinator. No code copied. |
| Strategy templates (MultiMa etc.) | Freqtrade / freqtrade-strategies (registry pins) | GPL-3.0 | **Reference only**; not opened for code, no GPL source entered. |

No new dependency, no framework import, no upstream numeric default adopted. Source bytes were fetched once into a
scratch directory for inspection (hashes above) and are not part of the repository.

## 8. Phase 05 final pre-LIVE: weak-part survey (2026-10-23, owner request)

The owner asked to strengthen weak parts with ideas from well-known trading-bot repositories. Measured weak parts:
(1) 72h return of frozen F2 (median 0, 36.7% no-trade windows, top 10% of trades = 103% of profit);
(2) the AI decision role (decision_v2 judged F2 with C1 criteria). Licenses checked on 2026-10-23: Freqtrade
`LICENSE` = GPL-3.0, Hummingbot `LICENSE` = Apache-2.0, TradingAgents = Apache-2.0 (README/LICENSE).

| Weak part | Source / license | Decision |
|---|---|---|
| Few F2 opportunities in 72h; two markets only | Freqtrade pairlist chain: VolumePairList, AgeFilter, SpreadFilter, RangeStabilityFilter, PrecisionFilter (documentation `freqtrade.io/en/stable/plugins/`); GPL-3.0 | **Reimplementation of concept** in `universe/screener.py` and `wallex-agent screen`. No code, no defaults. Thresholds come from our config: volume ≥ max position / 1%; age ≥ F2 warm-up; spread ≤ `max_spread_bps`; 24h range ≥ 1.5 × round-trip cost and ≤ 25%; price step ≤ 10 bps. Public data, proposal only: owner confirmation, `[run].symbols` and a per-symbol backtest are still required. |
| AI role | TauricResearch/TradingAgents (analyst team, bull/bear debate, trader, risk team); Apache-2.0 | **Reference only.** Its trader LLM decides timing and size and its agents spawn agents, both against AGENTS §4. The bounded VETO role (ADR-005) was built instead. A bull/bear field would change the frozen prompt and needs a fresh evaluation. |
| 72h exit horizon | Hummingbot PositionExecutor trailing stop with activation price (already reimplemented as our chandelier, §7); Apache-2.0 | **Deferred.** An activation-gated, tighter trail touches `portfolio/`/exit coordination (safety boundary: tests first, reviewer). F2's profit comes from rare long trends that a tighter exit would cut. Backtest before any change. |
| New algorithm family | Hummingbot pure market making / Avellaneda–Stoikov; Apache-2.0 | **Rejected for this challenge.** It needs a maker fee far below the 0.25%/side assumption (account fee unknown, U06), many resting orders and inventory control. Our execution is single entry/exit. |
| Re-sourcing strategies | Freqtrade-strategies (GPL-3.0), Jesse examples (MIT), vectorbt (Commons Clause), backtesting.py (AGPL), backtrader (GPL) | **Not used.** Breakout, momentum, squeeze and range mean-reversion were already sourced and tested in Phase 04 (§7). Only 4h trend families survive Wallex costs. GPL/AGPL/Commons-Clause code would also need an owner license decision. |

Nothing from these projects was copied. No dependency was added.
