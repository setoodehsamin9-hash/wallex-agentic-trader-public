# Handoff to Phase 04

Date: 2026-10-22 · Branch **`phase-03/integrated-kernel`** (Codex `phase-03/trading-kernel` @ `671f661` + Claude
`phase-03/restart-recovery` @ `7d04d9d`, integrated; not merged to `main`) · Integration summary:
`INTEGRATED_PHASE03_REPORT_FA.md`. Phase 03 status: **PARTIAL** (kernel, agent, recovery and SHADOW path are built
and tested; LIVE stays disabled; no strategy is profitable yet).

## Phase 04 scope (binding)

**Phase 04 = Claude-led Strategy / Market / Parameter Design and evidence-driven profitability improvement.**
The kernel (risk, execution, ledger, recovery, agent boundary) is treated as stable infrastructure: change it only
for a concrete defect, with tests first and a safety review (AGENTS.md §10). Strategy work happens in `strategies/`,
`features/`, config and backtest/shadow evidence — never by loosening risk, policy or validation.

Inputs: `research/phase-03/external/muse/` (market, strategy, risk-execution research — evidence, not specification;
AGENTS/ADR-002 win on conflict), `reports/phase-03/BACKTEST_REPORT.md`, `reports/phase-03/ai/PAIRED_BACKTEST.json`.

Recommended order:
1. Read the real account fee/precision (owner-run, read-only) or keep the 0.25%/side stress; fees dominate today.
2. Market selection: which TMN pairs have spread/depth/volatility that can beat round-trip cost (Muse market research).
3. Pre-registered strategy rounds on FRESH forward data (not the already-seen TEST split): e.g. larger minimum edge
   over cost, longer bars, fewer trades, alternative families. Record every trial (TRIALS.jsonl), time-ordered splits.
4. Parameter search kept minimal (Muse plan) with overfitting guards; AI-vs-B0 ablation on the same windows.
5. Longer public SHADOW forward runs with candidates (now resumable after crashes: `shadow --resume RUN_ID`).

## 1. What exists now

| Area | State | Key files |
|---|---|---|
| Config + calendar | Versioned TOML; fail-closed limits; LIVE refuses without window, competition end, U04, owner approval | `runtime/config.py`, `runtime/calendar.py` |
| Market data | Public REST (read-only), parsing/QC, eligible universe, hash-pinned dataset | `market/*`, `data/datasets/*` |
| Strategy | C1 breakout (cost-aware target) + B0 deterministic selector | `strategies/c1_breakout.py`, `agent/baseline.py` |
| Risk | Sole producer of intents; sizing, caps, protections, kill latch, recheck after approval | `risk/*`, `portfolio/ledger.py` |
| Execution | Verdict binding, duplicate refusal, persist-before-send, UNKNOWN → reconcile, exits, BACKTEST/SHADOW simulators | `execution/*` |
| **Restart recovery** | Hash-chained state log, deterministic rebuild, coordinator READY/EXITS_ONLY/BLOCKED, approvals invalidated, no resubmit, single exit; `recover`, `shadow --resume`, `abandon` | `recovery/*`, ADR-003 |
| Agent | NineRouter bounded PydanticAI agent (read tools only, structured output, budgets, deadline), validator, recording, replay; MLflow eval | `agent/*` |
| LIVE adapter | REST + account reader + preflight — **fakes only**, PROVISIONAL mappings; not composable | `execution/wallex_live.py`, `runtime/preflight.py` |

## 2. Evidence

- NineRouter (`gemini-3.8-flash`): 15/15 valid structured decisions in the final run, zero unauthorized candidates,
  zero write tools offered; tool calls failed only on an invented `reason` argument (addressed by instruction in
  prompt `decision_v2`, now default; `decision_v1` frozen as evaluated; validation unchanged; re-measure with a real
  run: `uv run pytest -m ninerouter`). AI-vs-B0 on seen validation data: both negative.
- SHADOW public runs: zero real writes; 8 cycles had no candidate.
- Integrated candidate path (synthetic scenario): candidate → VALID → risk APPROVE → SHADOW fill → journal/state log
  → replay identical, for B0 and a SCRIPTED model through the real agent wrapper
  (`ai/INTEGRATED_CANDIDATE_PATH.json`). The real-model variant is opt-in (`pytest -m ninerouter`).
- Recovery: 40 scenario tests (crash points, UNKNOWN, double-sell, approvals, corruption, SHADOW resume).

## 3. Blockers before LIVE (unchanged in kind)

1. Owner trading windows + IANA timezone; official competition clock (U01); U04 answer.
2. Challenge-account key on the owner's machine → read-only private spike → fixtures → finalize PROVISIONAL
   mappings; a verified private `query(client_order_id)` + balance source for LIVE recovery.
3. Owner-approved limits (`approved_by_owner = true`), clean tagged commit, kill-switch drill.
4. A strategy with positive net expectancy on fresh data (Phase 04).

## 4. Known limitations

- SHADOW fills: IOC on visible depth; no queue/latency model. Backtest has no historical book.
- Exit escalation for unfilled LIVE exits is minimal.
- Recovery: unkeyed hash chain (not tamper-proof against a full rewrite); a long-UNKNOWN LIVE order is never declared
  absent automatically (owner decision pending); equity-curve history is not restored after a restart.
- `EXIT_POSITION` AI action implemented but disabled by default.
